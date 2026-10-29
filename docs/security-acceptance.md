# 上线前安全验收报告

| 项目 | 内容 |
|---|---|
| 验收时间 | 2026-10-29 |
| 验收对象 | `http://127.0.0.1:8000` 实际运行实例（限流/耗时在隔离实例 8001 + 临时库执行，避免 IP 计数污染） |
| 验收方式 | 实际 HTTP 请求 + 数据库核对，不只读代码 |
| 代码基线 | tag `baseline-20261029`（Git 仓库，master 分支） |
| 自动化测试 | `pytest -q` → 83 passed |
| 数据基线 | 用户 1（仅 legacy）、会话 0、行程 25（全部 legacy）、版本 29、运行 47、审计 38 条、三表 NULL 归属 0 |
| 归属变更 | 2026-10-29 22:37 执行 `--claim-to <管理员账号>`（用户名已脱敏）：trips 25 / versions 29 / runs 47 改判；当前 `legacy` 归属为 0，无 NULL 归属 |
| 总体结论 | **有条件通过**：9 项验收全部通过；上线前必须完成第 6 节部署前置（TLS + production 配置）与第 5 节中标注为“上线前”的风险处置 |

## 1. 验收结果

| 用例 | 实际结果 | 结论 | 证据摘要 |
|---|---|---|---|
| Cookie 属性 | `HttpOnly`、`SameSite=lax`、`Path=/` 存在；`Secure` 在 development/HTTP 下缺失 | 通过 | `Set-Cookie: travelai_session=…; HttpOnly; Max-Age=86400; Path=/; SameSite=lax` |
| 登录限流 | 连续 6 次错误密码均 401 且响应体逐字节相同；第 6 次后 ip/account 两维度写入 `blocked_until`；退避期内正确密码也 401；退避过期后 200 且账号计数清除 | 通过（内部状态变化） | `login_failures` 两行 `fail_count=5` 且带 `blocked_until`；审计 `login_blocked` ×2 |
| 响应一致性 | 不存在用户 5 次 228.6–259.8ms（中位 232.8ms）；密码错误 5 次 231.4–253.5ms（中位 232.9ms）；状态码与 body 相同 | 通过 | 均为 `401 INVALID_CREDENTIALS`，文案一致 |
| 越权访问 | A 会话访问 B 行程：详情/版本/diff 均 404；列表 200 且不含 B 行程 | 通过（见备注 2） | `detail=404 versions=404 diff=404`，`list_contains_b_trip=false` |
| legacy 数据 | legacy 行程 25 条，普通用户读取 404；三表 `owner_id IS NULL` 均为 0 | 通过 | `trips`/`trip_versions`/`agent_runs` null 计数 0/0/0 |
| 会话失效 | 登出后原 Cookie 401；`logout-all` 后另一设备 401 | 通过 | `logout-all {"status":"ok","revoked":2}`，两端均 401 |
| 审计完整性 | 登录失败、越权、密码重置均落库；审计不含密码/token/Cookie 名/HttpOnly | 通过 | `login_failed` / `trip_access_denied` / `password_reset` 均存在；敏感字段命中 0 |
| 内容类型 | form-urlencoded 与 text/plain 写请求均 422 | 通过 | `model_attributes_type` 校验错误 |
| 启动自检 | `production` + `COOKIE_SECURE=false` 启动失败 | 通过 | exit 1，`RuntimeError: production 环境必须设置 TRAVELAI_COOKIE_SECURE=true` |

### 备注 1：限流的“行为变化”判定口径

用例原本要求“第 6 次行为发生变化”，但既有需求要求“被限流响应与密码错误不可区分”。当前实现选择后者：外部响应不变，变化只体现在服务端（`blocked_until`、`login_blocked` 审计、正确密码同样被拒）。若未来要求外部可见的 429，需要先推翻“不可区分”这一设计。

### 备注 2：行程列表的返回语义

列表接口按 owner 过滤，本人列表应为 200 且不含他人行程；不应要求 404。验收口径建议表述为“列表 200 但不含他人行程”。

## 2. Secure 属性落地

代码侧无需修改：`production` 启动自检已在 `app/config.py` 强制 `COOKIE_SECURE=true`，Cookie 的 `Secure` 直接读取该配置。本轮只补齐部署配置与模板：

| 文件 | 改动 |
|---|---|
| `docker-compose.yml` | 端口改为仅绑定 `127.0.0.1:8000`，注明 TLS 必须在反向代理终止 |
| `.env.example` | 补充“应用只监听 HTTP，生产需反向代理终止 TLS”的说明 |
| `README.md` | 配置表补充 `TRAVELAI_ENV` / `TRAVELAI_COOKIE_SECURE` / `TRAVELAI_TRUST_PROXY`；新增“生产部署（HTTPS + Secure Cookie）”步骤与验证方法 |

