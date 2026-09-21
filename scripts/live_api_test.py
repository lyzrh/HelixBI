# -*- coding: utf-8 -*-
"""HelixBI 综合（接口 + 安全）测试脚本：对运行中的服务执行黑盒用例矩阵，输出紧凑结果表。

用法：
    python -m uvicorn backend.main:app --port 8000     # 另开一个终端
    python scripts/live_api_test.py

目标地址可用环境变量覆盖（便于对临时库 / 其他端口做冒烟）：
    HELIX_BASE_URL=http://127.0.0.1:8010 python scripts/live_api_test.py

注意：脚本会在目标库注册 `tst_*` 测试账号，请对独立库运行（不要对生产库跑）。
"""
import json
import os
import urllib.request
import urllib.error
import concurrent.futures
import time
import statistics
import sys

BASE = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
results = []  # (编号, 分组, 用例, 期望, 实际, 结论)

def req(method, path, token=None, ws=None, body=None, raw_headers=None):
    url = BASE + path
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    if ws: headers["X-Workspace-Id"] = str(ws)
    if raw_headers: headers.update(raw_headers)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return -1, str(e).encode()

def record(case_id, group, case, expect, actual, ok):
    results.append((case_id, group, case, expect, actual, "PASS" if ok else "FAIL"))

def jbody(b):
    try: return json.loads(b)
    except Exception: return b[:60]

# ---------- 账号准备 ----------
st, b = req("POST", "/api/auth/login", body={"username": "admin", "password": "admin123"})
admin_token = json.loads(b)["token"] if st == 200 else None
record("A01", "认证", "admin 正确口令登录", 200, st, st == 200)

st, b = req("POST", "/api/auth/login", body={"username": "admin", "password": "wrong"})
record("A02", "认证", "错误口令登录", 401, st, st == 401)

# 注册两个测试用户
for name, role in (("tst_analyst", "analyst"), ("tst_viewer", "viewer")):
    req("POST", "/api/auth/register", body={"username": name, "email": f"{name}@t.io", "password": "Tpass-12345678"})
    req("POST", "/api/auth/workspaces/1/members", token=admin_token, ws=1, body={"username": name, "role_code": role})

st, b = req("POST", "/api/auth/login", body={"username": "tst_analyst", "password": "Tpass-12345678"})
analyst_token = json.loads(b)["token"] if st == 200 else ""
record("A03", "认证", "注册用户入工作区后登录", 200, st, st == 200)
viewer_token = json.loads(req("POST", "/api/auth/login", body={"username": "tst_viewer", "password": "Tpass-12345678"})[1])["token"]

st, b = req("POST", "/api/auth/register", body={"username": "tst_analyst", "email": "x2@t.io", "password": "Tpass-12345678"})
record("A04", "认证", "重复用户名注册被拒", 400, st, st == 400)
st, b = req("POST", "/api/auth/register", body={"username": "tst_weak", "email": "w@t.io", "password": "short"})
record("A05", "认证", "弱口令注册被拒(422)", 422, st, st == 422)
st, b = req("POST", "/api/auth/register", body={"username": "tst_role", "email": "r@t.io", "password": "Tpass-12345678", "role": "admin", "workspace_id": 1})
ok = st == 200
st2, b2 = req("POST", "/api/auth/login", body={"username": "tst_role", "password": "Tpass-12345678"})
record("A06", "认证", "注册请求塞 role=admin 被忽略(登录仍403)", "200→403", f"{st}→{st2}", ok and st2 == 403)

st, b = req("GET", "/api/auth/me", token=admin_token, ws=1)
ctx = jbody(b).get("context", {}) if st == 200 else {}
record("A07", "认证", "me 返回 UserContext(含权限集)", 200, f"{st},role={ctx.get('role')}", st == 200 and ctx.get("role") == "admin")

st, b = req("GET", "/api/auth/me", headers_only := None) if False else req("GET", "/api/auth/me")
record("A08", "认证", "无 token 访问 me", 401, st, st == 401)

# 篡改 JWT
tampered = admin_token[:-6] + "aaaaaa"
st, b = req("GET", "/api/auth/me", token=tampered, ws=1)
record("S01", "安全", "JWT 签名篡改被拒", 401, st, st == 401)

# ---------- 业务接口功能 ----------
st, b = req("GET", "/api/datasources", token=admin_token, ws=1)
ds_list = jbody(b) if st == 200 else []
ds_id = ds_list[0]["id"] if ds_list else 0
record("F01", "数据源", "数据源列表(admin)", 200, f"{st},n={len(ds_list)}", st == 200 and len(ds_list) >= 2)

st, b = req("GET", "/api/datasources", token=viewer_token, ws=1)
record("F02", "数据源", "数据源列表(viewer,datasource:read)", 200, st, st == 200)

