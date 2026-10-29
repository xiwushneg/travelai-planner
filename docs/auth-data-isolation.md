# 最小可用认证与数据隔离

## 1. 会话方案：服务端 session

- 浏览器只持有随机 token（`travelai_session`，httpOnly / SameSite=Lax / Path=/），数据库 `sessions.token_hash` 只保存 SHA-256 摘要。
- 选择理由：本项目单机 SQLite、前端由后端托管；需要登出即撤销、可在服务端失效会话。JWT 无法在不引入黑名单表的前提下撤销，且 localStorage 方案会暴露给 XSS。
- 有效期取 `settings.access_token_expire_minutes`（默认 24 小时）；登录/注册时顺带清理过期会话。

## 2. 数据模型变更

| 表 | 变更 |
|---|---|
| `users` | 新增：`id`、`username`、`password_hash`（bcrypt）、`is_active`、`created_at`；`lower(username)` 唯一索引 |
| `sessions` | 新增：`id`、`user_id`、`token_hash`（UNIQUE）、`expires_at`、`created_at` |
| `trips` / `trip_versions` | 新增 `owner_id TEXT NOT NULL REFERENCES users(id)` |
| `agent_runs` | 新增 `owner_id TEXT REFERENCES users(id)`（历史失败运行允许为空） |

新增索引：`idx_trips_owner(owner_id, updated_at DESC)`、`idx_versions_owner`、`idx_runs_owner`、`idx_sessions_expires`。

密码使用 bcrypt（`bcrypt>=4.1`，直接调用 `hashpw/checkpw`），密码长度 8–72 字节；任何明文都不入库。

## 3. 迁移步骤

脚本：[scripts/migrate_auth.py](../scripts/migrate_auth.py)

```bash
# 1) 先演练，确认待执行动作
python scripts/migrate_auth.py --dry-run

# 2) 停掉写入方（uvicorn），再正式执行
python scripts/migrate_auth.py
```

脚本行为：

1. `PRAGMA integrity_check` 预检；
2. 备份到 `data/backups/travelai-<时间戳>.db`；
3. 建 `users` / `sessions` 表与索引（`IF NOT EXISTS`，幂等）；
4. `PRAGMA table_info` 判断缺列后 `ALTER TABLE ADD COLUMN owner_id`，并回填；
5. 创建保留账户 `legacy`（`id=user-legacy`，`is_active=0`，`password_hash='!disabled'`，无法登录）；
6. 校验 `owner_id IS NULL` 计数为 0；
7. 可选 `--claim-to <username>`：把 legacy 名下数据改判给已有账号。

服务启动时 `Repository._init_schema` 执行同一套幂等 DDL，因此全新库和测试库无需手工迁移；旧库只需跑一次脚本（或直接启动服务，会自动补列并回填 legacy）。

本次实测（`data/travelai.db`）：trips 25 行、trip_versions 29 行、agent_runs 47 行全部归属 `user-legacy`，空归属 0；备份文件 `data/backups/travelai-20261029-181451.db`。

## 4. 接口与前端行为

- 新增：`POST /api/v1/auth/register`、`POST /api/v1/auth/login`、`POST /api/v1/auth/logout`、`GET /api/v1/auth/me`。
- 行程相关接口（列表、创建、读取、版本、diff、modify-day、run）全部依赖会话；未登录返回 `401 {"detail":{"code":"UNAUTHENTICATED"}}`。
- 他人行程统一返回 `404`，响应结构与「行程不存在」一致，避免泄露存在性。
- `GET /api/v1/trips` 只返回本人行程；`/spots`、`/health`、`/evidence/search` 属公共目录，保持匿名可读。
- 前端：`api.js` 统一 `credentials: 'include'` 与 401 回调；新增登录/注册页；未登录时只渲染登录页；顶栏显示当前账号与「登出」；会话失效自动退回登录页。

## 5. 验证结果

- `pytest -q`：54 passed（原 34 项 + 6 项鉴权 + 8 项安全基线 + 6 项登录退避）。
- 新增覆盖：注册/登录/me/登出、密码以 bcrypt 哈希存储、重名中性 409、错误密码 401、未登录访问 401、越权访问他人行程 404（读取/版本/diff/修改/run/列表）、会话过期 401；注册限流 429、未知账号同样消耗 bcrypt、生产环境拒绝不安全配置、Secure Cookie 生效、代理头默认不采信、登录失败指数退避与到期恢复、成功登录重置账号计数、IP/账号维度独立。
- `python scripts/http_smoke.py`：PASS（真实 Uvicorn 进程中完成 401 → 注册 → 创建 → 修改 → 版本 → 409 → 422 → SPA 回退全链路）。
- 浏览器验证：未登录显示登录页；注册后进入工作台；点击登出回到登录页。

