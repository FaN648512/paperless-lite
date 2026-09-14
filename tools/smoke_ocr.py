# -*- coding: utf-8 -*-
"""OCR 冒烟测试 v2：验证识别质量，并对比不同分辨率下的速度，为后端参数定标准。"""
import os
import sys
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(os.path.dirname(HERE), "samples")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rapidocr_onnxruntime import RapidOCR

print("初始化引擎 ...")
t0 = time.time()
engine = RapidOCR()
print("引擎就绪 %.2fs\n" % (time.time() - t0))

src = os.path.join(SAMPLES, "01_增值税专用发票.png")
if not os.path.exists(src):
    src = os.path.join(HERE, "samples", "01_增值税专用发票.png")
print("测试图:", src)


def ocr(path):
    t0 = time.time()
    result, elapse = engine(path)
    return result, time.time() - t0, elapse


def to_text(result):
    """兼容 (box, text, score) 与 (box, score, text) 两种返回顺序。"""
    lines = []
    for item in result or []:
        if len(item) >= 3:
            a, b = item[1], item[2]
            text = a if isinstance(a, str) else b
            score = b if isinstance(b, (int, float)) else a
            lines.append((str(text), float(score) if isinstance(score, (int, float)) else 0.0))
    return lines


print("\n" + "=" * 62)
print("【测试 1】原始尺寸 1240x1754")
result, total, elapse = ocr(src)
lines = to_text(result)
print("耗时 %.2fs  行数 %d" % (total, len(lines)))
full_orig = "\n".join(t for t, _ in lines)

print("\n--- 识别全文 ---")
for t, s in lines:
    print("  [%.2f] %s" % (s, t))

print("\n--- 关键词命中检查 ---")
for kw in ["增值税专用发票", "发票号码", "开票日期", "蓝盾检测", "12800", "价税合计"]:
    print("  %-14s -> %s" % (kw, "命中" if kw in full_orig else "未命中"))

# ---- 尺寸对比 ----
print("\n" + "=" * 62)
print("【测试 2】不同长边尺寸的速度对比")
img = Image.open(src).convert("RGB")
for side in [1600, 1200, 960, 800]:
    w, h = img.size
    ratio = side / float(max(w, h))
    if ratio >= 1:
        print("  长边 %-5d 跳过（原图更小）" % side)
        continue
    resized = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    tmp = os.path.join(HERE, "_tmp_%d.png" % side)
    resized.save(tmp)
    r, tt, _ = ocr(tmp)
    ls = to_text(r)
    txt = "\n".join(t for t, _ in ls)
    hit = sum(1 for kw in ["增值税专用发票", "发票号码", "开票日期", "蓝盾检测", "12800", "价税合计"] if kw in txt)
    print("  长边 %-5d 耗时 %6.2fs  行数 %3d  关键命中 %d/6" % (side, tt, len(ls), hit))
    try:
        os.remove(tmp)
    except Exception:
        pass

print("\n完成。")
