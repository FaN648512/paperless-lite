# -*- coding: utf-8 -*-
"""
Office 导入 + 格式分类 + 扫描空结果提示 的服务端验收
运行前需先启动服务（python app.py）
"""
import json
import os
import shutil
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICE_DIR = os.path.join(HERE, "tools", "officetest")
EMPTY_DIR = os.path.join(HERE, "tools", "scantest_empty")
JUNK_DIR = os.path.join(HERE, "tools", "scantest_junk")

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [通过] %s %s" % (name, extra))
    else:
        FAIL += 1
        print("  [失败] %s %s" % (name, extra))


def post(path, payload):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))


def get(path):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=60).read().decode("utf-8"))


def main():
    print("=== 1. 扫描：正常文件夹（4 个 Office 文件） ===")
    res = post("/api/scan-folder", {"path": OFFICE_DIR})
    items = res.get("items", [])
    check("扫描到 4 个可导入文件", len(items) == 4,
          "实际 %d 个：%s" % (len(items), [i["name"] for i in items]))
    check("全部标记为新文件", all(i["status"] == "new" for i in items))

    print("=== 2. 扫描：空文件夹（应返回 0 项 + 总文件数 0，供前端弹窗） ===")
    os.makedirs(EMPTY_DIR, exist_ok=True)
    res2 = post("/api/scan-folder", {"path": EMPTY_DIR})
    check("空文件夹返回 0 个可导入文件", len(res2.get("items", [])) == 0)
    check("返回 total_files=0（前端据此提示“文件夹里没有任何文件”）",
          res2.get("total_files") == 0, "total_files=%s" % res2.get("total_files"))

    print("=== 3. 扫描：只有不支持的格式（应返回 0 项 + 跳过数） ===")
    shutil.rmtree(JUNK_DIR, ignore_errors=True)
    os.makedirs(JUNK_DIR, exist_ok=True)
    open(os.path.join(JUNK_DIR, "说明.txt"), "w", encoding="utf-8").write("不支持")
    open(os.path.join(JUNK_DIR, "材料.docx"), "wb").write(b"fake docx")
    res3 = post("/api/scan-folder", {"path": JUNK_DIR})
    check("返回 0 个可导入文件", len(res3.get("items", [])) == 0)
    check("total_files=2 且 skipped_others=2（前端据此提示“有文件但格式不支持”）",
          res3.get("total_files") == 2 and res3.get("skipped_others") == 2,
          "total=%s skipped=%s" % (res3.get("total_files"), res3.get("skipped_others")))

    print("=== 4. 导入 4 个 Office 文件 ===")
    imp = post("/api/import-folder", {"paths": [i["path"] for i in items]})
    created = imp.get("created", [])
    check("4 个文件全部导入成功", len(created) == 4, "created=%s" % created)

    print("=== 5. 等待处理完成 ===")
    for _ in range(60):
        docs = get("/api/documents")
        pending = [d for d in docs["items"] if d["id"] in created
                   and d["status"] in ("pending", "processing")]
        if not pending:
            break
        time.sleep(2)
    ok_status = all(d["status"] == "done" for d in docs["items"] if d["id"] in created)
    check("全部处理完成", ok_status,
          "状态：%s" % [d["status"] for d in docs["items"] if d["id"] in created])
    check("来源标记为 office（文件解析）",
          all(d["content_source"] == "office" for d in docs["items"] if d["id"] in created))

    print("=== 6. 左侧栏格式分类统计 ===")
    fmts = {f["key"]: f["count"] for f in get("/api/meta")["formats"]}
    check("格式统计含 Excel >= 2", fmts.get("excel", 0) >= 2, "excel=%s" % fmts.get("excel"))
    check("格式统计含 PPT >= 2", fmts.get("ppt", 0) >= 2, "ppt=%s" % fmts.get("ppt"))
    check("格式统计含 PDF / 图片（老数据未受影响）",
          fmts.get("pdf", 0) >= 1 and fmts.get("image", 0) >= 1,
          "pdf=%s image=%s" % (fmts.get("pdf"), fmts.get("image")))

    print("=== 7. 文档 format 字段正确 ===")
    byname = {d["original_name"]: d for d in docs["items"]}
    check("xlsx -> format=excel", byname["01_检测项目清单.xlsx"]["format"] == "excel")
    check("xls  -> format=excel", byname["02_培训计划.xls"]["format"] == "excel")
    check("pptx -> format=ppt", byname["03_安全生产培训.pptx"]["format"] == "ppt")
    check("ppt  -> format=ppt", byname["04_劳动合同管理.ppt"]["format"] == "ppt")

    print("=== 8. 全文搜索能命中 Office 内容 ===")
    for kw, expect_name in [("喷漆车间", "01_检测项目清单.xlsx"),
                            ("蓝盾检测", "01_检测项目清单.xlsx"),
                            ("作业场所危害因素识别与防护", "02_培训计划.xls"),
                            ("安全生产培训课件", "03_安全生产培训.pptx"),
                            ("合同期限三年", "04_劳动合同管理.ppt")]:
        r = get("/api/documents?q=" + urllib.parse.quote(kw))
        hit = any(d["original_name"] == expect_name for d in r["items"])
        check("搜索「%s」命中《%s》" % (kw, expect_name), hit)

    print("=== 9. 清理测试文档 ===")
    for cid in created:
        req = urllib.request.Request("%s/api/documents/%d" % (BASE, cid), method="DELETE")
        urllib.request.urlopen(req, timeout=30)
    rest = get("/api/documents")
    check("测试文档已清理", all(d["id"] not in created for d in rest["items"]),
          "剩余 %d 份" % rest["total"])
    shutil.rmtree(EMPTY_DIR, ignore_errors=True)
    shutil.rmtree(JUNK_DIR, ignore_errors=True)

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    sys.exit(main())