## 6. 安全基线加固（第二轮）

1. **登录失败退避（落库，跨进程）**：新增 `login_failures` 表与 `app/login_guard.py`；按来源 IP 与账号两个维度独立计数，失败 5 次后按 `30s * 2^(n-5)` 指数退避、封顶 30 分钟；成功登录清除该账号计数；24h 无失败自动衰减。退避命中仍返回与密码错误完全一致的 `401 INVALID_CREDENTIALS`（无 `Retry-After`），真实原因（`unknown_user` / `bad_password` / `inactive_user` / `backoff_ip` / `backoff_account`）只写入 `travelai.auth` 日志。
2. **注册限流（进程内）**：保留 `app/ratelimit.py` 滑动窗口，注册默认 3 次 / 10 分钟，超限返回 `429 RATE_LIMITED`；仅当 `TRAVELAI_TRUST_PROXY=true` 时才采信 `X-Forwarded-For`。
3. **注册重名中性化**：重名不再返回 `USERNAME_TAKEN` 与用户名，改为 `409 REGISTRATION_UNAVAILABLE` + 中性文案；真实原因只进日志。取舍：新注册仍需 201 + 会话（否则前端自动登录失效），因此状态码本身仍可区分“新增/重名”，这是不做邮箱验证下的残余可枚举性。
4. **失败耗时统一**：`app/auth.py` 增加固定 cost=12 的 `DUMMY_PASSWORD_HASH`；用户不存在、账号停用或处于退避时仍恰好执行一次 bcrypt。实测 20 次取样：已存在账号错误密码中位数 197.7ms / p95 221.9ms，不存在账号中位数 205.2ms / p95 218.9ms，中位数差 7.6ms，分布重叠。
5. **Secure Cookie 与启动自检**：`TRAVELAI_ENV=production` 且 `TRAVELAI_COOKIE_SECURE` 未开启时，`create_app` 在初始化数据库前抛错拒绝启动；`.env.example` 与 `docker-compose.yml` 的模板默认值改为 production + Secure。
6. **移除死配置**：删除未被任何代码引用的 `TRAVELAI_SECRET_KEY` 字段（会话使用随机 token + SHA-256，不依赖签名密钥）。

## 7. 上线前仍需处理的风险

1. **CSRF 维持现状（无 token）**：当前同源部署 + 写接口强制 JSON + SameSite=Lax + 无 CORS，评估为不可利用；出现非 JSON 写接口、跨子域、SameSite=None 等情况时必须升级（详见 `docs/csrf-session-lifecycle.md`）。
2. **注册限流仍是进程内状态**：登录退避已落 SQLite，多实例共享；注册限流多实例下会失效，需后续换共享存储。
3. **限流可被多 IP 轮换绕过**：账号维度退避依赖持续命中同一账号，攻击者用大量 IP 轮换且每个账号只试一两次时无法完全拦截；代理头必须仅在可信代理后开启，否则可伪造。
4. **无自助改密/找回**：管理员可用 `scripts/admin_reset_password.py` 重置（见 `docs/recovery-audit.md`）；用户侧仍无改密与找回流程。
5. **过期会话清理需外部触发**：登录时仍有兜底清理，独立脚本为 `scripts/cleanup_sessions.py`（见 `docs/csrf-session-lifecycle.md`），需由计划任务定期执行。
6. **历史演示数据不可登录查看**：已归 legacy；如需演示，用 `--claim-to` 转移到演示账号。
7. **迁移需停机窗口**：先停 uvicorn 再执行脚本，避免迁移期间写入竞争。
8. **审计仅覆盖关键事件**：登录成功/失败/退避、越权 404、密码重置、批量登出已落库（见 `docs/recovery-audit.md`）；行程修改等业务操作仍未审计，且审计无防篡改与告警能力。
9. **普通登出只撤销当前会话**：需要全端下线时使用“登出所有设备”（`/api/v1/auth/logout-all`）或管理员重置脚本；仍无按设备单独管理会话的能力。
10. **SQLite 单机限制**：并发写入与横向扩展能力有限，与本轮认证无关但影响上线规模。
