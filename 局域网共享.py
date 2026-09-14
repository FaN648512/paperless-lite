# -*- coding: utf-8 -*-
"""
paperless-lite 局域网共享开关
=============================
作用：让同一公司网络里的同事，用浏览器访问你电脑上的文档库。

用法：双击本文件（会弹出黑色控制台窗口），按数字键选择：
        1 —— 开放局域网访问（加一条防火墙放行规则）
        2 —— 关闭局域网访问（删掉那条规则，恢复只有你自己能开）
        3 —— 查看当前状态
        0 —— 退出

注意：
  * 开放/关闭防火墙需要管理员权限，弹出「是否允许此应用更改设备」时点「是」。
  * 只是放行端口，能不能看到文档还取决于账号密码（在网页里设置）。
  * 用完记得选 2 关掉，别让库一直开着。
"""

import ctypes
import os
import socket
import subprocess
import sys

PORT = 8765
RULE_NAME = "paperless-lite-8765"      # 防火墙规则名（用英文，避免编码麻烦）

# 数据目录：与环境变量 / local_settings.py 保持一致（见 app.py 顶部说明）
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import local_settings as _local
except ImportError:
    _local = None

DATA_DIR = (os.environ.get("PAPERLITE_DATA")
            or (getattr(_local, "DATA_DIR", "") if _local else "")
            or os.path.join(HERE, "data"))


# ---------------------------------------------------------------- 基础工具
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin():
    """用管理员权限重新启动自己"""
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, '"%s"' % os.path.abspath(__file__), None, 1)


def sh(args):
    r = subprocess.run(args, capture_output=True)
    out = (r.stdout or b"") + (r.stderr or b"")
    return r.returncode, out.decode("gbk", errors="replace")


def rule_exists():
    code, out = sh(["netsh", "advfirewall", "firewall", "show", "rule", "name=" + RULE_NAME])
    return code == 0 and RULE_NAME in out


def port_listening():
    code, out = sh(["netstat", "-ano"])
    return (":%d" % PORT) in out and "LISTENING" in out


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for it in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(it)
    except Exception:
        pass
    return sorted(i for i in ips if not i.startswith("127.") and not i.startswith("169.254."))


def sep(title=""):
    print("\n" + "=" * 62)
    if title:
        print(" ", title)
        print("=" * 62)


# ---------------------------------------------------------------- 三个动作
def do_open():
    sep("开放局域网访问")
    if rule_exists():
        print("  防火墙规则已经存在了，不用重复添加。")
    else:
        code, out = sh([
            "netsh", "advfirewall", "firewall", "add", "rule",
            "name=" + RULE_NAME,
            "dir=in", "action=allow", "protocol=TCP",
            "localport=%d" % PORT,
            "profile=private,domain",       # 只在「专用/公司域」网络放行，公用网络（咖啡厅）不放行
            "description=paperless-lite 文档库共享端口",
        ])
        if code == 0:
            print("  ✅ 防火墙已放行 %d 端口（仅限专用/公司网络）" % PORT)
        else:
            print("  ❌ 添加规则失败（返回码 %d）：" % code)
            print(out)
            print("  常见原因：没用管理员权限运行，或公司组策略禁止改防火墙。")
            return
    ips = lan_ips()
    print("\n  同事访问地址（让他们用浏览器打开）：")
    if ips:
        for ip in ips:
            print("     http://%s:%d" % (ip, PORT))
    else:
        print("     （没检测到内网 IP，请确认网线/WiFi 已连上）")
    print("\n  前提：")
    print("     1) 服务必须是开着的 —— 双击「启动服务.pyw」")
    print("     2) 同事和你要在同一网段（比如都是 192.168.1.x）")
    print("     3) 同事第一次打开会要求设/输账号密码")


def do_close():
    sep("关闭局域网访问")
    if not rule_exists():
        print("  规则本来就不存在，已经是最安全的状态（只有本机 127.0.0.1 能访问）。")
        return
    code, out = sh(["netsh", "advfirewall", "firewall", "delete", "rule", "name=" + RULE_NAME])
    if code == 0:
        print("  ✅ 已删除防火墙规则，同事现在连不进来了。")
        print("     你自己照常用 http://127.0.0.1:%d 访问，不受影响。" % PORT)
    else:
        print("  ❌ 删除失败（返回码 %d）：" % code)
        print(out)


def do_status():
    sep("当前状态")
    print("  管理员权限 :", "是" if is_admin() else "否（开放/关闭需要管理员）")
    print("  防火墙放行 :", "已开放（同事可连）" if rule_exists() else "未开放（只有本机可连）")
    print("  服务监听   :", "端口 %d 在监听（服务已启动）" % PORT if port_listening()
          else "端口 %d 没在监听（服务没启动）" % PORT)
    ips = lan_ips()
    print("  本机内网 IP:", "、".join(ips) if ips else "未检测到")
    if ips and rule_exists() and port_listening():
        print("\n  → 同事现在可以访问：")
        for ip in ips:
            print("     http://%s:%d" % (ip, PORT))
    print("\n  账号密码文件:", os.path.join(DATA_DIR, "auth.json"),
          "（存在就是已设置）" if os.path.exists(os.path.join(DATA_DIR, "auth.json")) else "（还没设置）")


def do_diagnose():
    """同事打不开时的排查清单"""
    sep("同事访问不了？按这个顺序查")
    print("""  1. 你自己先在本机开 http://127.0.0.1:%d  —— 打不开说明服务没启动，先双击「启动服务.pyw」
  2. 回到主菜单选 3，确认「防火墙放行 = 已开放」「服务监听 = 在监听」
  3. 用同事的电脑 ping 你的 IP（把 IP 换成菜单 3 里显示的）：
        ping 你的IP
     不通 = 你们不在同一网段 / 公司网络做了隔离，这个得找网管开
  4. 在你自己电脑的浏览器里打开同事用的那个地址 http://你的IP:%d
     你自己都打不开 = 防火墙没生效，重开一次（选 2 再选 1）
  5. 同事能打开登录页但进不去 = 账号密码问题，看下面「忘记密码」""")
    print("""
  忘记密码怎么办（本机使用场景）：
     关掉服务 → 删掉数据目录下的 auth.json → 重新启动服务
     → 打开页面会要求重新设置账号密码（文档数据不受影响）
     数据目录位置：""" + DATA_DIR)


# ---------------------------------------------------------------- 菜单
def main():
    if not is_admin():
        print("=" * 62)
        print("  这个操作需要管理员权限。")
        print("  接下来会弹出一个「用户账户控制」窗口，请点【是】。")
        print("=" * 62)
        run_as_admin()
        return

    while True:
        sep("paperless-lite 局域网共享开关")
        print("""
   1 —— 开放局域网访问（同事能连进来）
   2 —— 关闭局域网访问（收回，只有你自己能开）
   3 —— 查看当前状态
   4 —— 同事访问不了？排查清单
   0 —— 退出
""")
        choice = input("  请输入数字后回车：").strip()
        if choice == "1":
            do_open()
        elif choice == "2":
            do_close()
        elif choice == "3":
            do_status()
        elif choice == "4":
            do_diagnose()
        elif choice == "0":
            print("\n  已退出。\n")
            return
        else:
            print("  没看懂，请输入 0-4 的数字。")
            continue
        input("\n  按回车键返回菜单...")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("出错了：", e)
        input("按回车键退出...")
