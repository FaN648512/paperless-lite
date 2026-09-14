# -*- coding: utf-8 -*-
"""
文档处理管线：把上传的 PDF / 图片变成可搜索文本 + 缩略图。

处理策略（与 paperless-ngx 一致）：
    PDF  -> 逐页抽文本层；某页文本过少（扫描件）则渲染成图再 OCR
    图片 -> 直接 OCR

性能考量（本机构型：Pentium G840 双核 / 3.9GB 内存）：
    * OCR 前统一把长边压到 OCR_MAX_SIDE=960，实测耗时从 16.6s 降到 10.6s，
      且中文识别准确率不变（见 tools/smoke_ocr.py 对比数据）。
    * OCR 引擎全局单例懒加载，避免每篇文档重复加载上百 MB 模型。
    * PDF 渲染 DPI=150，首页同时生成缩略图，避免二次渲染。
"""
import os
import re
import sys
import threading
import uuid

from PIL import Image
import numpy as np

try:  # PyMuPDF 1.28 起推荐用 pymupdf 包名
    import pymupdf as fitz
except ImportError:  # 兼容旧版本
    import fitz

OCR_MAX_SIDE = 960          # OCR 输入图长边上限
THUMB_WIDTH = 360           # 缩略图宽度
RENDER_DPI = 150            # PDF 渲染 DPI
TEXT_LAYER_MIN_CHARS = 20   # 低于此字符数判定为扫描页，改走 OCR

_engine = None
_engine_lock = threading.Lock()


def get_engine():
    """OCR 引擎单例（懒加载，线程安全）。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR
                try:
                    _engine = RapidOCR(intra_op_num_threads=2)
                except TypeError:
                    _engine = RapidOCR()
    return _engine


def _resize_for_ocr(img):
    """把图片长边压到 OCR_MAX_SIDE，短边同比缩放（保持比例）。"""
    w, h = img.size
    longest = max(w, h)
    if longest <= OCR_MAX_SIDE:
        return img
    ratio = OCR_MAX_SIDE / float(longest)
    return img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)


def _ocr_image(img, log=None):
    """
    对 PIL 图片做 OCR，返回按行拼接的文本。

    注意：RapidOCR 只接受 str / Path / bytes / np.ndarray 四种输入，
    其 load_img 对路径的处理是 np.array(Image.open(path))，即 RGB 顺序的
    numpy 数组。这里保持与之完全一致（RGB，非 BGR），直接传 PIL 对象会报错。
    """
    engine = get_engine()
    if engine is None:
        raise RuntimeError("OCR 引擎未就绪")
    img = _resize_for_ocr(img.convert("RGB"))
    arr = np.array(img)          # RGB uint8
    result, _ = engine(arr)
    return _result_to_text(result)


def _result_to_text(result):
    """兼容 rapidocr 不同版本的返回顺序：(box, text, score) / (box, score, text)。"""
    lines = []
    for item in result or []:
        if len(item) < 3:
            continue
        a, b = item[1], item[2]
        text = a if isinstance(a, str) else (b if isinstance(b, str) else "")
        if text:
            lines.append(text.strip())
    return "\n".join(lines)


def _clean_text(t):
    """轻度清洗：合并多余空白行，保留段落结构。"""
    t = (t or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _save_thumb(img, thumb_path):
    try:
        w, h = img.size
        ratio = THUMB_WIDTH / float(w)
        thumb = img.convert("RGB").resize((THUMB_WIDTH, max(1, int(h * ratio))), Image.LANCZOS)
        thumb.save(thumb_path, "JPEG", quality=78)
        return True
    except Exception:
        return False


def _guess_title(text, original_name):
    """取正文前几行里最长的一行做标题；都不像样就退回文件名（去扩展名）。"""
    for line in (text or "").splitlines()[:6]:
        line = line.strip()
        # 跳过 Office 解析产生的分节标记，如【工作表：Sheet1】【第 3 页】
        if line.startswith("【") and line.endswith("】"):
            continue
        if 4 <= len(line) <= 60:
            return line
    return os.path.splitext(original_name or "未命名文档")[0]


def process_image(src_path, thumb_path, log=None):
    """处理图片文件。返回 (content, content_source, page_count)"""
    with Image.open(src_path) as im:
        im.load()
        img = im.convert("RGB")
        _save_thumb(img, thumb_path)
        text = _ocr_image(img, log=log)
    return _clean_text(text), "ocr", 1


def process_pdf(src_path, thumb_path, log=None):
    """处理 PDF。返回 (content, content_source, page_count)"""
    doc = fitz.open(src_path)
    page_count = len(doc)
    parts = []
    used_text_layer = False
    used_ocr = False
    thumb_done = False

    try:
        for i, page in enumerate(doc):
            page_text = (page.get_text("text") or "").strip()

            if len(page_text) >= TEXT_LAYER_MIN_CHARS:
                parts.append(page_text)
                used_text_layer = True
                if log:
                    log("第 %d/%d 页：命中文本层（%d 字），跳过 OCR"
                        % (i + 1, page_count, len(page_text)))
                if not thumb_done:
                    pix = page.get_pixmap(dpi=72)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    _save_thumb(img, thumb_path)
                    thumb_done = True
            else:
                if log:
                    log("第 %d/%d 页：无文本层，渲染并 OCR ..." % (i + 1, page_count))
                pix = page.get_pixmap(dpi=RENDER_DPI)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                if not thumb_done:
                    _save_thumb(img, thumb_path)
                    thumb_done = True
                parts.append(_ocr_image(img, log=log))
                used_ocr = True
    finally:
        doc.close()

    source = "mixed" if (used_text_layer and used_ocr) else (
        "ocr" if used_ocr else "text-layer")
    return _clean_text("\n".join(parts)), source, page_count


OFFICE_MAX_ROWS = 20000     # 单个 Excel 工作表最多读取行数（防内存爆）
OFFICE_MAX_SHEETS = 100     # 最多处理工作表数
OFFICE_MAX_CHARS = 300000   # 正文截断上限


# --------------------------------------------------------------------------
# Office 文档（Excel / PowerPoint）
# --------------------------------------------------------------------------
def _cell_text(v):
    """把单元格值转成干净字符串；浮点整数去掉小数点尾巴。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _rows_to_lines(rows):
    """二维行 -> 文本行（跳过全空行，单元格用空格连接）。"""
    out = []
    for row in rows:
        cells = [_cell_text(v) for v in row]
        line = " ".join([c for c in cells if c])
        if line:
            out.append(line)
    return out


