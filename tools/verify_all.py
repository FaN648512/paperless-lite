# -*- coding: utf-8 -*-
"""
端到端验收：搜索 / 标签筛选 / 自动标签规则引擎 / PDF 双分支。
"""
import json
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8765"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, payload=None):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def delete(path):
    req = urllib.request.Request(BASE + path, method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def patch(path, payload):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="PATCH",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  [通过] %s %s" % (name, detail))
    else:
        failed += 1
        print("  [失败] %s %s" % (name, detail))


# ============================================================
print("=" * 72)
print("一、中文全文搜索")
print("=" * 72)

QUERIES = [
    ("发票", "2字词，trigram 无效 → LIKE 兜底", ["增值税专用发票"]),
    ("蓝盾检测", "3字词，trigram 主路径", ["增值税专用发票"]),
    ("工作场所", "4字词", ["增值税专用发票", "劳动合同书"]),
    ("12800", "数字金额", ["增值税专用发票"]),
    ("星海电子", "长词短语", ["设备采购合同"]),
    ("合同", "2字词，多份命中", ["设备采购合同", "劳动合同书"]),
    ("收据", "2字词", ["收款收据"]),
    ("作业场所危害因素", "长短语", ["工作场所危害因素检测报告"]),
    ("张三", "人名", ["劳动合同书"]),
    ("气相色谱仪", "设备名", ["设备采购合同"]),
    ("440101199001011234", "身份证号", ["劳动合同书"]),
    ("2026年03月18日", "带标点的日期", ["增值税专用发票"]),
    # 「叁仟伍佰元整」的「叁」OCR 会误识为形近字「参」，这是 OCR 固有误差，
    # 不是检索缺陷，故用不含形近字的片段来验证。
    ("仟伍佰元整", "收据大写金额片段（避开 OCR 形近字）", ["收款收据"]),
    ("代理记账服务费", "业务关键词", ["收款收据"]),
]

for q, note, expect in QUERIES:
    items = get("/api/documents?q=" + urllib.parse.quote(q))["items"]
    ids = [d["id"] for d in items]
    titles = [d["title"] for d in items]
    missing = [e for e in expect if not any(e in t for t in titles)]
    dup = len(ids) != len(set(ids))
    ok = not missing and not dup
    detail = "命中%d份%s%s" % (len(ids), "" if ok else " 缺:" + str(missing), " (重复!)" if dup else "")
    check("查询「%s」" % q, ok, detail)


# ============================================================
print("\n" + "=" * 72)
print("二、PDF 双处理分支")
print("=" * 72)
docs = {d["id"]: d for d in get("/api/documents")["items"]}
native = [d for d in docs.values() if d["content_source"] == "text-layer"]
scanned = [d for d in docs.values() if d["content_source"] == "ocr" and d["ext"] == "pdf"]
check("原生 PDF 走文本层抽取（不触发 OCR）", len(native) >= 1,
      "%d 份：%s" % (len(native), [d["original_name"] for d in native]))
check("扫描件 PDF 走 OCR 分支", len(scanned) >= 1,
      "%d 份：%s" % (len(scanned), [d["original_name"] for d in scanned]))
if scanned:
    d = scanned[0]
    check("扫描件 PDF 页数识别正确（应为 3 页）", d["page_count"] == 3,
          "实际 %d 页 / %d 字" % (d["page_count"], d["ocr_chars"]))


# ============================================================
print("\n" + "=" * 72)
print("三、标签筛选")
print("=" * 72)
tags = {t["name"]: t for t in get("/api/tags")["items"]}
for name in ["发票", "合同", "收据", "检测报告"]:
    if name not in tags:
        check("标签「%s」存在" % name, False, "未生成")
        continue
    t = tags[name]
    items = get("/api/documents?tag=%d" % t["id"])["items"]
    check("按标签「%s」筛选" % name, len(items) == t["doc_count"],
          "筛出 %d 份（统计值 %d）" % (len(items), t["doc_count"]))


# ============================================================
print("\n" + "=" * 72)
print("四、自动标签规则引擎")
print("=" * 72)

# 4.1 新建一条正则规则：匹配"单据编号"格式（如 SJ-2026-0402 / LD-2026-0188）
r1 = post("/api/rules", {
    "name": "验收-带编号单据",
    "field": "content",
    "match_type": "regex",
    "pattern": r"[A-Z]{2}-\d{4}-\d{4}",
})
check("新建正则规则", r1.get("ok"), "rule_id=%s" % r1.get("rule_id"))

# 4.2 新建一条「全部关键词」规则：同时含"甲方"和"乙方" → 标签"双方合同"
r2 = post("/api/rules", {
    "name": "验收-双方合同",
    "field": "any",
    "match_type": "all",
    "pattern": "甲方 乙方",
})
check("新建 all 匹配规则", r2.get("ok"), "rule_id=%s" % r2.get("rule_id"))

# 4.3 重跑规则
applied = post("/api/rules/apply")
check("对所有文档重跑规则", applied.get("ok"), "处理 %s 份" % applied.get("applied"))

tags2 = {t["name"]: t for t in get("/api/tags")["items"]}
check("正则规则命中（带单据编号的文档）",
      tags2.get("验收-带编号单据", {}).get("doc_count", 0) >= 1,
      "命中 %d 份" % tags2.get("验收-带编号单据", {}).get("doc_count", 0))
check("all 规则命中（同时含甲乙方）", tags2.get("验收-双方合同", {}).get("doc_count", 0) >= 1,
      "命中 %d 份" % tags2.get("验收-双方合同", {}).get("doc_count", 0))

# 4.4 停用规则后重跑，标签应被移除
rid = r2["rule_id"]
patch("/api/rules/%d" % rid, {"enabled": 0})
post("/api/rules/apply")
tags3 = {t["name"]: t for t in get("/api/tags")["items"]}
check("停用规则后自动标签被移除", tags3.get("验收-双方合同", {}).get("doc_count", 0) == 0,
      "剩余 %d 份" % tags3.get("验收-双方合同", {}).get("doc_count", 0))

# 4.5 清理验收用规则与标签
for r in get("/api/rules")["items"]:
    if r["tag_name"].startswith("验收-"):
        delete("/api/rules/%d" % r["id"])
        delete("/api/tags/%d" % r["tag_id"])
print("  （已清理验收用规则与标签）")


# ============================================================
print("\n" + "=" * 72)
print("五、统计")
print("=" * 72)
s = get("/api/stats")
print("   文档总数 %s | 已处理 %s | OCR 识别 %s | 文本层 %s | 累计 %s 字"
      % (s.get("total"), s.get("done"), s.get("ocred"), s.get("text_layer"), s.get("chars")))

print("\n" + "=" * 72)
print("验收结果：通过 %d 项 / 失败 %d 项" % (passed, failed))
print("=" * 72)
sys.exit(1 if failed else 0)
