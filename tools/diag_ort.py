# -*- coding: utf-8 -*-
"""定位 onnxruntime DLL 初始化失败的根因。"""
import ctypes
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=" * 62)
print("A) 当前内存水位")
try:
    class MEM(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEM(); m.dwLength = ctypes.sizeof(MEM)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    print("   总计 %.2f GB / 可用 %.2f GB / 占用 %d%%"
          % (m.ullTotalPhys / 1024**3, m.ullAvailPhys / 1024**3, m.dwMemoryLoad))
except Exception as e:
    print("   读取失败:", e)

print("\nB) 关键 VC++ 运行库是否存在")
for dll in ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "msvcp140_1.dll", "msvcp140_2.dll"]:
    p = os.path.join(r"C:\Windows\System32", dll)
    print("   %-22s %s" % (dll, "存在" if os.path.exists(p) else "缺失"))

print("\nC) 逐包 import 测试")
for mod in ["numpy", "cv2", "onnxruntime", "PIL", "pymupdf", "rapidocr_onnxruntime"]:
    try:
        __import__(mod)
        print("   %-24s OK" % mod)
    except Exception as e:
        print("   %-24s FAIL: %s: %s" % (mod, type(e).__name__, str(e)[:110]))

print("\nD) 直接 ctypes 加载 onnxruntime 的 pybind dll")
try:
    import onnxruntime as ort
    base = os.path.dirname(ort.__file__)
    print("   onnxruntime 包目录:", base)
    print("   版本:", ort.__version__)
except Exception:
    base = None
    print("   onnxruntime 包路径无法获取（import 已失败）")

if base:
    capi = os.path.join(base, "capi")
    for f in sorted(os.listdir(capi)) if os.path.isdir(capi) else []:
        if f.endswith(".dll"):
            print("   capi/%s  (%.1f MB)" % (f, os.path.getsize(os.path.join(capi, f)) / 1024 / 1024))

print("\nE) 尝试用 SetDllDirectory 预加载 vcruntime 后再 import")
try:
    ctypes.windll.kernel32.SetDllDirectoryW(r"C:\Windows\System32")
    import onnxruntime
    print("   import onnxruntime -> OK, 版本", onnxruntime.__version__)
except Exception as e:
    print("   import onnxruntime -> FAIL:", type(e).__name__, str(e)[:140])

print("\n完成。")