上线前检查清单：

1. `TRAVELAI_ENV=production`、`TRAVELAI_COOKIE_SECURE=true`，否则启动即失败（已验证）。
2. 由反向代理提供 HTTPS，容器端口不直接暴露公网。
3. 只有代理覆写 `X-Forwarded-For` 时才开 `TRAVELAI_TRUST_PROXY=true`。
4. 登录响应 `Set-Cookie` 同时包含 `HttpOnly`、`Secure`、`SameSite=lax`，浏览器能持久保存会话。

## 3. 审计表现状（本轮不改结构）

`audit_events` 表字段：`id`、`created_at`、`actor_key`（用户名哈希）、`event_type`、`ip`、`result`、`detail`（非敏感 JSON）。

已记录事件：`login_succeeded`、`login_failed`、`login_blocked`、`trip_access_denied`、`password_reset`、`logout_all`。

查询方式：`python scripts/audit_query.py --limit N [--event ...] [--username ...] [--json]`（只读打开数据库）。

已知局限：

- 哈希链只能发现单条记录被改写或删除；整库回滚靠外部锚点（`<db>.audit-anchor.json`，HMAC 签名）检测，但锚点与库同机存放，攻击者若连同锚点一起回滚或删除，仍无法识别。
- 留存清理由 `scripts/audit_retention.py` 手动或计划任务触发，不内置排期。
- 用户名只存哈希，IP 受 NAT/代理影响，无法定位到具体自然人。
- 审计写入是尽力而为，失败只记 warning，不能证明“某事件一定没发生”。

如需把锚点推送到外部只读存储（WORM/对象存储）或接入实时告警，属于后续独立改动，不在本轮范围。

## 4. 加固措施与验证状态

| 能力 | 实现要点 | 验证方式 | 状态 |
|---|---|---|---|
| 密码存储 | bcrypt，默认 cost=12，无明文 | 单元测试 + 代码核对 | 已满足 |
| 会话 | 32 字节随机 token，库中仅存 SHA-256；Cookie `HttpOnly` + `SameSite=lax` | 验收用例 1 | 已满足（生产需 Secure） |
| 数据隔离 | `trips`/`trip_versions`/`agent_runs` 均有 `owner_id`；他人资源返回 404 | 验收用例 4、5 | 已满足 |
| 防爆破 | IP + 账号双维度计数，5 次失败后 30s 起指数退避、30min 封顶；成功登录清账号计数 | 验收用例 2 | 已满足 |
| 响应一致性 | 失败统一 401 + 同一文案；未知账号也执行一次 dummy bcrypt | 验收用例 3 | 已满足 |
| 审计 | 6 类事件落库（登录成功/失败/限流、越权、密码重置、批量登出）；用户名哈希；过滤敏感字段；HMAC 哈希链 + 外部锚点 + 留存脚本 | 验收用例 7 + `tests/test_audit_chain.py` | 已满足（防篡改与回滚检测，无告警） |
| 可恢复性 | `admin_reset_password.py`（精确用户名 + 显式确认 + 一次性密码）重置并吊销全部会话；`POST /auth/logout-all` | 测试 + 验收用例 6 | 已满足 |
| CSRF | 同源 + 写接口 JSON-only + SameSite=lax + 无 CORS；5 个失效条件已文档化并有守护测试 | 验收用例 8 + 守护测试 | 维持现状（有前置条件） |
| Secure / 环境 | `production` 下未开 Secure 拒绝启动；模板默认 production + Secure | 验收用例 9 | 已满足（需 HTTPS 部署） |
| 会话清理 | 独立 CLI，`--dry-run`/`--json`，幂等 | 测试 + 实跑 | 已满足（需外部计划任务） |
| 注册限流 | 3 次/10 分钟（进程内存） | 测试 | 部分满足（多实例不共享） |

## 5. 风险登记

