# -*- coding: utf-8 -*-
"""端到端验收：中文全文搜索（含 2 字词 trigram 兜底路径）。"""
import json
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8765"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


QUERIES = [
    # (查询词, 说明, 预期命中的文档标题关键词)
    ("发票", "2字中文词（trigram 无效，走 LIKE 兜底）", ["增值税专用发票"]),
    ("蓝盾检测", "3字词（trigram 主路径）", ["增值税专用发票", "工作场所危害因素检测报告"]),
    ("工作场所", "4字词", ["增值税专用发票", "劳动合同书", "工作场所危害因素检测报告"]),
    ("12800", "数字金额", ["增值税专用发票"]),
    ("星海电子", "长词短语", ["增值税专用发票", "设备采购合同"]),
    ("合同", "2字词，应命中多份合同", ["设备采购合同", "劳动合同书"]),
    ("收据", "2字词", ["收款收据"]),
    ("作业场所危害因素", "长短语", ["工作场所危害因素检测报告"]),
    ("张三", "人名（劳动合同乙方）", ["劳动合同书"]),
    ("气相色谱仪", "设备名（采购合同）", ["设备采购合同"]),
]

print("=" * 70)
print("搜索验收")
print("=" * 70)

passed = failed = 0
for q, note, expect in QUERIES:
    data = get("/api/documents?q=" + urllib.parse.quote(q))
    titles = [d["title"] for d in data["items"]]
    ok = True
    for e in expect:
        if not any(e in t for t in titles):
            ok = False
    flag = "通过" if ok else "不通过"
    if ok:
        passed += 1
    else:
        failed += 1
    print("\n查询「%s」  %s  命中 %d 份  [%s]" % (q, flag, len(titles), note))
    for t in titles:
        print("    - %s" % t)
    if not ok:
        print("    预期包含：%s" % expect)

print("\n" + "=" * 70)
print("通过 %d / 失败 %d" % (passed, failed))
print("=" * 70)

print("\n标签统计：")
for t in get("/api/tags")["items"]:
    print("   %-10s %2d 份  规则 %d 条" % (t["name"], t["doc_count"], t["rule_count"]))

print("\n总体统计：")
s = get("/api/stats")
for k, v in s.items():
    print("   %-12s %s" % (k, v))
