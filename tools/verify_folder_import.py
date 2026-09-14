# -*- coding: utf-8 -*-
"""文件夹扫描导入的端到端验收（API 级）——含测试文件夹自建。"""
import json
import os
import shutil
import urllib.request

from PIL import Image, ImageDraw

BASE = "http://127.0.0.1:8765"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(ROOT, "tools", "foldertest")
SAMPLES = os.path.join(ROOT, "samples")

results = []
def check(name, ok, extra=""):
    results.append(ok)
    print(("[通过] " if ok else "[失败] ") + name + (("  " + extra) if extra else ""))

def post(path, payload):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))

# ---------- 0. 搭建测试文件夹 ----------
sub = os.path.join(FOLDER, "子文件夹")
os.makedirs(sub, exist_ok=True)
shutil.copy(os.path.join(SAMPLES, "01_增值税专用发票.png"), os.path.join(FOLDER, "重复_已有发票.png"))
shutil.copy(os.path.join(SAMPLES, "02_收款收据.png"), os.path.join(sub, "重复_收据.png"))

def make_new_png(path, text):
    img = Image.new("RGB", (640, 300), (245, 246, 250))
    d = ImageDraw.Draw(img)
    d.text((40, 130), text, fill=(30, 30, 30))
    img.save(path)

make_new_png(os.path.join(FOLDER, "新文件A.png"), "FOLDER-IMPORT-TEST-A 2026-09-04")
make_new_png(os.path.join(FOLDER, "新文件B.png"), "FOLDER-IMPORT-TEST-A 2026-09-04")  # 与A同内容
with open(os.path.join(FOLDER, "备注.txt"), "w", encoding="utf-8") as f:
    f.write("不支持的文件")
print("测试文件夹就绪\n")

# ---------- 1. 扫描 ----------
scan = post("/api/scan-folder", {"path": FOLDER})
items = scan["items"]
check("扫描成功", "items" in scan, "共 %d 个文件，其他跳过 %d 个" % (len(items), scan["skipped_others"]))
by_status = {}
for i in items:
    by_status.setdefault(i["status"], []).append(i["rel"])
check("与已有文档重复被标记 2 个", len(by_status.get("dup-db", [])) == 2,
      str(by_status.get("dup-db", [])))
check("批内重复被标记 1 个", len(by_status.get("dup-batch", [])) == 1,
      str(by_status.get("dup-batch", [])))
check("新文件被标记 1 个", len(by_status.get("new", [])) == 1,
      str(by_status.get("new", [])))
check("不支持的 .txt 未列入", "备注.txt" not in json.dumps(items, ensure_ascii=False))

# ---------- 2. 导入（故意把全部 4 个路径都传，重复的应在导入时被拦） ----------
r = post("/api/import-folder", {"paths": [i["path"] for i in items]})
check("只导入 1 个新文件", len(r["created"]) == 1, "created=%s" % r["created"])
check("重复的在导入时被跳过 3 个", len(r["skipped"]) == 3,
      "; ".join("%s(%s)" % (s["path"], s["reason"][:22]) for s in r["skipped"]))

# ---------- 3. 确认新文档已入库 ----------
with urllib.request.urlopen(BASE + "/api/documents", timeout=30) as resp:
    docs = json.loads(resp.read().decode("utf-8"))
hit = [x for x in docs["items"] if x["id"] in r["created"]]
check("新文档已入库", len(hit) == 1,
      "%s 状态=%s" % (hit[0]["original_name"] if hit else "-", hit[0]["status"] if hit else "-"))

# ---------- 4. 清理测试文档 ----------
ok_del = True
for did in r["created"]:
    req = urllib.request.Request(BASE + "/api/documents/%d" % did, method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as resp:
        ok_del = ok_del and json.loads(resp.read().decode()).get("ok")
check("测试文档已清理", ok_del)

print()
print("验收结果：通过 %d / 失败 %d" % (sum(results), len(results) - sum(results)))
