# -*- coding: utf-8 -*-
"""本机专属配置模板 —— 复制成 local_settings.py 再按需修改。

local_settings.py 已被 .gitignore 排除，不会进版本库；
所有配置项都有可移植的默认值，不建这个文件也能直接跑。
"""

# 数据目录（文档原件 docs/ 、缩略图 thumbs/ 、SQLite 库 paperlite.db 、账号 auth.json）
# 默认：<项目目录>/data
# Windows 上想避开系统盘可写成 r"E:\my-docs-data"
DATA_DIR = ""

# 一键备份（备份数据.pyw）的输出目录，默认：<数据目录>-backup
BACKUP_DIR = ""

# 运行服务用的 Python 解释器，留空则用当前解释器
# 例：r"C:\venvs\paperless\Scripts\python.exe"
VENV_PY = ""
