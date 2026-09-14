# -*- coding: utf-8 -*-
"""
票据合同 OCR 文档库 —— 后端服务
=================================

paperless-ngx 的保真降级实现，保留四大核心：
    图片/PDF 上传 → OCR 文本 → 标签（含自动规则引擎）→ 中文全文搜索

技术栈：Flask + SQLite(FTS5 trigram) + RapidOCR + PyMuPDF

数据目录解析顺序（优先级从高到低）：
    1. 环境变量 PAPERLITE_DATA
    2. local_settings.py 里的 DATA_DIR（该文件不进版本库，放机器相关配置）
    3. 项目目录下的 ./data（默认可移植值）
"""
import os
import json
import socket
import hashlib
import queue
import sys
import threading
import time
import traceback

from flask import Flask, jsonify, request, send_from_directory, Response, session, redirect

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from core.db import Database                                    # noqa: E402
from core.pipeline import run_pipeline, new_stored_name         # noqa: E402
from core.pipeline import _guess_title                          # noqa: E402
from core.rules import apply_rules, guess_correspondent, MATCH_TYPE_LABELS, FIELD_LABELS  # noqa: E402

# 机器相关配置（数据目录、解释器路径等）放在 local_settings.py —— 该文件不进版本库。
# 没有它时，一切走可移植的默认值，换台机器 clone 下来就能跑。
try:
    import local_settings as _local          # noqa: E402
except ImportError:
    _local = None


def _local_get(name, default=None):
    return getattr(_local, name, default) if _local else default


DATA_DIR = (os.environ.get("PAPERLITE_DATA")
            or _local_get("DATA_DIR")
            or os.path.join(BASE_DIR, "data"))
DOCS_DIR = os.path.join(DATA_DIR, "docs")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
DB_PATH = os.path.join(DATA_DIR, "paperlite.db")
PORT = int(os.environ.get("PAPERLITE_PORT", "8765"))
# 监听地址：0.0.0.0 = 允许同局域网的其他机器访问；
# 只想自己用就设 PAPERLITE_HOST=127.0.0.1。
HOST = os.environ.get("PAPERLITE_HOST", "0.0.0.0")

for d in (DATA_DIR, DOCS_DIR, THUMB_DIR):
    os.makedirs(d, exist_ok=True)

ALLOWED_EXT = {
    # 图片
    "png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp",
    # 文档
    "pdf",
    # Excel
    "xlsx", "xlsm", "xls",
    # PowerPoint
    "pptx", "ppt",
}

# 扩展名 -> 格式大类（用于左侧栏格式分类筛选）
FORMAT_GROUPS = {
    "pdf": "pdf",
    "png": "image", "jpg": "image", "jpeg": "image",
    "bmp": "image", "tif": "image", "tiff": "image", "webp": "image",
    "xlsx": "excel", "xlsm": "excel", "xls": "excel",
    "pptx": "ppt", "ppt": "ppt",
}

FORMAT_LABELS = {
    "pdf": "PDF",
    "image": "图片",
    "excel": "Excel",
    "ppt": "PPT",
    "other": "其他",
}


def format_of(ext):
    """扩展名归一到格式大类。"""
    return FORMAT_GROUPS.get((ext or "").lower(), "other")