| ID | 风险 | 影响 | 等级 | 处置建议 | 时点 |
|---|---|---|---|---|---|
| R1 | CSRF 防护依赖“同源 + JSON-only + SameSite=lax + 无 CORS” | 条件被打破后跨站写请求可利用 | 中 | 触发 5 个条件任一时先升级双提交 Cookie/完整 token；现有守护测试会拦截代码侧变更 | 持续 |
| R2 | 注册限流口径过粗（成功与失败同桶、仅进程内存） | 共享出口 IP 的正常用户会被误伤；多实例可绕过 | 中 | **已解决**：2026-10-29 改为 `register_attempts` 落 SQLite 双桶 —— 重名失败 3 次/10 分钟、成功建号 10 次/小时，多实例共享计数；过期行由清理任务删除 | 已处置 |
| R3 | 审计可被删改、长期膨胀、整库回滚不可见 | 有库权限即可删改；回滚可抹掉近期证据 | 中 | **已缓解**：HMAC 哈希链 + `verify_audit_chain.py` 校验 + `audit_retention.py` 留存清理；水位锚点 `<db>.audit-anchor.json` 发现整库回滚（退出码 1）。锚点同机存放，需配合备份权限收敛并定期把锚点导出到外部存储 | 已处置 |
| R4 | 无自助改密/找回 | 用户只能找管理员 | 中 | 已有管理员 CLI 兜底；邮箱/短信找回明确不做 | 接受 |
| R5 | 管理员重置脚本仅依赖服务器权限，无二次审批 | 内部人员可重置任意账号 | 中 | 重置事件已审计；限制服务器登录与脚本权限 | 上线前（权限收敛） |
| R6 | SQLite 单文件，多实例写竞争 | 高并发下写入排队/超时 | 中 | 单写实例或迁移 Postgres | 延后 |
| R7 | IP 可伪造（误开 `TRAVELAI_TRUST_PROXY`） | 限流与审计失真 | 中 | 仅在代理覆写 XFF 时开启；默认关闭 | 上线前 |
| R8 | 未设置 CSP / X-Frame-Options / HSTS 等安全响应头 | 点击劫持、混合内容风险 | 低-中 | 在反向代理层统一补齐 | 上线前 |
| R9 | 应用不终止 TLS，依赖外部代理 | 配置错误会导致 Secure Cookie 失效或明文暴露 | **高** | 强制按第 6 节部署并复验 Set-Cookie | 上线前 |
| R10 | 无版本控制，报告无法绑定 commit | 问题无法追溯、回滚无基线 | 低 | **已解决**：2026-10-29 初始化 Git，以 tag `baseline-20261029` 作为基线 |
| R11 | 无 MFA、无异常登录告警 | 账号被盗后无实时发现 | 低 | 审计已可用；后续接入告警 | 延后 |

## 6. 上线前置检查清单

- [ ] `TRAVELAI_ENV=production`、`TRAVELAI_COOKIE_SECURE=true`、`TRAVELAI_AUDIT_KEY` 为至少 16 位随机串（缺失或使用默认值会直接拒绝启动）
- [ ] 反向代理终止 TLS，容器端口不直接暴露公网（模板已绑 `127.0.0.1:8000`，配置参考 `deploy/nginx.conf.example`）
- [ ] 代理覆写 `X-Forwarded-For` 后才设置 `TRAVELAI_TRUST_PROXY=true`
- [ ] 登录响应验证 `Set-Cookie` 含 `HttpOnly; Secure; SameSite=lax`，HTTPS 下会话可持久
- [ ] 代理层补齐 HSTS、CSP、`X-Frame-Options`、`X-Content-Type-Options`
- [ ] 迁移前备份 `data/backups/*.db`，并验证回滚脚本可用
- [x] 已注册每日清理任务（会话 + 失效登录计数）：`TravelAI-CleanupSessions`，03:30，日志 `logs/cleanup.log`
- [ ] 审计留存清理：`scripts/audit_retention.py --days 180`，并定期运行 `scripts/verify_audit_chain.py` 校验哈希链与锚点（输出 `anchor=ok`）
- [ ] 首次上线或审计密钥轮换后，执行 `scripts/verify_audit_chain.py --update-anchor` 建立锚点基线，并把 `<db>.audit-anchor.json` 纳入备份外的独立保管
- [ ] 审计查询权限收敛（`scripts/audit_query.py` 只读打开）
- [ ] 复跑 `pytest -q`（当前 83 passed）与 `scripts/http_smoke.py`
- [x] 已执行：legacy 数据 claim 至管理员真实账号（用户名已脱敏，2026-10-29），`legacy_trips` 现为 0

## 7. 复验命令（逐项对应第 6 节清单）

以下命令均在项目根目录 `travelai_planner` 下用 PowerShell 执行；`<域名>`、`<账号>`、`<密码>` 按实际替换。

**① 生产配置自检**

```powershell
$env:TRAVELAI_ENV='production'; $env:TRAVELAI_COOKIE_SECURE='true'
.\.venv\Scripts\python.exe -c "from app.config import settings, validate_settings; validate_settings(settings); print('config ok')"
```

