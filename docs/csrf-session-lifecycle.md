# CSRF 评估与会话生命周期

本轮只做两件事：给出 CSRF 结论（不实现 token）、把会话过期清理改为可独立触发。未引入新依赖，未改业务接口。

## 1. CSRF 评估

### 检查结果

| 检查项 | 结果 | 证据 |
|---|---|---|
| 写接口数量 | 7 个，全部 POST，无 PUT/PATCH/DELETE | `app/api/routes.py`：register、login、logout、logout-all、create trip、modify-day、evidence search |
| Content-Type 强制 | 5 个带 body 的接口全部使用 Pydantic JSON；无 `Form(...)`、`UploadFile`、`request.body()`、multipart 解析 | 全 `app/` 检索无命中；实测 form-urlencoded / text/plain 均返回 422（`tests/test_csrf_session.py`） |
| 无 body 的写接口 | `logout`、`logout-all` 不受 Content-Type 约束 | `routes.py` 对应路由签名无 body 参数 |
| CORS | 未配置任何 CORS 中间件，不存在带 credentials 的跨域放行 | 检索无 `CORSMiddleware`；测试断言中间件列表不含 CORS |
| 部署形态 | 同源：FastAPI 直接托管 `frontend/dist`，前端使用相对路径 `/api/...` | `app/main.py` 静态托管与 SPA 回退 |
| Cookie | `HttpOnly`、`SameSite=lax`、`Path=/`；`Secure` 由 `TRAVELAI_COOKIE_SECURE` 控制（生产环境启动自检强制） | `routes.py` cookie 设置处 |

### 结论：维持现状足够，不实现 CSRF token

- 带 body 的写接口全部要求 `application/json`。跨站页面要发这种请求必须触发预检，而项目没有 CORS 配置，预检必然失败，攻击请求无法到达业务逻辑。
- `logout` / `logout-all` 虽然可以跨站提交普通表单，但 `SameSite=Lax` 不会在跨站 POST 中携带会话 Cookie，因此攻击者只能触发一个未携带身份的空请求，无法登出受害者。
- 同源部署 + 前端相对路径，不存在跨子域共享 Cookie 的场景。

### 方案对比

| 方案 | 改动成本 | 前端改动 | 适用前提 | 结论 |
|---|---|---|---|---|
| A 维持现状 | 0 | 无 | 同源部署、JSON-only 写接口、SameSite=Lax、无 CORS | **采用** |
| B 双提交 Cookie | 后端校验中间件 + 前端读取 Cookie 并回传 header | 需要 | SameSite 降级或出现非 JSON 写接口时的最小补强 | 暂不采用 |
| C 完整 token | 服务端签发、存储、校验，登录流程改造 | 需要，且覆盖面最大 | 多站点、复杂跨域、合规要求 | 暂不采用 |

选择 A 的理由：当前没有任何可利用路径，B/C 都会引入新的状态与失效逻辑，收益为零；等出现下方任一变化时再升级。

### 强制触发条件（任一发生即失去豁免，必须先升级再上线）

下表不是“建议”，而是合并/部署前的前置条件；不满足就不得沿用当前“维持现状”的结论。

| 触发条件 | 为什么会让结论失效 | 必须执行的动作 |
|---|---|---|
| 新增文件上传接口（multipart） | multipart 是跨站表单允许的类型，绕过 JSON-only 保护 | 先实现双提交 Cookie（方案 B）或完整 token（方案 C），再合并接口 |
| 新增子域名，或前后端分离部署 | 同源约束消失，SameSite 不再覆盖跨子域调用 | 先升级到方案 B/C，并重新评估 Cookie `Domain` 范围 |
| 将 SameSite 改为 `None`（如嵌入场景） | 跨站请求会重新携带 Cookie | 先升级到方案 B/C；`SameSite=None` 必须同时 `Secure` |
| 某接口改用 `Form` 参数或手动解析 body | 该接口的 JSON-only 约束被打破 | 先升级到方案 B/C，再改该接口 |
| 增加反向代理但未限制请求方法与类型 | 代理可能改写或放行非 JSON 请求 | 在代理层限制方法与 `Content-Type`，并重新跑第 1 节验收 |

升级方案优先级：能用同源 + JSON-only 解决时不动；否则先上方案 B（双提交 Cookie，前端只需额外回传一个 header）；多站点/合规场景才用方案 C。

### 自动化守护

`tests/test_csrf_session.py` 中新增两项守护测试，任一失败即表示触发了上表条件：

- `test_write_routes_do_not_declare_form_or_file_params`：运行时检查所有写路由，出现 `Form`/`File`/`UploadFile` 参数即失败。
- `test_app_source_has_no_non_json_write_constructs`：扫描 `app/` 源码，出现 `Form(...)`、`File/UploadFile`、`request.body()`/`request.form()`、`multipart/form-data`、`CORSMiddleware` 即失败。

Cookie 属性（`HttpOnly`、`SameSite=lax`、`Path=/`）与“未注册 CORS”由 `test_session_cookie_attributes_and_no_cors` 持续断言；改为 `SameSite=None` 会直接使该测试失败。部署层面的子域名/代理变化无法由测试覆盖，需在变更评审时对照上表执行。

## 2. 会话过期清理

### 实现

新增 `scripts/cleanup_sessions.py`：

```powershell
python scripts/cleanup_sessions.py                # 删除并报告数量
python scripts/cleanup_sessions.py --dry-run      # 只统计
python scripts/cleanup_sessions.py --json         # JSON 输出，便于接入日志/监控
```

- 复用 `Repository.delete_expired_sessions()`（`DELETE FROM sessions WHERE expires_at <= now`），无新依赖、无调度框架。
- 幂等：重复执行第二次 `deleted=0`；`--dry-run` 不会删除。
- 输出包含删除数量与剩余会话数；`--json` 形如 `{"deleted": 2, "remaining": 1}`。
- 登录时的机会性清理保留作为兜底，不改变现有行为。

### 本轮实测输出（真实库）

```
[dry-run] 将清理过期会话 0 个，当前共有 0 个。
已清理过期会话 0 个，当前剩余 0 个。
已清理过期会话 0 个，当前剩余 0 个。   # 第二次执行，幂等
```

当前库内没有活跃会话（此前的临时账号已清理），因此删除数为 0；行为由测试覆盖。

### 部署建议

- 用系统计划任务（Windows 任务计划程序或 cron）每天执行一次即可，无需常驻进程。
- 多实例部署时任意实例执行均安全（同一数据库，删除条件幂等）。

## 3. 测试结果

- `pytest -q`：**63 passed**（原 34 项核心测试全部保留通过；本轮新增 4 项）。
- 新增覆盖：
  1. 7 个写接口中带 body 的全部拒绝 form-urlencoded 与 text/plain（422）；
  2. 会话 Cookie 含 `HttpOnly`、`SameSite=lax`、`Path=/`，且应用未注册 CORS 中间件；
  3. 清理脚本删除过期会话、保留有效会话，第二次执行删除数为 0；
  4. `--dry-run` 不删除且多次统计结果一致，正式执行后删除数量正确。
- `scripts/http_smoke.py`：PASS（本轮未改业务代码）。

## 4. 限制

- 清理脚本只处理 `sessions`；登录失败计数（`login_failures`）按 24 小时衰减在登录时自动清理，审计表暂无留存策略。
- 无内置调度，需由外部计划任务触发；脚本本身不记录执行日志，如需留痕请用 `--json` 接入日志系统。
- 清理过程中若已有请求持有过期会话，下一次请求会因 `expires_at` 校验返回 401，行为不变。