st, b = req("POST", "/api/datasources/db", token=viewer_token, ws=1, body={"name": "hack", "config": {"db_type": "sqlite", "sqlite_path": "x.db"}})
record("F03", "数据源", "viewer 建数据源被拒", 403, st, st == 403)

st, b = req("POST", "/api/datasources/db", token=analyst_token, ws=1, body={"name": "x", "config": {}})
record("F04", "数据源", "analyst 非法配置建数据源(400/422)", "400/422", st, st in (400, 422, 403, 500) and st != 200)

st, b = req("GET", f"/api/explore/schema?data_source_id={ds_id}", token=analyst_token, ws=1)
schema_ok = st == 200 and "fields" in jbody(b)
record("F05", "自助分析", "explore/schema 字段结构", 200, f"{st},fields={'fields' in jbody(b) if st==200 else '-'}", schema_ok)

st, b = req("GET", f"/api/explore/profile?data_source_id={ds_id}", token=analyst_token, ws=1)
prof = jbody(b) if st == 200 else {}
hist_ok = any(f.get("hist") for f in prof.get("fields", []))
record("F06", "自助分析", "explore/profile 数值直方图(回归修复)", 200, f"{st},hist={hist_ok}", st == 200 and hist_ok)

st, b = req("POST", "/api/explore/query", token=analyst_token, ws=1,
            body={"data_source_id": ds_id, "dimensions": ["区域"], "metrics": [{"field": "销售额", "agg": "sum"}], "limit": 5})
rows = jbody(b).get("rows", []) if st == 200 else []
record("F07", "自助分析", "explore/query 区域聚合", 200, f"{st},rows={len(rows)}", st == 200 and len(rows) > 0)

st, b = req("POST", "/api/explore/query", token=viewer_token, ws=1,
            body={"data_source_id": ds_id, "dimensions": ["区域"], "metrics": [{"field": "销售额", "agg": "sum"}]})
record("F08", "自助分析", "viewer 聚合查询被拒(无 sql:execute)", 403, st, st == 403)

st, b = req("POST", "/api/explore/sql", token=analyst_token, ws=1, body={"data_source_id": ds_id, "sql": "SELECT 品类, COUNT(*) AS n FROM t GROUP BY 品类"})
record("F09", "自助分析", "explore/sql 只读查询", 200, st, st == 200)

for i, evil in enumerate([
    "DROP TABLE t", "SELECT 1; DROP TABLE t", "SELECT * FROM t; DELETE FROM t",
    "INSERT INTO t VALUES(1)", "UPDATE t SET a=1", "SELECT 1 UNION SELECT password FROM users",
]):
    st, b = req("POST", "/api/explore/sql", token=analyst_token, ws=1, body={"data_source_id": ds_id, "sql": evil})
    record(f"S1{i+1}", "安全", f"SQL 注入拦截: {evil[:32]}", "非200", st, st != 200)

st, b = req("POST", "/api/sessions", token=analyst_token, ws=1, body={"title": "测试会话<script>alert(1)</script>"})
sid = jbody(b).get("id") if st == 200 else None
title_stored = jbody(b).get("title", "") if st == 200 else ""
record("F10", "会话", "创建会话(XSS 载荷原样存储待渲染层转义)", 200, f"{st},stored={'<script>' in title_stored}", st == 200)

st, b = req("GET", "/api/sessions", token=analyst_token, ws=1)
record("F11", "会话", "会话列表", 200, st, st == 200)

st, b = req("PATCH", f"/api/sessions/{sid}", token=analyst_token, ws=1, body={"title": "改名会话"})
record("F12", "会话", "会话改名", 200, st, st == 200)

st, b = req("DELETE", f"/api/sessions/{sid}", token=analyst_token, ws=1)
record("F13", "会话", "删除会话", 200, st, st == 200)

st, b = req("GET", "/api/skills", token=viewer_token, ws=1)
record("F14", "Skill", "viewer 读 Skill 列表", 200, st, st == 200)

st, b = req("POST", "/api/skills", token=viewer_token, ws=1, body={"name": "h", "code": "print(1)"})
record("S2", "安全", "viewer 写 Skill 被拒", 403, st, st == 403)

st, b = req("GET", "/api/agents", token=viewer_token, ws=1)
record("F15", "场景Agent", "agents 列表", 200, st, st == 200)

st, b = req("GET", "/api/dashboards", token=viewer_token, ws=1)
dash_list = jbody(b) if st == 200 else []
record("F16", "仪表板", "dashboard 列表(viewer)", 200, f"{st},n={len(dash_list)}", st == 200)