**② TLS 与端口暴露**

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess
docker compose config | Select-String "8000"
Invoke-WebRequest "https://<域名>/health" -UseBasicParsing | Select-Object StatusCode
```

期望：监听地址为 `127.0.0.1`，公网仅能通过 HTTPS 访问，`/health` 返回 200。

**③ 代理头信任**

```powershell
"TRAVELAI_TRUST_PROXY=$env:TRAVELAI_TRUST_PROXY"
```

未设置或为 `false` 时安全；为 `true` 时必须确认前置代理会覆写 `X-Forwarded-For`。

**④ Cookie 属性（必须用 HTTPS 域名）**

```powershell
$r = Invoke-WebRequest "https://<域名>/api/v1/auth/login" -Method POST -ContentType 'application/json' -Body '{"username":"<账号>","password":"<密码>"}' -SessionVariable s -UseBasicParsing
$r.Headers['Set-Cookie']
```

期望同时包含 `HttpOnly`、`Secure`、`SameSite=lax`。手工拼 `Cookie` 头在 PowerShell 下不可靠，验证会话请继续使用 `$s`。

**⑤ 安全响应头**

```powershell
(Invoke-WebRequest "https://<域名>/" -UseBasicParsing).Headers.Keys | Sort-Object
```

期望包含 `Strict-Transport-Security`、`Content-Security-Policy`、`X-Frame-Options`、`X-Content-Type-Options`（由反向代理补齐）。

**⑥ 备份与迁移可回滚**

```powershell
Get-ChildItem data\backups\*.db | Sort-Object LastWriteTime -Descending | Select-Object -First 3 Name,Length,LastWriteTime
.\.venv\Scripts\python.exe scripts\migrate_auth.py --dry-run
.\.venv\Scripts\python.exe scripts\migrate_audit.py
```

`migrate_audit.py` 不带参数时只打印 DDL，不会写入。需要在副本上实际演练回滚（不动生产库）：

```powershell
Copy-Item data\travelai.db "$env:TEMP\rollback-check.db" -Force
.\.venv\Scripts\python.exe scripts\migrate_audit.py --db "$env:TEMP\rollback-check.db" --rollback
Remove-Item "$env:TEMP\rollback-check.db"
```

**⑦ 会话清理任务**

```powershell
.\.venv\Scripts\python.exe scripts\cleanup_sessions.py --dry-run --json
schtasks /query /tn "TravelAI Session Cleanup"   # Linux 用 crontab -l
```

第一条应返回 `{"dry_run": true, ...}`；第二条确认计划任务已注册。

**⑧ 审计只读查询与完整性校验**

```powershell
.\.venv\Scripts\python.exe scripts\audit_query.py --limit 5 --json
.\.venv\Scripts\python.exe scripts\verify_audit_chain.py --json   # 期望 rollback.status = ok，退出码 0
```

若输出 `missing_anchor`，执行 `--update-anchor` 建立基线；若输出 `invalid_anchor`，先确认是否轮换过 `TRAVELAI_AUDIT_KEY`，确认无误后再重建。`rollback` 或 `fork` 表示整库回滚/改写，需立即封存备份并排查。

**⑨ 自动化测试与冒烟**

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 期望 83 passed
.\.venv\Scripts\python.exe scripts\http_smoke.py
```

**⑩ legacy 归属与 NULL 检查**

```powershell
@'
import sqlite3
c = sqlite3.connect("data/travelai.db")
tables = ("trips", "trip_versions", "agent_runs")
print("null_owner =", [c.execute(f"select count(*) from {t} where owner_id is null").fetchone()[0] for t in tables])
print("legacy_trips =", c.execute("select count(*) from trips where owner_id='user-legacy'").fetchone()[0])
'@ | .\.venv\Scripts\python.exe -
```

期望 `null_owner = [0, 0, 0]`；`legacy_trips` 若大于 0，需用 `scripts/migrate_auth.py --claim-to <用户名>` 按需归属。

**启动自检反向验证（必须失败）**

```powershell
$env:TRAVELAI_ENV='production'; $env:TRAVELAI_COOKIE_SECURE='false'
.\.venv\Scripts\python.exe -c "import app.main"; "exit=$LASTEXITCODE"   # 期望非 0
$env:TRAVELAI_COOKIE_SECURE='true'
```

全部通过后恢复环境变量，避免影响本地调试。

## 8. 验收范围外

TLS 证书与代理配置、公网漏洞扫描（如 ZAP/Nuclei）、压力与并发测试、前端 XSS/依赖漏洞扫描、邮箱验证与 MFA，均未在本轮验收覆盖。

## 9. 签署

| 角色 | 姓名 | 结论 | 日期 |
|---|---|---|---|
| 安全验收 | 待填 | 有条件通过 | 2026-10-29 |
| 研发负责人 | 待填 | 待确认 | 待填 |
| 运维/发布 | 待填 | 待确认（第 6 节） | 待填 |
