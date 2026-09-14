# -*- coding: utf-8 -*-
"""
自动标签匹配引擎 —— 复刻 paperless-ngx 的 matching algorithm。

支持五种匹配方式（与原版一一对应）：
    any       任一关键词命中（空格分词）
    all       所有关键词都命中（空格分词）
    regex     正则匹配
    exact     整段文本与模式串完全相等
    fulltext  模式串作为完整子串出现（不分词）

作用字段（与原版一致）：
    any           标题 + 正文
    title         仅标题
    content       仅正文
    correspondent 仅往来单位名
"""
import re

FIELDS = ("any", "title", "content", "correspondent")
MATCH_TYPES = ("any", "all", "regex", "exact", "fulltext")

MATCH_TYPE_LABELS = {
    "any": "任一关键词",
    "all": "全部关键词",
    "regex": "正则表达式",
    "exact": "完全相等",
    "fulltext": "全文子串",
}

FIELD_LABELS = {
    "any": "标题+正文",
    "title": "标题",
    "content": "正文",
    "correspondent": "往来单位",
}


def _split_words(pattern):
    return [w for w in re.split(r"[\s,，;；]+", pattern or "") if w]


def _field_text(field, title, content, correspondent):
    if field == "title":
        return title or ""
    if field == "content":
        return content or ""
    if field == "correspondent":
        return correspondent or ""
    return "%s\n%s" % (title or "", content or "")


def match_rule(rule, title, content, correspondent):
    """判断单条规则是否命中。返回 bool。"""
    text = _field_text(rule["field"], title, content, correspondent)
    pattern = rule["pattern"] or ""

    if not rule["case_sensitive"]:
        text_cmp = text.lower()
        pattern_cmp = pattern.lower()
    else:
        text_cmp = text
        pattern_cmp = pattern

    mtype = rule["match_type"]

    if mtype == "any":
        words = _split_words(pattern_cmp)
        return any(w in text_cmp for w in words) if words else False

    if mtype == "all":
        words = _split_words(pattern_cmp)
        return all(w in text_cmp for w in words) if words else False

    if mtype == "fulltext":
        return pattern_cmp.strip() in text_cmp

    if mtype == "exact":
        return text_cmp.strip() == pattern_cmp.strip()

    if mtype == "regex":
        try:
            flags = 0 if rule["case_sensitive"] else re.IGNORECASE
            return re.search(pattern, text, flags) is not None
        except re.error:
            return False

    return False


def apply_rules(db, doc_id, title, content, correspondent, remove_missing=True):
    """
    对一篇文档跑一遍所有启用的自动规则。
    返回被打上的标签名列表。

    remove_missing=True 时，会把"之前由规则自动打上、但这次没再命中"的标签移除，
    这样改了规则之后重跑，标签会跟着变，与原版行为一致。
    """
    rules = [r for r in db.list_rules() if r["enabled"]]
    hits = []          # (tag_id, rule_id)
    hit_rule_ids = []

    for r in rules:
        if match_rule(r, title, content, correspondent):
            hits.append((r["tag_id"], r["id"]))
            hit_rule_ids.append(r["id"])

    if remove_missing:
        # 清掉旧的自动标签（只清 source='auto' 的，保留手动打的）
        old = db.conn.execute(
            "SELECT tag_id FROM document_tags WHERE doc_id=? AND source='auto'",
            (doc_id,)).fetchall()
        for row in old:
            db.conn.execute(
                "DELETE FROM document_tags WHERE doc_id=? AND tag_id=? AND source='auto'",
                (doc_id, row["tag_id"]))

    names = []
    for tag_id, rule_id in hits:
        db.add_doc_tag(doc_id, tag_id, source="auto", rule_id=rule_id)
        db.bump_rule_hits(rule_id)
        row = db.conn.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()
        if row:
            names.append(row["name"])
    return names


def guess_correspondent(content):
    """从正文里猜往来单位（原版 correspondent 的极简版：抓"公司/单位"结尾的机构名）。"""
    if not content:
        return ""
    m = re.search(r"[\u4e00-\u9fa5（）()]{2,30}?(?:有限公司|有限责任公司|股份公司|集团|事务所|研究院|中心|医院|学校)",
                  content)
    return m.group(0) if m else ""
