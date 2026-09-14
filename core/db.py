# -*- coding: utf-8 -*-
"""
数据层：SQLite + FTS5(trigram) 全文索引。

设计要点
--------
1. 用 FTS5 的 trigram 分词器处理中文。trigram 按三字滑动窗口切分，
   中文子串检索表现好，但要求查询词 >= 3 个字符；短词（如"发票"）由
   db.search_documents 自动回退到 LIKE 检索，两条路结果合并去重。
2. FTS 使用 external content 表（content='documents'），靠触发器与
   主表保持一致，避免内容重复存储占用内存与磁盘。
"""
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL DEFAULT '',
    original_name   TEXT NOT NULL DEFAULT '',
    stored_name     TEXT NOT NULL DEFAULT '',
    mime            TEXT NOT NULL DEFAULT '',
    ext             TEXT NOT NULL DEFAULT '',
    file_size       INTEGER NOT NULL DEFAULT 0,
    page_count      INTEGER NOT NULL DEFAULT 1,
    content         TEXT NOT NULL DEFAULT '',
    content_source  TEXT NOT NULL DEFAULT '',   -- text-layer / ocr / mixed
    ocr_chars       INTEGER NOT NULL DEFAULT 0,
    correspondent_id INTEGER,
    doctype_id      INTEGER,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending/processing/done/failed
    error           TEXT NOT NULL DEFAULT '',
    progress        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    processed_at    TEXT,
    owner           TEXT NOT NULL DEFAULT '',        -- 上传者用户名（多用户权限用）
    visibility      TEXT NOT NULL DEFAULT 'readonly' -- private / readonly / public_edit
);

CREATE TABLE IF NOT EXISTS tags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL UNIQUE,
    color    TEXT NOT NULL DEFAULT '#7c9cff',
    is_auto  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS document_tags (
    doc_id   INTEGER NOT NULL,
    tag_id   INTEGER NOT NULL,
    source   TEXT NOT NULL DEFAULT 'manual',   -- manual / auto
    rule_id  INTEGER,
    PRIMARY KEY (doc_id, tag_id)
);

CREATE TABLE IF NOT EXISTS tag_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id         INTEGER NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    field          TEXT NOT NULL DEFAULT 'any',          -- any/title/content/correspondent
    match_type     TEXT NOT NULL DEFAULT 'any',          -- any/all/regex/exact/fulltext
    pattern        TEXT NOT NULL DEFAULT '',
    case_sensitive INTEGER NOT NULL DEFAULT 0,
    enabled        INTEGER NOT NULL DEFAULT 1,
    hit_count      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS correspondents (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document_types (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_doc_status  ON documents(status);
CREATE INDEX IF NOT EXISTS idx_doc_created ON documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dt_tag      ON document_tags(tag_id);
-- 注意：owner 索引不能写在这里 —— 老库升级时 owner 列是后面 ALTER 补上的，
--      在这里建索引会报 "no such column: owner"。改到 _init_schema 末尾建。

-- 全文索引（外部内容表）
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title,
    content,
    content='documents',
    content_rowid='id',
    tokenize='trigram'
);

-- 与主表同步的触发器
CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON documents BEGIN
    INSERT INTO docs_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON documents BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, content)
        VALUES ('delete', old.id, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON documents BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, content)
        VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO docs_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