def _clip(parts, limit=OFFICE_MAX_CHARS):
    text = "\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + "\n…（内容过长已截断）"
    return text


def _read_xlsx(path):
    """现代 Excel：openpyxl 只读模式，边读边丢，内存占用最低。"""
    from openpyxl import load_workbook

    parts = []
    sheets = 0
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            sheets += 1
            if sheets > OFFICE_MAX_SHEETS:
                break
            lines = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= OFFICE_MAX_ROWS:
                    lines.append("…（本表超过 %d 行，已截断）" % OFFICE_MAX_ROWS)
                    break
                cells = [_cell_text(v) for v in row]
                line = " ".join([c for c in cells if c])
                if line:
                    lines.append(line)
            if lines:
                parts.append("【工作表：%s】" % ws.title)
                parts.extend(lines)
    finally:
        wb.close()
    return parts, sheets


def _read_xls(path):
    """老版 Excel（.xls，BIFF 格式）：xlrd 2.x 只支持 .xls。"""
    import xlrd

    book = xlrd.open_workbook(path)
    parts = []
    for sh in book.sheets()[:OFFICE_MAX_SHEETS]:
        lines = []
        for r in range(min(sh.nrows, OFFICE_MAX_ROWS)):
            cells = [_cell_text(sh.cell_value(r, c)) for c in range(sh.ncols)]
            line = " ".join([c for c in cells if c])
            if line:
                lines.append(line)
        if sh.nrows > OFFICE_MAX_ROWS:
            lines.append("…（本表超过 %d 行，已截断）" % OFFICE_MAX_ROWS)
        if lines:
            parts.append("【工作表：%s】" % sh.name)
            parts.extend(lines)
    return parts, book.nsheets


def _shape_text(shape):
    """递归取形状里的文字（含组合形状、表格）。"""
    out = []
    if getattr(shape, "has_text_frame", False):
        t = (shape.text_frame.text or "").strip()
        if t:
            out.append(t)
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            cells = [(_cell_text(c.text) or "").strip() for c in row.cells]
            line = " ".join([c for c in cells if c])
            if line:
                out.append(line)
    if shape.shape_type is not None and getattr(shape, "shapes", None) is not None:
        try:
            for sub in shape.shapes:
                out.extend(_shape_text(sub))
        except Exception:
            pass
    return out


