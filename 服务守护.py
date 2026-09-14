# -*- coding: utf-8 -*-
"""
服务守护进程 —— paperless-lite 稳定性保障
==========================================
由 启动服务.pyw 拉起（也可直接双击本文件）。
职责：启动 app.py 并盯着它——如果服务进程意外退出（崩溃 / 被系统回收），
3 秒后自动重启，控制台会打印每次重启记录。
停止服务：在本窗口按 Ctrl+C，或直接关掉本窗口。
"""
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
URL = "http://127.0.0.1:8765"

# 用哪个 Python 跑服务：环境变量 PAPERLITE_PY > local_settings.py 的 VENV_PY > 当前解释器
try:
    import local_settings as _local
except ImportError:
    _local = None

VENV_PY = (os.environ.get("PAPERLITE_PY")
           or (getattr(_local, "VENV_PY", "") if _local else "")
           or sys.executable)


def now():
    return datetime.now().strftime("%H:%M:%S")


def main():
    print("=" * 58)
    print("  服务守护进程已启动（崩溃会自动重启，按 Ctrl+C 停止）")
    print("  访问地址：%s" % URL)
    print("=" * 58)
    restarts = 0
    try:
        while True:
            proc = subprocess.Popen([VENV_PY, APP], cwd=HERE)
            print("[%s] 服务已启动 (进程 %d)" % (now(), proc.pid))
            try:
                proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
                raise
            restarts += 1
            print("[%s] 服务退出（退出码 %s），3 秒后第 %d 次自动重启..."
                  % (now(), proc.returncode, restarts))
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[%s] 守护进程已停止，服务关闭。" % now())


if __name__ == "__main__":
    main()