def _file_md5(path, chunk=1 << 20):
    """分块计算文件 MD5，用于重复文件检测。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 单文件 100MB
app.config["JSON_AS_ASCII"] = False

# --------------------------------------------------------------------------
# 访问控制（给同事共享时用）
# --------------------------------------------------------------------------
# 账号密码存在「数据目录」里，不写在代码里，也不进 git。
# 首次访问会强制先设置账号密码，不会有无密码的空窗期。
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")
LOGIN_FAILS = {}          # ip -> [失败次数, 最后一次时间]
MAX_FAILS = 5             # 连续失败 5 次
FAIL_LOCK = 300           # 锁 5 分钟（秒）
LOCAL_IPS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")   # 本机不锁

try:
    from werkzeug.security import generate_password_hash, check_password_hash
except ImportError:       # 极端兜底：没装 werkzeug 也能跑（Flask 自带，正常不会走到）
    import hashlib as _hl
    import secrets as _sc

    def generate_password_hash(pw):
        salt = _sc.token_hex(16)
        return "sha256$%s$%s" % (salt, _hl.sha256((salt + pw).encode()).hexdigest())

    def check_password_hash(h, pw):
        try:
            _, salt, digest = h.split("$")
            return _hl.sha256((salt + pw).encode()).hexdigest() == digest
        except Exception:
            return False


def _ensure_secret():
    """session 签名密钥：生成一次存起来，重启后登录状态不丢。"""
    key = ""
    if os.path.exists(SECRET_FILE):
        try:
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
        except Exception:
            key = ""
    if not key:
        import secrets
        key = secrets.token_hex(32)
        try:
            with open(SECRET_FILE, "w", encoding="utf-8") as f:
                f.write(key)
        except Exception:
            pass
    return key


app.secret_key = _ensure_secret()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 12 * 3600   # 登录保持 12 小时


def load_auth():
    """返回账号文件的原始 dict；文件不存在（还没设置过）时返回 None。"""
    if not os.path.exists(AUTH_FILE):
        return None
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    # 兼容早期单账号格式：{username, password_hash}
    if data and "users" not in data and data.get("username"):
        data = {"users": [{
            "username": data["username"],
            "password_hash": data.get("password_hash", ""),
            "is_admin": 1,
            "created_at": data.get("updated_at", ""),
        }]}
    return data


def load_users():
    data = load_auth()
    return (data or {}).get("users") or []


def save_users(users):
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump({"users": users,
                   "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                  f, ensure_ascii=False, indent=2)


def find_user(username):
    for u in load_users():
        if u.get("username") == username:
            return u
    return None


def create_user(username, password, is_admin=0):
    users = load_users()
    users.append({
        "username": username,
        "password_hash": generate_password_hash(password),
        "is_admin": 1 if is_admin else 0,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_users(users)


def current_user():
    """当前登录用户（dict），未登录返回 None。"""
    uid = session.get("uid")
    if not uid:
        return None
    return find_user(uid)


def is_admin_user(user=None):
    u = user if user is not None else current_user()
    return bool(u and u.get("is_admin"))


# --------------------------------------------------------------------------
# 文档权限：私密 / 只读 / 公开编辑
# --------------------------------------------------------------------------
VIS_LABELS = {
    "private": "私密（只有你自己能看）",
    "readonly": "只读（别人能看，不能改）",
    "public_edit": "公开编辑（别人也能改）",
}
VIS_SHORT = {"private": "私密", "readonly": "只读", "public_edit": "公开编辑"}


def can_view(row, user=None):
    """能不能看到这份文档"""
    u = user if user is not None else current_user()
    if not u:
        return False
    if u.get("is_admin"):          # 管理员（库主）看得见全部
        return True
    if row["owner"] and row["owner"] == u["username"]:
        return True                # 自己上传的
    return row["visibility"] != "private"


def can_edit(row, user=None):
    """能不能改这份文档（改标题/标签、删除、重新识别）"""
    u = user if user is not None else current_user()
    if not u:
        return False
    if u.get("is_admin"):
        return True
    if row["owner"] and row["owner"] == u["username"]:
        return True                # 自己的随便改
    return row["visibility"] == "public_edit"   # 别人的：只有被设成公开编辑才能改


def is_owner(row, user=None):
    u = user if user is not None else current_user()
    if not u:
        return False
    return bool(row["owner"]) and row["owner"] == u["username"]


def lan_ips():
    """本机内网 IP（给同事访问用），排除 127.x 与虚拟网卡常见的 169.254.x。"""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for it in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(it)
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127.") and not i.startswith("169.254."))


# 这几个地址不设防：登录页 + 静态资源 + 认证接口
AUTH_FREE = ("/login", "/api/auth/")


@app.before_request
def _auth_guard():
    p = request.path
    if p.startswith("/static/") or p.startswith(AUTH_FREE):
        return None
    if not load_users():
        # 还没有任何账号 —— 谁来访问都先看设置页
        if p.startswith("/api/"):
            return jsonify({"error": "need_setup"}), 401
        return redirect("/login")
    if not session.get("uid"):
        if p.startswith("/api/"):
            return jsonify({"error": "未登录或登录已过期"}), 401
        return redirect("/login")
    return None


@app.get("/login")
def login_page():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "login.html")


@app.get("/api/auth/status")
def auth_status():
    u = current_user()
    return jsonify({
        "need_setup": not load_users(),
        "logged_in": bool(u),
        "username": session.get("uid") or "",
        "is_admin": bool(u and u.get("is_admin")),
    })


@app.post("/api/auth/setup")
def auth_setup():
    """创建第一个账号——自动成为管理员，并接管库里已有的老文档。"""
    if load_users():
        return jsonify({"error": "账号已经设置过了，请直接登录"}), 400
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if len(username) < 2:
        return jsonify({"error": "账号至少 2 个字符"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    create_user(username, password, is_admin=1)
    # 升级前就存在的文档没有归属人，交给第一个账号，免得变成谁都看不到的孤儿
    adopted = db.adopt_orphan_docs(username)
    if adopted:
        print("  [权限] 已把 %d 份历史文档划归管理员「%s」" % (adopted, username))
    session["uid"] = username
    session.permanent = True
    return jsonify({"ok": True, "adopted": adopted})


@app.post("/api/auth/login")
def auth_login():
    if not load_users():
        return jsonify({"error": "尚未设置账号密码"}), 400
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    # 简单防爆破：同一 IP 连错 5 次锁 5 分钟
    # 注意：只锁外部 IP，本机（127.0.0.1）不锁——你自己手滑输错不该把自己关在门外
    ip = request.remote_addr or "-"
    now = time.time()
    rec = LOGIN_FAILS.get(ip)
    if ip not in LOCAL_IPS and rec and rec[0] >= MAX_FAILS and now - rec[1] < FAIL_LOCK:
        return jsonify({"error": "错误次数太多，请 5 分钟后再试（急用就重启服务）"}), 429
    user = find_user(username)
    ok_pwd = check_password_hash(user.get("password_hash", ""), password) if user else False
    if not (user and ok_pwd):
        LOGIN_FAILS[ip] = [ (rec[0] + 1) if rec else 1, now ]
        return jsonify({"error": "账号或密码不对"}), 401
    LOGIN_FAILS.pop(ip, None)
    session["uid"] = username
    session.permanent = True
    return jsonify({"ok": True})


@app.post("/api/auth/logout")
def auth_logout():
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/auth/password")
def auth_change_password():
    """登录后改自己的密码（需要输原密码）。"""
    u = current_user()
    if not u:
        return jsonify({"error": "未登录"}), 401
    data = request.get_json(force=True, silent=True) or {}
    old = data.get("old_password") or ""
    new = data.get("new_password") or ""
    if not check_password_hash(u.get("password_hash", ""), old):
        return jsonify({"error": "原密码不对"}), 400
    if len(new) < 6:
        return jsonify({"error": "新密码至少 6 位"}), 400
    users = load_users()
    for it in users:
        if it["username"] == u["username"]:
            it["password_hash"] = generate_password_hash(new)
    save_users(users)
    session.clear()   # 改完强制重新登录
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# 用户管理（仅管理员）
# --------------------------------------------------------------------------
@app.get("/api/users")
def api_users():
    if not is_admin_user():
        return jsonify({"error": "只有管理员能查看用户"}), 403
    me = current_user()["username"]
    rows = db.list_doc_owners()
    counts = {r["owner"]: r["c"] for r in rows}
    return jsonify({"users": [{
        "username": u.get("username", ""),
        "is_admin": bool(u.get("is_admin")),
        "created_at": u.get("created_at", ""),
        "doc_count": counts.get(u.get("username", ""), 0),
        "is_me": u.get("username") == me,
    } for u in load_users()]})


@app.post("/api/users")
def api_create_user():
    if not is_admin_user():
        return jsonify({"error": "只有管理员能添加用户"}), 403
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    is_admin = 1 if data.get("is_admin") else 0
    if len(username) < 2:
        return jsonify({"error": "账号至少 2 个字符"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    if find_user(username):
        return jsonify({"error": "这个账号名已经被用了"}), 400
    create_user(username, password, is_admin=is_admin)
    return jsonify({"ok": True})


@app.delete("/api/users/<username>")
def api_delete_user(username):
    u = current_user()
    if not (u and u.get("is_admin")):
        return jsonify({"error": "只有管理员能删除用户"}), 403
    if username == u["username"]:
        return jsonify({"error": "不能删掉你自己"}), 400
    users = [x for x in load_users() if x.get("username") != username]
    if len(users) == len(load_users()):
        return jsonify({"error": "找不到这个用户"}), 404
    if not any(x.get("is_admin") for x in users):
        return jsonify({"error": "至少要留一个管理员"}), 400
    save_users(users)
    return jsonify({"ok": True})


@app.post("/api/users/<username>/password")
def api_reset_user_password(username):
    """管理员给同事重置密码（同事自己忘了密码时用）。"""
    if not is_admin_user():
        return jsonify({"error": "只有管理员能重置密码"}), 403
    data = request.get_json(force=True, silent=True) or {}
    new = data.get("password") or ""
    if len(new) < 6:
        return jsonify({"error": "新密码至少 6 位"}), 400
    users = load_users()
    hit = None
    for it in users:
        if it["username"] == username:
            it["password_hash"] = generate_password_hash(new)
            hit = it
    if not hit:
        return jsonify({"error": "找不到这个用户"}), 404
    save_users(users)
    return jsonify({"ok": True})


db = Database(DB_PATH)

# 处理日志（内存态，供前端展示进度）：doc_id -> [消息...]
LOGS = {}
LOGS_LOCK = threading.Lock()
TASK_QUEUE = queue.Queue()


def push_log(doc_id, msg):
    with LOGS_LOCK:
        LOGS.setdefault(doc_id, []).append({"t": time.strftime("%H:%M:%S"), "m": msg})
        if len(LOGS[doc_id]) > 200:
            LOGS[doc_id] = LOGS[doc_id][-200:]


def get_logs(doc_id):
    with LOGS_LOCK:
        return list(LOGS.get(doc_id, []))


# --------------------------------------------------------------------------
# 后台处理
# --------------------------------------------------------------------------
def process_document(doc_id):
    try:
        db.update_document(doc_id, status="processing", progress=5, error="")
        doc = db.get_document(doc_id)
        src = os.path.join(DOCS_DIR, doc["stored_name"])
        thumb = os.path.join(THUMB_DIR, "%d.jpg" % doc_id)

        push_log(doc_id, "开始处理：%s" % doc["original_name"])
        t0 = time.time()

        content, source, pages = run_pipeline(
            src, doc["ext"], thumb, log=lambda m: push_log(doc_id, m))

        title = _guess_title(content, doc["original_name"])
        correspondent = guess_correspondent(content)
        corr_id = db.get_or_create_correspondent(correspondent) if correspondent else None

        db.update_document(
            doc_id,
            title=title,
            content=content,
            content_source=source,
            page_count=pages,
            ocr_chars=len(content),
            correspondent_id=corr_id,
            status="done",
            progress=100,
            processed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        # 自动标签
        tags = apply_rules(db, doc_id, title, content, correspondent)
        push_log(doc_id, "自动打标签：%s" % (", ".join(tags) if tags else "（无匹配）"))

        cost = time.time() - t0
        label = {"text-layer": "文本层抽取", "ocr": "OCR 识别",
                 "mixed": "混合抽取", "office": "文件解析"}.get(source, source)
        push_log(doc_id, "完成（%s，%d 页 / %d 字 / 耗时 %.1fs）" % (label, pages, len(content), cost))
    except Exception as e:
        db.update_document(doc_id, status="failed", error=str(e), progress=0)
        push_log(doc_id, "失败：%s" % e)
        traceback.print_exc()
    finally:
        with LOGS_LOCK:
            pass


def _worker_loop():
    """串行消费任务队列。

    本机只有 2 个逻辑核心，OCR 是纯 CPU 密集任务，并发跑多个反而互相抢时间片、
    整体更慢，还会把内存峰值叠上去（3.9GB 总量下很危险）。
    因此这里刻意只用 1 个常驻 worker 串行处理，与原版在低配设备上把
    PAPERLESS_TASK_WORKERS 调成 1 的做法一致。
    """
    while True:
        doc_id = TASK_QUEUE.get()
        try:
            process_document(doc_id)
        except Exception:
            traceback.print_exc()
        finally:
            TASK_QUEUE.task_done()


def start_worker():
    t = threading.Thread(target=_worker_loop, daemon=True)
    t.start()


def spawn_worker(doc_id):
    """把文档丢进处理队列（不阻塞请求）。"""
    TASK_QUEUE.put(doc_id)


# --------------------------------------------------------------------------
# 序列化
# --------------------------------------------------------------------------
def doc_to_dict(row, with_content=False, with_tags=True):
    d = {
        "id": row["id"],
        "title": row["title"],
        "original_name": row["original_name"],
        "mime": row["mime"],
        "ext": row["ext"],
        "format": format_of(row["ext"]),
        "file_size": row["file_size"],
        "page_count": row["page_count"],
        "content_source": row["content_source"],
        "ocr_chars": row["ocr_chars"],
        "status": row["status"],
        "progress": row["progress"],
        "error": row["error"],
        "created_at": row["created_at"],
        "processed_at": row["processed_at"],
        "correspondent_id": row["correspondent_id"],
        "doctype_id": row["doctype_id"],
        "thumb_url": "/thumb/%d" % row["id"],
        "file_url": "/file/%d" % row["id"],
        # 多用户权限：归属人 + 可见性 + 当前用户能不能改
        "owner": row["owner"] if "owner" in row.keys() else "",
        "visibility": row["visibility"] if "visibility" in row.keys() else "readonly",
    }
    d["visibility_label"] = VIS_SHORT.get(d["visibility"], d["visibility"])
    d["is_owner"] = is_owner(row)
    d["can_edit"] = can_edit(row)
    if row["correspondent_id"]:
        c = db.conn.execute("SELECT name FROM correspondents WHERE id=?",
                            (row["correspondent_id"],)).fetchone()
        d["correspondent"] = c["name"] if c else ""
    else:
        d["correspondent"] = ""
    if row["doctype_id"]:
        dt = db.conn.execute("SELECT name FROM document_types WHERE id=?",
                             (row["doctype_id"],)).fetchone()
        d["doctype"] = dt["name"] if dt else ""
    else:
        d["doctype"] = ""
    if with_tags:
        d["tags"] = [{"id": t["id"], "name": t["name"], "color": t["color"],
                      "source": t["source"]} for t in db.get_doc_tags(row["id"])]
    if with_content:
        d["content"] = row["content"]
        d["preview"] = (row["content"] or "")[:400]
    else:
        d["preview"] = (row["content"] or "")[:160]
    return d


# --------------------------------------------------------------------------
# 页面 / 静态资源
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")


@app.get("/thumb/<int:doc_id>")
def thumb(doc_id):
    # 私密文档的缩略图也不能被别人直接按 URL 拿到
    row = db.get_document(doc_id)
    if not row or not can_view(row):
        return Response(status=403)
    path = os.path.join(THUMB_DIR, "%d.jpg" % doc_id)
    if not os.path.exists(path):
        return Response(status=404)
    return send_from_directory(THUMB_DIR, "%d.jpg" % doc_id, mimetype="image/jpeg")


@app.get("/file/<int:doc_id>")
def serve_file(doc_id):
    row = db.get_document(doc_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    if not can_view(row):
        return jsonify({"error": "这份文档是私密的，你没有权限下载"}), 403
    return send_from_directory(DOCS_DIR, row["stored_name"],
                               download_name=row["original_name"], as_attachment=False)


# --------------------------------------------------------------------------
# 文档 API
# --------------------------------------------------------------------------
@app.get("/api/documents")
def api_list():
    q = request.args.get("q", "")
    tag_id = request.args.get("tag", type=int)
    doctype_id = request.args.get("doctype", type=int)
    corr_id = request.args.get("correspondent", type=int)
    u = current_user()
    rows = db.search_documents(q, tag_id, doctype_id, corr_id,
                               viewer=(u or {}).get("username"),
                               viewer_admin=bool(u and u.get("is_admin")))
    return jsonify({"items": [doc_to_dict(r) for r in rows], "total": len(rows)})


@app.get("/api/documents/<int:doc_id>")
def api_detail(doc_id):
    row = db.get_document(doc_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    if not can_view(row):
        return jsonify({"error": "这份文档是私密的，你没有权限查看"}), 403
    data = doc_to_dict(row, with_content=True)
    data["logs"] = get_logs(doc_id)
    return jsonify(data)


@app.patch("/api/documents/<int:doc_id>/visibility")
def api_set_visibility(doc_id):
    """
    设置文档可见性（私密 / 只读 / 公开编辑）。
    只有上传者本人能改；管理员（库主）也能改。
    """
    row = db.get_document(doc_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    u = current_user()
    if not (is_owner(row, u) or is_admin_user(u)):
        return jsonify({"error": "只有上传者本人或管理员能改这个设置"}), 403
    vis = (request.get_json(force=True, silent=True) or {}).get("visibility", "")
    if vis not in VIS_LABELS:
        return jsonify({"error": "可见性取值只能是 private / readonly / public_edit"}), 400
    db.set_visibility(doc_id, vis)
    return jsonify({"ok": True, "visibility": vis,
                    "visibility_label": VIS_SHORT.get(vis, vis)})


@app.post("/api/documents")
def api_upload():
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "没有收到文件"}), 400

    created, rejected = [], []
    u = current_user() or {}
    owner = u.get("username", "")
    # 上传时可指定默认可见性（私密 / 只读 / 公开编辑），默认「只读」
    vis = request.form.get("visibility", "readonly")
    if vis not in VIS_LABELS:
        vis = "readonly"
    for f in files:
        original = f.filename or "未命名"
        ext = os.path.splitext(original)[1].lstrip(".").lower()
        if ext not in ALLOWED_EXT:
            rejected.append({"name": original, "reason": "不支持的类型：.%s" % (ext or "空")})
            continue
        stored = new_stored_name(original)
        save_path = os.path.join(DOCS_DIR, stored)
        f.save(save_path)
        size = os.path.getsize(save_path)
        fhash = _file_md5(save_path)
        dup = db.find_doc_by_hash(fhash)
        if dup:
            os.remove(save_path)  # 重复文件不入库，连暂存文件一起清掉
            rejected.append({"name": original,
                             "reason": "内容重复：与已有文档《%s》相同，已保留原文件" % dup["title"]})
            continue
        doc_id = db.add_document(
            title=os.path.splitext(original)[0],
            original_name=original,
            stored_name=stored,
            mime=f.mimetype or "",
            ext=ext,
            file_size=size,
            file_hash=fhash,
            owner=owner,
            visibility=vis,
        )
        created.append(doc_id)
        spawn_worker(doc_id)

    return jsonify({"created": created, "rejected": rejected})


# --------------------------------------------------------------------------
# 文件夹扫描导入
# --------------------------------------------------------------------------
@app.post("/api/scan-folder")
def api_scan_folder():
    """扫描一个文件夹，返回其中所有可上传文件的清单（含重复标记）。"""
    data = request.get_json(silent=True) or {}
    folder = (data.get("path") or "").strip().strip('"')
    if not folder:
        return jsonify({"error": "请输入文件夹路径"}), 400
    if not os.path.isdir(folder):
        return jsonify({"error": "文件夹不存在：%s" % folder}), 400

    # 已有文档的哈希表（老文档哈希为空则现算并回填，一次性自愈）
    known = {}
    for r in db.conn.execute("SELECT id, title, stored_name, file_hash FROM documents").fetchall():
        h = r["file_hash"]
        if not h:
            fp = os.path.join(DOCS_DIR, r["stored_name"])
            if os.path.isfile(fp):
                try:
                    h = _file_md5(fp)
                    db.update_document(r["id"], file_hash=h)
                except OSError:
                    continue
            else:
                continue
        known[h] = r["title"]

    items, skipped_others, total_files = [], 0, 0
    seen_hashes = {}   # 批内去重: hash -> 第一个文件名
    for root, _dirs, names in os.walk(folder):
        for n in names:
            ext = os.path.splitext(n)[1].lstrip(".").lower()
            total_files += 1
            if ext not in ALLOWED_EXT:
                skipped_others += 1
                continue
            full = os.path.join(root, n)
            try:
                size = os.path.getsize(full)
                fhash = _file_md5(full)
            except OSError:
                continue
            if fhash in known:
                status, detail = "dup-db", "与已有文档《%s》内容相同" % known[fhash]
            elif fhash in seen_hashes:
                status, detail = "dup-batch", "与本批《%s》内容相同" % seen_hashes[fhash]
            else:
                status, detail = "new", ""
                seen_hashes[fhash] = n
            items.append({
                "path": full,
                "name": n,
                "rel": os.path.relpath(full, folder),
                "size": size,
                "status": status,
                "detail": detail,
            })
            if len(items) >= 1000:
                break
        if len(items) >= 1000:
            break

    items.sort(key=lambda x: x["rel"].lower())
    return jsonify({
        "items": items,
        "folder": folder,
        "total_files": total_files,
        "skipped_others": skipped_others,
        "new_count": sum(1 for i in items if i["status"] == "new"),
    })


@app.post("/api/import-folder")
def api_import_folder():
    """批量导入用户勾选的本地文件（导入时再查重一次，重复的跳过）。"""
    data = request.get_json(silent=True) or {}
    paths = data.get("paths") or []
    if not paths:
        return jsonify({"error": "没有选择文件"}), 400

    created, skipped = [], []
    batch_hashes = set()
    u = current_user() or {}
    owner = u.get("username", "")
    vis = data.get("visibility", "readonly")
    if vis not in VIS_LABELS:
        vis = "readonly"
    for p in paths:
        p = (p or "").strip()
        ext = os.path.splitext(p)[1].lstrip(".").lower()
        if ext not in ALLOWED_EXT or not os.path.isfile(p):
            skipped.append({"path": p, "reason": "文件不存在或类型不支持"})
            continue
        try:
            fhash = _file_md5(p)
        except OSError:
            skipped.append({"path": p, "reason": "文件无法读取"})
            continue
        dup = db.find_doc_by_hash(fhash)
        if dup:
            skipped.append({"path": os.path.basename(p),
                            "reason": "内容重复：与《%s》相同" % dup["title"]})
            continue
        if fhash in batch_hashes:
            skipped.append({"path": os.path.basename(p), "reason": "本批内重复，只保留一个"})
            continue
        batch_hashes.add(fhash)

        original = os.path.basename(p)
        stored = new_stored_name(original)
        dst = os.path.join(DOCS_DIR, stored)
        try:
            with open(p, "rb") as src, open(dst, "wb") as out:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
        except OSError as e:
            skipped.append({"path": original, "reason": "复制失败：%s" % e})
            continue
        doc_id = db.add_document(
            title=os.path.splitext(original)[0],
            original_name=original,
            stored_name=stored,
            mime="",
            ext=ext,
            file_size=os.path.getsize(dst),
            file_hash=fhash,
            owner=owner,
            visibility=vis,
        )
        created.append(doc_id)
        spawn_worker(doc_id)

    return jsonify({"created": created, "skipped": skipped})


@app.patch("/api/documents/<int:doc_id>")
def api_update(doc_id):
    row = db.get_document(doc_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    if not can_edit(row):
        return jsonify({"error": "这份文档你没有编辑权限（上传者设为只读）"}), 403
    data = request.get_json(silent=True) or {}
    fields = {}
    if "title" in data:
        fields["title"] = data["title"]
    if "correspondent" in data:
        fields["correspondent_id"] = db.get_or_create_correspondent(data["correspondent"])
    if "doctype" in data:
        fields["doctype_id"] = db.get_or_create_doctype(data["doctype"])
    db.update_document(doc_id, **fields)
    return jsonify({"ok": True})


@app.delete("/api/documents/<int:doc_id>")
def api_delete(doc_id):
    row = db.get_document(doc_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    if not can_edit(row):
        return jsonify({"error": "这份文档你没有删除权限（上传者设为只读）"}), 403
    for p in (os.path.join(DOCS_DIR, row["stored_name"]),
              os.path.join(THUMB_DIR, "%d.jpg" % doc_id)):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    db.delete_document(doc_id)
    return jsonify({"ok": True})


@app.post("/api/documents/<int:doc_id>/tags")
def api_add_tag(doc_id):
    row = db.get_document(doc_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    if not can_edit(row):
        return jsonify({"error": "这份文档你没有编辑权限（上传者设为只读）"}), 403
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "标签名为空"}), 400
    is_new = db.tag_exists(name) is None
    tag_id = db.get_or_create_tag(name)
    db.add_doc_tag(doc_id, tag_id, source="manual")
    auto = _auto_match_new_tag(name, tag_id) if is_new else None
    return jsonify({"ok": True, "tag_id": tag_id, "auto": auto})


@app.delete("/api/documents/<int:doc_id>/tags/<int:tag_id>")
def api_remove_tag(doc_id, tag_id):
    row = db.get_document(doc_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    if not can_edit(row):
        return jsonify({"error": "这份文档你没有编辑权限（上传者设为只读）"}), 403
    db.remove_doc_tag(doc_id, tag_id)
    return jsonify({"ok": True})


@app.post("/api/documents/<int:doc_id>/reprocess")
def api_reprocess(doc_id):
    row = db.get_document(doc_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    if not can_edit(row):
        return jsonify({"error": "这份文档你没有编辑权限（上传者设为只读）"}), 403
    db.update_document(doc_id, status="pending", progress=0, error="")
    with LOGS_LOCK:
        LOGS.pop(doc_id, None)
    spawn_worker(doc_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# 标签 API
# --------------------------------------------------------------------------
@app.get("/api/tags")
def api_tags():
    return jsonify({"items": [dict(r) for r in db.list_tags()]})


@app.post("/api/tags")
def api_create_tag():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "标签名为空"}), 400
    is_new = db.tag_exists(name) is None
    tag_id = db.get_or_create_tag(name, data.get("color", "#7c9cff"))
    auto = _auto_match_new_tag(name, tag_id) if is_new else None
    return jsonify({"ok": True, "tag_id": tag_id, "auto": auto})


@app.delete("/api/tags/<int:tag_id>")
def api_delete_tag(tag_id):
    db.delete_tag(tag_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# 规则 API
# --------------------------------------------------------------------------
@app.get("/api/rules")
def api_rules():
    return jsonify({
        "items": [dict(r) for r in db.list_rules()],
        "meta": {
            "match_types": MATCH_TYPE_LABELS,
            "fields": FIELD_LABELS,
        },
    })


@app.post("/api/rules")
def api_create_rule():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    pattern = (data.get("pattern") or "").strip()
    if not name or not pattern:
        return jsonify({"error": "标签名与匹配内容都不能为空"}), 400
    tag_id = db.get_or_create_tag(name, data.get("color", "#7c9cff"))
    rule_id = db.add_rule(
        tag_id,
        data.get("field", "any"),
        data.get("match_type", "any"),
        pattern,
        name=(data.get("rule_name") or "").strip(),
        case_sensitive=int(bool(data.get("case_sensitive"))),
    )
    return jsonify({"ok": True, "rule_id": rule_id, "tag_id": tag_id})


@app.patch("/api/rules/<int:rule_id>")
def api_update_rule(rule_id):
    data = request.get_json(silent=True) or {}
    fields = {}
    for k in ("field", "match_type", "pattern", "enabled", "case_sensitive", "name"):
        if k in data:
            fields[k] = data[k]
    db.update_rule(rule_id, **fields)
    return jsonify({"ok": True})


@app.delete("/api/rules/<int:rule_id>")
def api_delete_rule(rule_id):
    db.delete_rule(rule_id)
    return jsonify({"ok": True})


@app.post("/api/rules/apply")
def api_apply_rules():
    """对所有已完成文档重跑自动标签（改了规则后刷新结果）。"""
    count = _apply_all_rules()
    return jsonify({"ok": True, "applied": count})


def _apply_all_rules():
    """对所有已完成文档重跑全部启用规则，返回处理的文档数。"""
    rows = db.conn.execute(
        "SELECT id, title, content, correspondent_id FROM documents WHERE status='done'"
    ).fetchall()
    count = 0
    for r in rows:
        corr = ""
        if r["correspondent_id"]:
            c = db.conn.execute("SELECT name FROM correspondents WHERE id=?",
                                (r["correspondent_id"],)).fetchone()
            corr = c["name"] if c else ""
        apply_rules(db, r["id"], r["title"], r["content"], corr)
        count += 1
    return count


def _auto_match_new_tag(name, tag_id):
    """
    新建标签时自动全库检索：给新标签生成一条"标签名=关键词"的规则
    （标题+正文，任一关键词），并立即对所有文档应用。
    返回 {"rule_id":..., "applied":..., "tagged":...}；该标签已有规则则返回 None。
    """
    row = db.conn.execute(
        "SELECT id FROM tag_rules WHERE tag_id=?", (tag_id,)).fetchone()
    if row:
        return None
    rule_id = db.add_rule(tag_id, "any", "any", name, name="自动匹配")
    applied = 0
    tagged = 0
    rows = db.conn.execute(
        "SELECT id, title, content, correspondent_id FROM documents WHERE status='done'"
    ).fetchall()
    for r in rows:
        corr = ""
        if r["correspondent_id"]:
            c = db.conn.execute("SELECT name FROM correspondents WHERE id=?",
                                (r["correspondent_id"],)).fetchone()
            corr = c["name"] if c else ""
        names = apply_rules(db, r["id"], r["title"], r["content"], corr)
        applied += 1
        if name in names:
            tagged += 1
    return {"rule_id": rule_id, "applied": applied, "tagged": tagged}


# --------------------------------------------------------------------------
# 其它
# --------------------------------------------------------------------------
@app.get("/api/meta")
def api_meta():
    u = current_user()
    return jsonify({
        "me": {
            "username": (u or {}).get("username", ""),
            "is_admin": bool(u and u.get("is_admin")),
        },
        "visibility_options": [{"value": k, "label": v} for k, v in VIS_LABELS.items()],
        "correspondents": [dict(r) for r in db.list_correspondents()],
        "doctypes": [dict(r) for r in db.list_doctypes()],
        "stats": db.stats(),
        "data_dir": DATA_DIR,
        "queue_size": TASK_QUEUE.qsize(),
        "formats": format_counts(),
    })


def format_counts():
    """按格式大类统计文档数（不受当前筛选影响，供左侧栏显示）。
    同样要过滤掉别人的私密文档，否则左侧数字会和实际看到的对不上。"""
    u = current_user()
    where, params = "", []
    if u and not u.get("is_admin"):
        where = "WHERE visibility <> 'private' OR owner = ?"
        params = [u["username"]]
    counts = {}
    sql = "SELECT ext, COUNT(*) AS c FROM documents %s GROUP BY ext" % where
    for r in db.conn.execute(sql, params):
        g = format_of(r["ext"])
        counts[g] = counts.get(g, 0) + (r["c"] or 0)
    return [
        {"key": k, "label": FORMAT_LABELS.get(k, k), "count": counts.get(k, 0)}
        for k in ("pdf", "image", "excel", "ppt", "other")
    ]


@app.get("/api/suggest")
def api_suggest():
    """搜索自动补全：文档标题 + 标签名 候选（私密文档只对本人/管理员显示）。"""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify({"titles": [], "tags": []})
    like = "%" + q + "%"
    u = current_user()
    titles, tags = [], []

    sql_docs = "SELECT id, title, original_name FROM documents WHERE (title LIKE ? OR original_name LIKE ?)"
    params_docs = [like, like]
    if u and not u.get("is_admin"):
        sql_docs += " AND (visibility <> 'private' OR owner = ?)"
        params_docs.append(u["username"])
    sql_docs += " ORDER BY id DESC LIMIT 5"
    for r in db.conn.execute(sql_docs, params_docs):
        name = r["title"] or r["original_name"]
        if name and name not in [t["name"] for t in titles]:
            titles.append({"name": name, "doc_id": r["id"]})

    for r in db.conn.execute(
            "SELECT t.id, t.name, t.color FROM tags t WHERE t.name LIKE ? LIMIT 3", (like,)):
        tags.append({"id": r["id"], "name": r["name"], "color": r["color"]})

    return jsonify({"titles": titles, "tags": tags})


@app.post("/api/documents/batch-tags")
def api_batch_tags():
    """批量添加/移除标签。仅对「我有编辑权」的文档生效，其余跳过。"""
    data = request.get_json(force=True, silent=True) or {}
    ids = data.get("ids") or []
    tag_id = data.get("tag_id")
    action = data.get("action")  # add | remove
    if not ids or not tag_id or action not in ("add", "remove"):
        return jsonify({"error": "参数不完整（需要 ids / tag_id / action）"}), 400
    if not db.conn.execute("SELECT 1 FROM tags WHERE id=?", (tag_id,)).fetchone():
        return jsonify({"error": "标签不存在"}), 404

    u = current_user()
    updated, skipped = 0, 0
    for doc_id in ids:
        row = db.get_document(int(doc_id))
        if not row or not can_edit(row):
            skipped += 1
            continue
        if action == "add":
            db.add_doc_tag(int(doc_id), int(tag_id), source="manual")
        else:
            db.remove_doc_tag(int(doc_id), int(tag_id))
        updated += 1
    return jsonify({"ok": True, "updated": updated, "skipped": skipped})


@app.get("/api/stats")
def api_stats():
    return jsonify(db.stats())


def recover_stuck_docs():
    """
    启动时自愈：上一次服务退出时正在处理/排队的文档会永远停在
    pending/processing（前端还会因此无限轮询）。这里把它们重新入队。
    """
    rows = db.conn.execute(
        "SELECT id FROM documents WHERE status IN ('pending','processing')"
    ).fetchall()
    for r in rows:
        db.update_document(r["id"], status="pending", progress=0)
        spawn_worker(r["id"])
    if rows:
        print("已重新排队 %d 份未完成文档" % len(rows))


def adopt_orphan_docs_if_any():
    """
    启动时兜底：把没有归属人的老文档划给管理员。
    （升级到多用户版本之前的文档 owner 是空的，不处理就会变成谁都改不了的孤儿）
    """
    users = load_users()
    if not users:
        return 0
    row = db.conn.execute(
        "SELECT COUNT(*) AS c FROM documents WHERE owner='' OR owner IS NULL").fetchone()
    if not row or not row["c"]:
        return 0
    admin = next((u for u in users if u.get("is_admin")), users[0])
    n = db.adopt_orphan_docs(admin["username"])
    if n:
        print("  [权限] 已把 %d 份无归属文档划归管理员「%s」" % (n, admin["username"]))
    return n


if __name__ == "__main__":
    start_worker()
    recover_stuck_docs()
    adopt_orphan_docs_if_any()
    print("=" * 58)
    print("  票据合同 OCR 文档库（paperless-ngx 保真降级版）")
    print("=" * 58)
    print("  数据目录 :", DATA_DIR)
    print("  本机访问 : http://127.0.0.1:%d" % PORT)
    for ip in lan_ips():
        print("  同事访问 : http://%s:%d   （需同一局域网 + 账号密码）" % (ip, PORT))
    if not lan_ips():
        print("  同事访问 : 未检测到内网 IP，请检查网络连接")
    if load_auth() is None:
        print("  ⚠ 尚未设置账号密码 —— 打开页面会先要求设置")
    print("  ⚠ 公司网络下请先确认已放行防火墙端口（局域网共享.py）")
    print("=" * 58)
    # 0.0.0.0 = 允许局域网其他电脑连进来；想退回只本机可用就设环境变量 PAPERLITE_HOST=127.0.0.1
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