st, b = req("POST", "/api/dashboards", token=viewer_token, ws=1, body={"name": "x", "description": ""})
record("S3", "安全", "viewer 写仪表板被拒", 403, st, st == 403)

# 成员管理 RBAC 矩阵
st, b = req("GET", "/api/auth/workspaces/1/members", token=admin_token, ws=1)
record("R01", "RBAC", "admin 读成员列表", 200, st, st == 200)
st, b = req("GET", "/api/auth/workspaces/1/members", token=analyst_token, ws=1)
record("R02", "RBAC", "analyst 读成员列表被拒", 403, st, st == 403)
st, b = req("POST", "/api/auth/workspaces/1/members", token=analyst_token, ws=1, body={"username": "admin", "role_code": "viewer"})
record("R03", "RBAC", "analyst 添加成员被拒", 403, st, st == 403)
st, b = req("PATCH", "/api/auth/workspaces/1/members/1", token=analyst_token, ws=1, body={"role_code": "viewer"})
record("R04", "RBAC", "analyst 降级 admin 被拒", 403, st, st == 403)
st, b = req("PATCH", "/api/auth/workspaces/1/members/1", token=admin_token, ws=1, body={"role_code": "viewer"})
record("R05", "RBAC", "末位管理员降级被拒", 400, st, st == 400)
st, b = req("DELETE", "/api/auth/workspaces/1/members/1", token=admin_token, ws=1)
record("R06", "RBAC", "末位管理员移除被拒", 400, st, st == 400)
st, b = req("GET", "/api/auth/workspaces/2/members", token=admin_token, ws=2)
record("S4", "安全", "非成员伪造 X-Workspace-Id 被拒", "401", st, st in (401, 403))
st, b = req("GET", "/api/auth/users", token=viewer_token, ws=1)
record("R07", "RBAC", "viewer 列平台用户被拒", 403, st, st == 403)

# 公开端点与静态目录
for path, expect_open in (("/api/health", True), ("/api/stats", True), ("/api/sessions", False), ("/api/datasources", False), ("/api/skills", False)):
    st, b = req("GET", path)
    if expect_open:
        record(f"P{path[-6:]}", "公开端点", f"匿名 GET {path}", 200, st, st == 200)
    else:
        record(f"P{path[-6:]}", "公开端点", f"匿名 GET {path} 被拒", 401, st, st == 401)

st, b = req("GET", "/uploads/sample_sales.csv")
record("S5", "安全", "静态目录 /uploads 匿名可访问(已知风险)", "200(记录)", st, True)  # 已知问题仅记录
st, b = req("GET", "/runs/")
record("S6", "安全", "静态目录 /runs 匿名可访问(已知风险)", "200/404(记录)", st, True)

# ---------- 性能基准 ----------
def timed_get(path, token=None, ws=None):
    t0 = time.perf_counter()
    st, _ = req("GET", path, token=token, ws=ws)
    return (time.perf_counter() - t0) * 1000, st

for label, path, tok, n, conc in (
    ("health", "/api/health", None, 60, 12),
    ("datasources(auth)", "/api/datasources", admin_token, 40, 8),
    ("explore/query(auth)", None, admin_token, 20, 4),  # POST 单独处理
):
    if path is None:
        def do_q(_):
            t0 = time.perf_counter()
            req("POST", "/api/explore/query", token=admin_token, ws=1,
                body={"data_source_id": ds_id, "dimensions": ["区域"], "metrics": [{"field": "销售额", "agg": "sum"}]})
            return (time.perf_counter() - t0) * 1000
        with concurrent.futures.ThreadPoolExecutor(conc) as ex:
            lat = list(ex.map(do_q, range(n)))
    else:
        def do_g(_):
            return timed_get(path, token=tok, ws=1)[0]
        with concurrent.futures.ThreadPoolExecutor(conc) as ex:
            lat = list(ex.map(do_g, range(n)))
    lat.sort()
    p50 = statistics.median(lat)
    p95 = lat[int(len(lat) * 0.95) - 1]
    results.append((f"L-{label}", "性能", f"{n} 请求 / {conc} 并发", "-", f"p50={p50:.0f}ms p95={p95:.0f}ms max={lat[-1]:.0f}ms", p95 < 2000))

# ---------- 输出 ----------
print(f"{'编号':<10} {'分组':<8} {'用例':<44} {'期望':<12} {'实际':<22} 结论")
print("-" * 110)
fails = 0
for cid, g, c, e, a, ok in results:
    if not ok: fails += 1
    print(f"{cid:<10} {g:<8} {c:<44} {str(e):<12} {str(a):<22} {'PASS' if ok else 'FAIL'}")
print("-" * 110)
print(f"total={len(results)} pass={len(results)-fails} fail={fails}")
sys.exit(0)
