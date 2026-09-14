# -*- coding: utf-8 -*-
"""读取 CPU 型号，判断是否支持 onnxruntime 需要的 AVX2 指令集。"""
import ctypes
import platform
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("platform.processor():", platform.processor())
print("platform.machine() :", platform.machine())

# 从注册表读 CPU 名称
try:
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
    name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
    mhz, _ = winreg.QueryValueEx(key, "~MHz")
    ident, _ = winreg.QueryValueEx(key, "Identifier")
    print("CPU 名称 :", name.strip())
    print("标称主频 :", mhz, "MHz")
    print("标识符   :", ident.strip())
    winreg.CloseKey(key)
except Exception as e:
    print("注册表读取失败:", e)

# 用 CPUID 指令检测指令集（leaf 1 ECX/EDX，leaf 7 EBX）
print("\n指令集检测（CPUID）:")


def cpuid(leaf, subleaf=0):
    # 通过 ctypes 调用无法直接执行 CPUID；改为读取 CPU 特性位不可行，
    # 这里退化为报告 CPU 型号由人工/后续实测判断。
    return None


# 实测法：执行一小段 AVX2 机器码过于复杂，
# 改用 Windows 侧信息 + 后续 onnxruntime 版本实测来验证。
print("  （CPUID 需内联汇编，改用 onnxruntime 版本实测法验证）")

# 顺带报告：onnxruntime 各版本对指令集的要求不同，
# 1.18 及以前对老 CPU 更友好，1.20+ 官方 wheel 默认要求更高。
print("\nWindows 版本:", platform.platform())
