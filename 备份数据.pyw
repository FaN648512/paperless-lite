"""
paperless-lite 数据一键备份
===========================
用法：双击本文件即可。它会把数据目录完整复制一份到备份目录，
以时间戳命名，并自动保留最近若干份。

数据目录 / 备份目录的解析顺序见 app.py 顶部说明；
想固定到某个盘，就在 local_settings.py 里写 DATA_DIR / BACKUP_DIR。

为什么需要它：
  文档库里是合同、发票这类敏感资料，且往往只存在一份。
  硬盘万一出问题，数据就永久没了。备份成本极低，务必定期做。

技术要点：
  数据库用 SQLite 官方的 backup 接口复制，保证「边运行边备份」也不会拷到半个事务
  （直接 copy 文件在服务运行时可能拿到损坏的库）。
"""
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import local_settings as _local
except ImportError:
    _local = None


def _local_get(name, default=""):
    return getattr(_local, name, default) if _local else ""


SRC = (os.environ.get("PAPERLITE_DATA")
       or _local_get("DATA_DIR")
       or os.path.join(HERE, "data"))
BACKUP_ROOT = (os.environ.get("PAPERLITE_BACKUP")
               or _local_get("BACKUP_DIR")
               or SRC.rstrip("\\/") + "-backup")
KEEP = 5          # 保留最近几份备份
DB_NAME = "paperlite.db"


def human(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024)
    return "%.1f MB" % (n / 1024 / 1024)


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def backup_db(src_db, dst_db):
    """用 SQLite backup 接口安全复制（服务运行中也能拷出一致快照）。"""
    src = sqlite3.connect(src_db)
    dst = sqlite3.connect(dst_db)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def main():
    if not os.path.isdir(SRC):
        print("找不到数据目录：%s" % SRC)
        print("如果你改过数据位置，请设置环境变量 PAPERLITE_DATA。")
        input("按回车键退出...")
        sys.exit(1)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(dest, exist_ok=True)

    print("=" * 56)
    print(" paperless-lite 数据备份")
    print("=" * 56)
    print("源目录  : %s" % SRC)
    print("备份到  : %s" % dest)
    print("-" * 56)

    # 1. 数据库（安全快照）
    src_db = os.path.join(SRC, DB_NAME)
    if os.path.isfile(src_db):
        backup_db(src_db, os.path.join(dest, DB_NAME))
        print("  [OK] 数据库已备份（SQLite 一致性快照）")

    # 2. 原始文档与缩略图
    for sub in ("docs", "thumbs"):
        src_sub = os.path.join(SRC, sub)
        if os.path.isdir(src_sub):
            shutil.copytree(src_sub, os.path.join(dest, sub), dirs_exist_ok=True)
            n = sum(len(f) for _r, _d, f in os.walk(src_sub))
            print("  [OK] %s 已备份（%d 个文件）" % (sub, n))

    size = dir_size(dest)
    print("-" * 56)
    print("本次备份体积：%s" % human(size))

    # 3. 只保留最近 KEEP 份，自动清理旧的
    try:
        olds = sorted(
            [d for d in os.listdir(BACKUP_ROOT)
             if os.path.isdir(os.path.join(BACKUP_ROOT, d))],
            reverse=True,
        )[KEEP:]
        for d in olds:
            shutil.rmtree(os.path.join(BACKUP_ROOT, d), ignore_errors=True)
            print("  已清理旧备份：%s" % d)
    except OSError as e:
        print("  清理旧备份时出错（不影响本次备份）：%s" % e)

    # 4. 恢复说明写进备份目录
    with open(os.path.join(dest, "恢复方法.txt"), "w", encoding="utf-8") as fh:
        fh.write(
            "如何恢复这份备份\n"
            "================\n"
            "1. 先关掉 paperless-lite 服务（关掉黑色控制台窗口，或用 启动服务 前先停）\n"
            "2. 把本文件夹里的 paperlite.db、docs、thumbs 三个东西\n"
            "   复制回 %s，覆盖同名文件\n" % SRC
            + "3. 重新启动服务，数据即恢复\n\n"
            "备份时间：%s\n" % time.strftime("%Y-%m-%d %H:%M:%S")
        )

    print("-" * 56)
    print("备份完成！存放位置：")
    print("  %s" % dest)
    print()
    print("提示：建议每月至少备份一次，重要资料上传后随手跑一次。")
    print("      恢复方法已写进备份文件夹里的「恢复方法.txt」。")
    print("=" * 56)
    input("按回车键关闭...")


if __name__ == "__main__":
    main()
