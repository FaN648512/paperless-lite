# -*- coding: utf-8 -*-
"""局域网共享功能的端到端验收：鉴权是否真的挡得住"""
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    import local_settings as _local
except ImportError:
    _local = None

DATA_DIR = (os.environ.get("PAPERLITE_DATA")
            or (getattr(_local, "DATA_DIR", "") if _local else "")
            or os.path.join(ROOT, "data"))
BASE = os.environ.get("PL_BASE", "http://127.0.0.1:8765")
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK]   " + name)
    else:
        fail += 1
        print("  [FAIL] " + name + ("  -> " + str(extra) if extra else ""))


def new_session():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)), cj


def get(op, path):
    try:
        r = op.open(BASE + path, timeout=10)
        return r.status, r.read().decode("utf-8", "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.url


def post(op, path, obj):
    data = json.dumps(obj or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        r = op.open(req, timeout=10)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


print("\n=== 1. 未设置密码时，所有人都被挡在登录页 ===")
op1, _ = new_session()
code, body, url = get(op1, "/")
check("访问首页被重定向到 /login", code == 200 and url.endswith("/login"), "%s %s" % (code, url))
code, body, _ = get(op1, "/api/documents")
check("接口返回 401（不会泄露文档数据）", code == 401, code)
check("接口正文不含文档内容", "original_name" not in body, body[:120])
code, body, _ = get(op1, "/api/meta")
check("meta 接口也被挡（401）", code == 401, code)
code, body, _ = get(op1, "/api/auth/status")
check("登录页能查状态 need_setup=true", code == 200 and json.loads(body)["need_setup"] is True, body)

print("\n=== 2. 设置账号密码（弱密码要被拒）===")
op2, _ = new_session()
code, body = post(op2, "/api/auth/setup", {"username": "a", "password": "123"})
check("账号太短被拒（400）", code == 400, "%s %s" % (code, body))
code, body = post(op2, "/api/auth/setup", {"username": "admin", "password": "123"})
check("密码不足 6 位被拒（400）", code == 400, "%s %s" % (code, body))

print("\n=== 3. 正常设置账号密码 ===")
op3, cj3 = new_session()
code, body = post(op3, "/api/auth/setup", {"username": "boss", "password": "demo123456"})
check("设置成功（200）", code == 200, "%s %s" % (code, body))
check("密码文件已生成", __import__("os").path.exists(AUTH_FILE))
with open(AUTH_FILE, encoding="utf-8") as f:
    a = json.load(f)
check("存的是哈希不是明文", "demo123456" not in json.dumps(a), a)
check("哈希带 scrypt 标识", "scrypt" in a.get("password_hash", ""), a.get("password_hash", "")[:20])

print("\n=== 4. 已登录的正常访问 ===")
code, body, url = get(op3, "/")
check("首页正常打开（不再跳转）", code == 200 and not url.endswith("/login"), "%s %s" % (code, url))
code, body, _ = get(op3, "/api/documents")
check("文档列表能取到", code == 200, code)
docs = json.loads(body)
docs = docs["items"] if isinstance(docs, dict) else docs
check("确实返回了文档数据", isinstance(docs, list) and len(docs) > 0,
      "共 %s 份" % (len(docs) if isinstance(docs, list) else body[:80]))

print("\n=== 5. 换了浏览器（没有 cookie）必须重新登录 ===")
op4, _ = new_session()
code, body, url = get(op4, "/")
check("未登录访问首页被踢到 /login", url.endswith("/login"), url)
code, body, _ = get(op4, "/api/documents")
check("未登录调接口 401", code == 401, code)
# 注意：urllib 会自动跟随 302，所以这里看「最终落在哪个 URL」而不是状态码
_, _, url = get(op4, "/file/1")
check("未登录直接下载原件被踢到登录页", url.endswith("/login"), url)
_, _, url = get(op4, "/thumb/1")
check("未登录取缩略图被踢到登录页", url.endswith("/login"), url)

print("\n=== 6. 登录校验 ===")
op5, _ = new_session()
code, body = post(op5, "/api/auth/login", {"username": "boss", "password": "wrongpwd"})
check("密码错误被拒（401）", code == 401, "%s %s" % (code, body))
code, body = post(op5, "/api/auth/login", {"username": "boss", "password": "demo123456"})
check("密码正确登录成功（200）", code == 200, "%s %s" % (code, body))
code, body, _ = get(op5, "/api/documents")
check("登录后立刻能取数据", code == 200, code)

print("\n=== 7. 改密码（需要原密码）===")
op7, _ = new_session()
post(op7, "/api/auth/login", {"username": "boss", "password": "demo123456"})
code, body = post(op7, "/api/auth/password", {"old_password": "wrongold", "new_password": "newpass123"})
check("原密码不对，改不了（400）", code == 400, "%s %s" % (code, body))
code, body = post(op7, "/api/auth/password", {"old_password": "demo123456", "new_password": "newpass123"})
check("原密码正确，修改成功（200）", code == 200, "%s %s" % (code, body))
code, body, _ = get(op7, "/api/documents")
check("改完密码强制重新登录（旧会话失效）", code == 401, code)

print("\n=== 8. 退出登录 ===")
op8, _ = new_session()
post(op8, "/api/auth/login", {"username": "boss", "password": "newpass123"})
code, _, _ = get(op8, "/api/documents")
check("新密码可登录", code == 200, code)
code, body = post(op8, "/api/auth/logout", {})
check("退出成功（200）", code == 200, body)
code, _, _ = get(op8, "/api/documents")
check("退出后再访问被挡（401）", code == 401, code)

print("\n=== 9. 服务有没有真的绑到局域网（同事能不能连进来）===")
import socket as _sk
_lan = []
try:
    _s = _sk.socket(_sk.AF_INET, _sk.SOCK_DGRAM)
    _s.connect(("10.255.255.255", 1))
    _lan.append(_s.getsockname()[0])
    _s.close()
except Exception:
    pass
check("检测到内网 IP", bool(_lan), _lan)
for ip in _lan:
    try:
        r = urllib.request.urlopen("http://%s:8765/api/auth/status" % ip, timeout=5)
        body = r.read().decode("utf-8", "replace")
        check("用内网 IP %s 能连上（说明绑的不是 127.0.0.1）" % ip, r.status == 200, body)
    except Exception as e:
        check("用内网 IP %s 能连上（说明绑的不是 127.0.0.1）" % ip, False, e)

print("\n=== 10. 防爆破：连错 5 次锁 5 分钟（放最后，会锁本机 5 分钟）===")
op6, _ = new_session()
codes = []
for i in range(7):
    code, _ = post(op6, "/api/auth/login", {"username": "boss", "password": "bad%d" % i})
    codes.append(code)
check("前几次是 401", codes[0] == 401, codes)
check("连续失败后变成 429（已锁定）", 429 in codes, codes)
code, body = post(op6, "/api/auth/login", {"username": "boss", "password": "newpass123"})
check("锁定期内「正确密码也进不去」（防暴力破解生效）", code == 429, "%s %s" % (code, body))
print("     （注：此锁按 IP 计，5 分钟后自动解除；急用就重启服务）")

print("\n" + "=" * 58)
print("  通过 %d 项，失败 %d 项" % (ok, fail))
print("=" * 58)
