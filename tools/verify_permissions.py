# -*- coding: utf-8 -*-
"""
多用户权限验收：私密 / 只读 / 公开编辑

场景：管理员(admin) + 普通用户(user) 两个账号，互相验证能看到什么、能改什么。
"""
import base64
import http.cookiejar
import json
import os
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_PNG = os.path.join(ROOT, "tools", "_perm_test.png")
ADMIN = ("boss", "demo123456")
USER2 = ("wang", "demo123456")

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
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(op, path):
    try:
        r = op.open(BASE + path, timeout=15)
        return r.status, r.read().decode("utf-8", "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.url


def post(op, path, obj=None, method="POST"):
    data = json.dumps(obj or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        r = op.open(req, timeout=15)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def patch(op, path, obj):
    return post(op, path, obj, method="PATCH")


def delete(op, path):
    return post(op, path, {}, method="DELETE")


def upload(op, path, visibility="readonly"):
    """multipart/form-data 上传一份文档"""
    boundary = "----paperlitePermTest"
    with open(path, "rb") as f:
        payload = f.read()
    body = b""
    body += ("--%s\r\n" % boundary).encode()
    body += b'Content-Disposition: form-data; name="visibility"\r\n\r\n'
    body += (visibility + "\r\n").encode()
    body += ("--%s\r\n" % boundary).encode()
    body += b'Content-Disposition: form-data; name="files"; filename="perm_test.png"\r\n'
    body += b"Content-Type: image/png\r\n\r\n"
    body += payload + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    req = urllib.request.Request(
        BASE + "/api/documents", data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        r = op.open(req, timeout=30)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def ids_of(op):
    code, body, _ = get(op, "/api/documents")
    if code != 200:
        return None, []
    return json.loads(body), [d["id"] for d in json.loads(body)["items"]]


def make_tmp_png():
    """造一张唯一内容的 1x1 PNG，避免被 MD5 查重拦下"""
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    with open(TMP_PNG, "wb") as f:
        f.write(png + os.urandom(32).hex().encode())   # 随机尾巴保证哈希唯一


# ==========================================================================
print("\n=== 0. 准备：造测试图片 ===")
make_tmp_png()
check("测试图片已生成", os.path.exists(TMP_PNG))

print("\n=== 1. 老板建号（第一个账号 = 管理员），接管历史文档 ===")
admin = new_session()
code, body = post(admin, "/api/auth/setup", {"username": ADMIN[0], "password": ADMIN[1]})
j = json.loads(body) if code == 200 else {}
check("管理员账号创建成功", code == 200, "%s %s" % (code, body))
adopted = j.get("adopted", 0)
check("历史文档已划归管理员（共 %d 份）" % adopted, adopted > 0, j)
code, body, _ = get(admin, "/api/auth/status")
check("状态显示 is_admin=true", json.loads(body).get("is_admin") is True, body)

print("\n=== 2. 管理员给同事开账号 ===")
code, body = post(admin, "/api/users",
                  {"username": USER2[0], "password": USER2[1], "is_admin": 0})
check("创建用户 wang 成功", code == 200, "%s %s" % (code, body))
code, body = post(admin, "/api/users",
                  {"username": USER2[0], "password": USER2[1]})
check("重名账号被拒（400）", code == 400, "%s %s" % (code, body))
code, body, _ = get(admin, "/api/users")
check("管理员能看用户列表", code == 200, code)

u2 = new_session()
code, body = post(u2, "/api/auth/login", {"username": USER2[0], "password": USER2[1]})
check("同事 wang 登录成功", code == 200, "%s %s" % (code, body))
code, body, _ = get(u2, "/api/auth/status")
check("同事不是管理员", json.loads(body).get("is_admin") is False, body)
code, body, _ = get(u2, "/api/users")
check("同事看用户列表被拒（403）", code == 403, code)

print("\n=== 3. 同事能看到老板的「只读」文档 ===")
_, admin_ids = ids_of(admin)
data2, u2_ids = ids_of(u2)
check("同事能看到历史文档（%d 份）" % len(u2_ids), len(u2_ids) > 0, len(u2_ids))
check("同事看到的和管理员一样多", len(u2_ids) == len(admin_ids),
      "admin=%d u2=%d" % (len(admin_ids), len(u2_ids)))

print("\n=== 4. 老板上传一份，设为「私密」 ===")
code, body = upload(admin, TMP_PNG, visibility="readonly")
created = json.loads(body).get("created", []) if code == 200 else []
check("上传成功", code == 200 and created, "%s %s" % (code, body))
if not created:
    print("\n上传失败，后续测试无法继续")
    raise SystemExit(1)
doc_a = created[0]

code, body = patch(admin, "/api/documents/%d/visibility" % doc_a, {"visibility": "private"})
check("设为私密成功", code == 200, "%s %s" % (code, body))

make_tmp_png()   # 换内容，方便第二次上传

print("\n=== 5. 私密文档：同事完全看不见 ===")
_, u2_ids = ids_of(u2)
check("同事列表里没有这份私密文档", doc_a not in u2_ids, "同事可见 %d 份" % len(u2_ids))
code, body, _ = get(u2, "/api/documents/%d" % doc_a)
check("同事直接访问详情被拒（403）", code == 403, "%s %s" % (code, body))
code, body, _ = get(u2, "/file/%d" % doc_a)
check("同事直接下载原件被拒（403）", code == 403, code)
code, body, _ = get(u2, "/thumb/%d" % doc_a)
check("同事取缩略图被拒（403）", code == 403, code)
code, body = patch(u2, "/api/documents/%d" % doc_a, {"title": "同事偷偷改标题"})
check("同事改标题被拒（403）", code == 403, "%s %s" % (code, body))
code, body = delete(u2, "/api/documents/%d" % doc_a)
check("同事删除被拒（403）", code == 403, "%s %s" % (code, body))
code, body = post(u2, "/api/documents/%d/tags" % doc_a, {"name": "偷偷打标签"})
check("同事打标签被拒（403）", code == 403, "%s %s" % (code, body))
code, body = patch(u2, "/api/documents/%d/visibility" % doc_a, {"visibility": "public_edit"})
check("同事改可见性被拒（403）", code == 403, "%s %s" % (code, body))

print("\n=== 6. 老板改成「公开编辑」，同事就能改了 ===")
code, body = patch(admin, "/api/documents/%d/visibility" % doc_a, {"visibility": "public_edit"})
check("设为公开编辑成功", code == 200, body)
code, body = patch(u2, "/api/documents/%d" % doc_a, {"title": "同事改过的标题"})
check("同事能改标题了（200）", code == 200, "%s %s" % (code, body))
code, body = post(u2, "/api/documents/%d/tags" % doc_a, {"name": "同事加的标签"})
check("同事能打标签了（200）", code == 200, "%s %s" % (code, body))
code, body = patch(u2, "/api/documents/%d/visibility" % doc_a, {"visibility": "readonly"})
check("但同事仍不能改可见性（403，只有本人/管理员能改）", code == 403, "%s %s" % (code, body))

print("\n=== 7. 老板改成「只读」，同事又改不动了 ===")
code, body = patch(admin, "/api/documents/%d/visibility" % doc_a, {"visibility": "readonly"})
check("设为只读成功", code == 200, body)
_, u2_ids = ids_of(u2)
check("同事能看见（只读只是不能改）", doc_a in u2_ids, "同事可见 %d 份" % len(u2_ids))
code, body = patch(u2, "/api/documents/%d" % doc_a, {"title": "再改一次"})
check("同事改标题被拒（403）", code == 403, "%s %s" % (code, body))
code, body = delete(u2, "/api/documents/%d" % doc_a)
check("同事删除被拒（403）", code == 403, code)
code, body = post(u2, "/api/documents/%d/reprocess" % doc_a)
check("同事重新识别被拒（403）", code == 403, code)
code, body, _ = get(u2, "/file/%d" % doc_a)
check("但同事能下载查看（200）", code == 200, code)

print("\n=== 8. 同事自己的文档，同事能自己改可见性 ===")
make_tmp_png()
code, body = upload(u2, TMP_PNG, visibility="readonly")
created_b = json.loads(body).get("created", []) if code == 200 else []
check("同事上传成功", code == 200 and created_b, "%s %s" % (code, body))
if created_b:
    doc_b = created_b[0]
    code, body = patch(u2, "/api/documents/%d/visibility" % doc_b, {"visibility": "private"})
    check("同事能把自己的文档设为私密（200）", code == 200, "%s %s" % (code, body))

    print("\n=== 9. 管理员特权：连别人的私密文档也能看能改 ===")
    _, admin_ids = ids_of(admin)
    check("管理员能看到同事的私密文档", doc_b in admin_ids, "管理员可见 %d 份" % len(admin_ids))
    code, body = patch(admin, "/api/documents/%d" % doc_b, {"title": "管理员改的"})
    check("管理员能改同事的私密文档（200）", code == 200, "%s %s" % (code, body))
    code, body = patch(admin, "/api/documents/%d/visibility" % doc_b, {"visibility": "readonly"})
    check("管理员能改同事文档的可见性（200）", code == 200, body)

    print("\n=== 10. 清理测试数据 ===")
    code, body = delete(admin, "/api/documents/%d" % doc_b)
    check("删除同事的测试文档", code == 200, body)

code, body = delete(admin, "/api/documents/%d" % doc_a)
check("删除老板的测试文档", code == 200, body)
code, body = delete(admin, "/api/users/%s" % USER2[0])
check("删除测试用户 wang", code == 200, body)
try:
    os.remove(TMP_PNG)
except OSError:
    pass

print("\n" + "=" * 60)
print("  通过 %d 项，失败 %d 项" % (ok, fail))
print("=" * 60)