"""

DEFAULT_RULES = [
    # (标签名, 颜色, 字段名, 匹配类型, 模式串)
    # 首次运行播种的示例规则 —— 命中「任何关键词之一」即自动打标签。
    # 字段 any=标题+正文；匹配类型 any=按空格分词做 OR 命中。
    # 上线后可在「标签规则」面板里改、删，或按自己的文档体系新增。
    ("发票", "#f0883e", "any", "any", "增值税专用发票 发票 开票 价税合计"),
    ("收据", "#3fb950", "any", "any", "收款收据 收据 交款单位 收款事由"),
    ("合同", "#a371f7", "any", "any", "劳动合同 采购合同 合同书 甲方 乙方 需方 供方"),
    ("报告", "#58a6ff", "any", "any", "检测报告 检验报告 评估报告 报告编号"),
    ("报销", "#db6d28", "content", "any", "报销 报销单 差旅费 费用明细"),
    ("对账", "#39c5cf", "content", "any", "对账单 对账 结算单 应收账款"),
]


class Database:
    """
    SQLite 封装。

    线程模型：Flask 开发服务器是多线程的，前端又会并发发 3 个请求
    （documents / tags / meta）。此前所有线程共用一个 Connection，
    曾偶发「同一个 Cursor 混用」导致返回行退化成 tuple，
    进而在 doc_to_dict 里抛 IndexError（表现为 /api/documents 偶发 500）。
    这里改成**每线程一条连接**（thread-local），对外仍通过 db.conn 访问，
    调用方无感知；WAL 模式保证读写并发安全。
    """

    def __init__(self, db_path):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.path = db_path
        self._local = threading.local()
        self._local.conn = self._make_conn()   # 主线程（含建表/播种）先建一条
        self._init_schema()
        self._seed_rules()

    def _make_conn(self):
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @property
    def conn(self):
        """当前线程的连接（首次访问时懒建）。"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._make_conn()
            self._local.conn = conn
        return conn

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        # 轻量迁移：documents 表补充新列（重复文件检测 / 多用户权限用）
        # 老库没有这些列，用 ALTER TABLE 补上，不影响已有数据
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(documents)")]
        if "file_hash" not in cols:
            self.conn.execute("ALTER TABLE documents ADD COLUMN file_hash TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        if "owner" not in cols:
            self.conn.execute("ALTER TABLE documents ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        if "visibility" not in cols:
            self.conn.execute(
                "ALTER TABLE documents ADD COLUMN visibility TEXT NOT NULL DEFAULT 'readonly'")
            self.conn.commit()
        # owner 列确认存在后再建索引（老库升级场景）
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_owner ON documents(owner)")
        self.conn.commit()

    def adopt_orphan_docs(self, owner):
        """
        把没有归属人的老文档（owner 为空）划给指定用户。
        场景：升级到多用户版本前就存在的文档，由第一个账号（管理员）接管，
        避免变成谁都看不到的孤儿文件。
        """
        cur = self.conn.execute(
            "UPDATE documents SET owner=? WHERE owner='' OR owner IS NULL", (owner,))
        self.conn.commit()
        return cur.rowcount

    def _seed_rules(self):
        """首次启动时预置几条贴合票据/合同场景的自动标签规则。"""
        cur = self.conn.execute("SELECT COUNT(*) AS c FROM tag_rules")
        if cur.fetchone()["c"] > 0:
            return
        for name, color, field, mtype, pattern in DEFAULT_RULES:
            tag_id = self.get_or_create_tag(name, color, is_auto=1)
            self.conn.execute(
                "INSERT INTO tag_rules(tag_id, name, field, match_type, pattern)"
                " VALUES (?,?,?,?,?)",
                (tag_id, "自动识别%s" % name, field, mtype, pattern),
            )
        self.conn.commit()

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---------------- 文档 ----------------
    def add_document(self, title, original_name, stored_name, mime, ext, file_size,
                     file_hash="", owner="", visibility="readonly"):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        cur = self.conn.execute(
            "INSERT INTO documents(title, original_name, stored_name, mime, ext,"
            " file_size, file_hash, owner, visibility, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (title, original_name, stored_name, mime, ext, file_size,
             file_hash, owner, visibility, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_visibility(self, doc_id, visibility):
        """设置文档可见性：private / readonly / public_edit"""
        if visibility not in ("private", "readonly", "public_edit"):
            return False
        self.conn.execute("UPDATE documents SET visibility=? WHERE id=?", (visibility, doc_id))
        self.conn.commit()
        return True

    def find_doc_by_hash(self, file_hash):
        """按内容哈希查重，返回已有文档行或 None。"""
        if not file_hash:
            return None
        return self.conn.execute(
            "SELECT id, title, original_name FROM documents WHERE file_hash=? LIMIT 1",
            (file_hash,)).fetchone()

    def get_document(self, doc_id):
        return self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def update_document(self, doc_id, **fields):
        if not fields:
            return
        keys = ",".join("%s=?" % k for k in fields)
        vals = list(fields.values()) + [doc_id]
        self.conn.execute("UPDATE documents SET %s WHERE id=?" % keys, vals)
        self.conn.commit()

    def delete_document(self, doc_id):
        self.conn.execute("DELETE FROM document_tags WHERE doc_id=?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        self.conn.commit()

    # ---------------- 搜索 ----------------
    def search_documents(self, query="", tag_id=None, doctype_id=None,
                         correspondent_id=None, limit=500, viewer=None,
                         viewer_admin=False):
        """
        全文搜索 + 标签筛选 + 权限过滤。
        中文短词（<3 字符）trigram 无法命中，自动回退 LIKE，结果合并去重。

        viewer：当前登录用户名。给了 viewer 就会自动隐藏「别人的私密文档」
                （管理员除外，管理员看得到全部）。
        """
        query = (query or "").strip()
        where, params = [], []

        if tag_id:
            where.append("d.id IN (SELECT doc_id FROM document_tags WHERE tag_id=?)")
            params.append(tag_id)
        if doctype_id:
            where.append("d.doctype_id=?")
            params.append(doctype_id)
        if correspondent_id:
            where.append("d.correspondent_id=?")
            params.append(correspondent_id)

        # 权限过滤：别人的「私密」文档不出现在列表里
        if viewer and not viewer_admin:
            where.append("(d.visibility <> 'private' OR d.owner = ?)")
            params.append(viewer)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        if not query:
            sql = ("SELECT d.* FROM documents d %s ORDER BY d.created_at DESC LIMIT ?"
                   % where_sql)
            return self.conn.execute(sql, params + [limit]).fetchall()

        hit_ids = {}
        # 路径一：FTS5 trigram（查询词 >= 3 字符才有意义）
        if len(query) >= 3:
            fts_where = where_sql + (" AND " if where_sql else "WHERE ")
            fts_where += "docs_fts MATCH ?"
            try:
                sql = ("SELECT d.*, bm25(docs_fts, 10.0, 1.0) AS score"
                       " FROM documents d JOIN docs_fts ON docs_fts.rowid = d.id"
                       " %s ORDER BY score LIMIT ?" % fts_where)
                for row in self.conn.execute(sql, params + [self._fts_query(query), limit]):
                    hit_ids[row["id"]] = row
            except sqlite3.Error:
                pass  # trigram 解析失败时静默回退到 LIKE

        # 路径二：LIKE 子串（兜底，也覆盖 2 字中文词）
        like = "%%%s%%" % query
        like_where = where_sql + (" AND " if where_sql else "WHERE ")
        like_where += "(d.title LIKE ? OR d.content LIKE ? OR d.original_name LIKE ?)"
        sql = ("SELECT d.*, 0 AS score FROM documents d %s ORDER BY d.created_at DESC LIMIT ?"
               % like_where)
        for row in self.conn.execute(sql, params + [like, like, like, limit]):
            if row["id"] not in hit_ids:
                hit_ids[row["id"]] = row

        rows = list(hit_ids.values())
        rows.sort(key=lambda r: (r["score"] if r["score"] else 99.0, -r["id"]))
        return rows[:limit]

    @staticmethod
    def _fts_query(q):
        """把用户输入转成安全的 FTS5 表达式：按空格切成多个短语，用 AND 连接。"""
        import re
        terms = [t for t in re.split(r"\s+", q.strip()) if t]
        out = []
        for t in terms:
            t = t.replace('"', "")
            if not t:
                continue
            out.append('"%s"' % t)
        return " AND ".join(out) if out else '""'

    # ---------------- 标签 ----------------
    def tag_exists(self, name):
        name = (name or "").strip()
        if not name:
            return None
        row = self.conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        return row["id"] if row else None

    def get_or_create_tag(self, name, color="#7c9cff", is_auto=0):
        name = (name or "").strip()
        if not name:
            return None
        row = self.conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO tags(name, color, is_auto) VALUES (?,?,?)", (name, color, is_auto))
        self.conn.commit()
        return cur.lastrowid

    def list_tags(self):
        sql = """
        SELECT t.id, t.name, t.color, t.is_auto,
               (SELECT COUNT(*) FROM document_tags dt WHERE dt.tag_id=t.id) AS doc_count,
               (SELECT COUNT(*) FROM tag_rules r WHERE r.tag_id=t.id) AS rule_count
        FROM tags t ORDER BY doc_count DESC, t.name
        """
        return self.conn.execute(sql).fetchall()

    def get_doc_tags(self, doc_id):
        sql = """
        SELECT t.id, t.name, t.color, dt.source, dt.rule_id
        FROM document_tags dt JOIN tags t ON t.id=dt.tag_id
        WHERE dt.doc_id=? ORDER BY t.name
        """
        return self.conn.execute(sql, (doc_id,)).fetchall()

    def add_doc_tag(self, doc_id, tag_id, source="manual", rule_id=None):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO document_tags(doc_id, tag_id, source, rule_id)"
                " VALUES (?,?,?,?)", (doc_id, tag_id, source, rule_id))
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def remove_doc_tag(self, doc_id, tag_id):
        self.conn.execute(
            "DELETE FROM document_tags WHERE doc_id=? AND tag_id=?", (doc_id, tag_id))
        self.conn.commit()

    def delete_tag(self, tag_id):
        self.conn.execute("DELETE FROM document_tags WHERE tag_id=?", (tag_id,))
        self.conn.execute("DELETE FROM tag_rules WHERE tag_id=?", (tag_id,))
        self.conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
        self.conn.commit()

    # ---------------- 规则 ----------------
    def list_rules(self):
        sql = """
        SELECT r.*, t.name AS tag_name, t.color AS tag_color
        FROM tag_rules r JOIN tags t ON t.id=r.tag_id
        ORDER BY r.id
        """
        return self.conn.execute(sql).fetchall()

    def add_rule(self, tag_id, field, match_type, pattern, name="", case_sensitive=0):
        cur = self.conn.execute(
            "INSERT INTO tag_rules(tag_id, name, field, match_type, pattern, case_sensitive)"
            " VALUES (?,?,?,?,?,?)",
            (tag_id, name or "自定义规则", field, match_type, pattern, case_sensitive))
        self.conn.commit()
        return cur.lastrowid

    def update_rule(self, rule_id, **fields):
        if not fields:
            return
        keys = ",".join("%s=?" % k for k in fields)
        self.conn.execute("UPDATE tag_rules SET %s WHERE id=?" % keys,
                          list(fields.values()) + [rule_id])
        self.conn.commit()

    def delete_rule(self, rule_id):
        self.conn.execute("DELETE FROM tag_rules WHERE id=?", (rule_id,))
        self.conn.commit()

    def bump_rule_hits(self, rule_id):
        self.conn.execute(
            "UPDATE tag_rules SET hit_count=hit_count+1 WHERE id=?", (rule_id,))
        self.conn.commit()

    # ---------------- 往来单位 / 文档类型 ----------------
    def get_or_create_correspondent(self, name):
        name = (name or "").strip()
        if not name:
            return None
        row = self.conn.execute(
            "SELECT id FROM correspondents WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO correspondents(name) VALUES (?)", (name,))
        self.conn.commit()
        return cur.lastrowid

    def get_or_create_doctype(self, name):
        name = (name or "").strip()
        if not name:
            return None
        row = self.conn.execute(
            "SELECT id FROM document_types WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO document_types(name) VALUES (?)", (name,))
        self.conn.commit()
        return cur.lastrowid

    def list_correspondents(self):
        return self.conn.execute("SELECT * FROM correspondents ORDER BY name").fetchall()

    def list_doctypes(self):
        return self.conn.execute("SELECT * FROM document_types ORDER BY name").fetchall()

    # ---------------- 统计 ----------------
    def list_doc_owners(self):
        """每个人名下有多少份文档（用户管理页显示用）。"""
        return self.conn.execute(
            "SELECT owner, COUNT(*) AS c FROM documents WHERE owner<>'' GROUP BY owner"
        ).fetchall()
    def stats(self):
        row = self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,"
            " SUM(CASE WHEN status='pending' OR status='processing' THEN 1 ELSE 0 END) AS pending,"
            " SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,"
            " SUM(CASE WHEN content_source='text-layer' THEN 1 ELSE 0 END) AS text_layer,"
            " SUM(CASE WHEN content_source IN ('ocr','mixed') THEN 1 ELSE 0 END) AS ocred,"
            " SUM(ocr_chars) AS chars"
            " FROM documents").fetchone()
        return dict(row) if row else {}
