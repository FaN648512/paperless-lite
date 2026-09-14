# -*- coding: utf-8 -*-
"""
paperless-lite 一键启动器
========================
用法：直接双击本文件（启动服务.pyw）即可。
它会：
  1. 检查服务是否已在运行（127.0.0.1:8765）；
  2. 没运行就用隔离 venv 的 Python 启动「服务守护进程」
     （带独立控制台窗口，服务崩溃会自动重启）；
  3. 等服务就绪后自动用默认浏览器打开界面。
关闭服务：直接关掉那个守护进程控制台窗口即可。
"""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "服务守护.py")
URL = "http://127.0.0.1:8765"

# 用哪个 Python 跑服务：环境变量 PAPERLITE_PY > local_settings.py 的 VENV_PY > 当前解释器
try:
    import local_settings as _local
except ImportError:
    _local = None

VENV_PY = (os.environ.get("PAPERLITE_PY")
           or (getattr(_local, "VENV_PY", "") if _local else "")
           or sys.executable)


def server_alive(timeout=2):
    try:
        with urllib.request.urlopen(URL + "/api/meta", timeout=timeout):
            return True
    except Exception:
        return False


def main():
    if server_alive():
        print("服务已在运行，直接打开界面。")
    else:
        if not os.path.exists(VENV_PY):
            print("未找到虚拟环境 Python：", VENV_PY)
            print("请先在 WorkBuddy 里让我重新安装依赖。")
            input("按回车键退出...")
            sys.exit(1)
        # 新开一个控制台窗口跑守护进程，关掉窗口即停止服务
        subprocess.Popen(
            [VENV_PY, GUARD],
            cwd=HERE,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        print("正在启动服务", end="", flush=True)
        for _ in range(60):  # 最多等 30 秒
            if server_alive():
                print(" 就绪！")
                break
            print(".", end="", flush=True)
            time.sleep(0.5)
        else:
            print("\n启动超时，请看控制台窗口里的报错信息。")
            input("按回车键退出...")
            sys.exit(1)
    webbrowser.open(URL)


if __name__ == "__main__":
    # pythonw（无窗口双击）下没有控制台，print 可能报错，做兜底
    try:
        main()
    except Exception as e:
        try:
            print("出错：", e)
            input("按回车键退出...")
        except Exception:
            pass