def _read_pptx(path):
    from pptx import Presentation

    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        lines = []
        for shape in slide.shapes:
            lines.extend(_shape_text(shape))
        if lines:
            parts.append("【第 %d 页】" % i)
            parts.extend(lines)
    return parts, len(prs.slides)


def _read_ppt_legacy(path):
    """
    老版 .ppt（二进制 OLE）尽力提取。

    没有纯 Python 库能解析 .ppt，这里直接扫原始字节：幻灯片正文在二进制里
    以 TextCharsAtom(UTF-16LE) / TextBytesAtom(单字节) 形式存放，按可打印
    字符连续段抽取即可拿到绝大部分文字。属于兜底方案，准确率低于 .pptx。
    """
    with open(path, "rb") as f:
        raw = f.read()

    def ok(ch):
        o = ord(ch)
        if o in (0x0A, 0x0D, 0x09):
            return True
        if 0x20 <= o <= 0x7E:
            return True
        # 中日韩统一表意文字 + 常用中文标点
        if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            return True
        return False

    chunks = []
    # UTF-16LE 扫描（偶字节对齐 + 高字节为 0 的 ASCII 也算）
    buf = []
    for i in range(0, len(raw) - 1, 2):
        try:
            ch = raw[i:i + 2].decode("utf-16-le")
        except Exception:
            if buf:
                chunks.append("".join(buf)); buf = []
            continue
        if len(ch) == 1 and ok(ch):
            buf.append(ch)
        else:
            if len(buf) >= 4:
                chunks.append("".join(buf))
            buf = []
    if len(buf) >= 4:
        chunks.append("".join(buf))

    # 去重保序
    seen, parts = set(), []
    for c in chunks:
        c = c.strip()
        if len(c) >= 2 and c not in seen:
            seen.add(c)
            parts.append(c)
    return parts, 0


def _save_office_thumb(ext, thumb_path):
    """Office 文件没有可渲染页面，生成一张带格式标识的占位缩略图。"""
    try:
        from PIL import ImageDraw, ImageFont

        bg, label = ("#1D6F42", "XLS") if ext in ("xls", "xlsx", "xlsm") else ("#C43E1C", "PPT")
        if ext == "xls":
            label = "XLS"
        img = Image.new("RGB", (THUMB_WIDTH, int(THUMB_WIDTH * 1.28)), bg)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 76)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        draw.text(((img.width - bbox[2]) / 2, (img.height - bbox[3]) / 2 - 10),
                  label, fill="white", font=font)
        img.save(thumb_path, "JPEG", quality=80)
        return True
    except Exception:
        return False


def process_office(src_path, thumb_path, log=None):
    """处理 Excel / PowerPoint，返回 (content, 'office', 页数或表数)。"""
    ext = os.path.splitext(src_path)[1].lstrip(".").lower()
    if ext in ("xlsx", "xlsm"):
        parts, count = _read_xlsx(src_path)
        kind = "Excel"
    elif ext == "xls":
        parts, count = _read_xls(src_path)
        kind = "Excel(旧版)"
    elif ext == "pptx":
        parts, count = _read_pptx(src_path)
        kind = "PowerPoint"
    elif ext == "ppt":
        parts, count = _read_ppt_legacy(src_path)
        kind = "PowerPoint(旧版)"
        if log:
            log("老版 .ppt 为尽力提取，建议另存为 .pptx 后重新导入以获得更好效果")
    else:
        raise ValueError("暂不支持的 Office 类型：%s" % ext)

    _save_office_thumb(ext, thumb_path)
    if log:
        log("%s 解析完成：%d 个表/页，%d 字" % (kind, count, len("\n".join(parts))))
    return _clean_text(_clip(parts)), "office", max(count, 1)


OFFICE_EXT = {"xlsx", "xlsm", "xls", "pptx", "ppt"}


def run_pipeline(src_path, ext, thumb_path, log=None):
    """按扩展名分发处理，统一返回 (content, content_source, page_count)。"""
    ext = (ext or "").lower()
    if ext == "pdf":
        return process_pdf(src_path, thumb_path, log=log)
    if ext in ("png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"):
        return process_image(src_path, thumb_path, log=log)
    if ext in OFFICE_EXT:
        return process_office(src_path, thumb_path, log=log)
    raise ValueError("暂不支持的文件类型：%s" % ext)


def new_stored_name(original_name):
    """生成存储文件名（避免中文/特殊字符带来的路径问题）。"""
    ext = os.path.splitext(original_name or "")[1].lstrip(".").lower() or "bin"
    return "%s.%s" % (uuid.uuid4().hex, ext)
