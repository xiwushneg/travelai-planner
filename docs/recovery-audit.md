# 账号可恢复性最小方案与关键事件审计

本轮只做三件事：管理员重置密码、登出所有设备、关键事件审计。不做邮箱验证、短信、OAuth 或完整找回流程。

## 1. 改动说明

| 文件 | 改动 |
|---|---|
| `app/repository.py` | 新增 `audit_events` 表与 `idx_sessions_user` 索引；新增 `delete_sessions_for_user`、`get_user_exact`、`reset_password_and_revoke_sessions`、`get_trip_owner`、`get_run_owner`、`add_audit_event`、`list_audit_events`。行程/运行/用户/会话的既有查询与写入未改。 |
| `app/audit.py`（新建） | 审计写入封装：用户标识用 HMAC-SHA256(`TRAVELAI_AUDIT_KEY`, username.lower())[:32] 不可逆哈希；明细中自动剔除含 password/token/cookie/secret/authorization 的字段；事件按 HMAC 哈希链串联（`prev_hash`/`entry_hash`），写入失败只记 warning，不阻断登录。 |
| `app/api/routes.py` | 登录成功/失败/被退避分别写入 `login_succeeded` / `login_failed` / `login_blocked`；新增 `POST /api/v1/auth/logout-all`；5 个行程/运行入口（GET trip、versions、diff、modify-day、runs）先判断“存在但归属他人”，命中则写 `trip_access_denied`，响应仍是 404。 |
| `scripts/admin_reset_password.py`（新建） | 管理员重置密码：必须显式 `--confirm reset-password`；用户名精确匹配（区分大小写、禁止通配符/前后空格）；默认生成 20 位随机密码并**只打印一次**；重置与吊销会话在同一事务；写 `password_reset` 审计。 |
| `scripts/audit_query.py`（新建） | 只读审计查询（`file:...?mode=ro`），支持 `--limit`、`--event`、`--username`（自动哈希）、`--json`。 |
| `scripts/migrate_audit.py`（新建） | 增量迁移脚本，支持 dry-run、`--apply`、`--rollback`，执行前自动备份。 |
| `frontend/src/api.js`、`App.vue` | 新增 `logoutAll` 调用；顶栏“登出所有设备”入口，二次确认后强制回到登录页；已重新构建 `dist/`。 |

### 会话结构如何支撑“按用户批量失效”

`sessions` 表已有 `user_id` 列（每个登录/注册会话都归属具体用户），因此批量失效就是：

```sql
DELETE FROM sessions WHERE user_id = ?;
```

本轮补充 `idx_sessions_user` 索引，使删除为索引扫描。重置密码与登出所有设备都复用这条路径：前者在同一事务里先改哈希再删会话，后者直接按当前登录用户删除。**不需要改表结构即可支持批量失效。**

## 2. 迁移 SQL

```sql
-- 会话索引（按用户批量失效）
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- 审计表
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    actor_key TEXT,              -- 用户名不可逆哈希
    event_type TEXT NOT NULL,
    ip TEXT,
    result TEXT NOT NULL,
    detail TEXT                  -- JSON，仅非敏感字段
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_key, created_at);
```

执行（不丢数据，纯 ADD）：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_audit.py            # 只打印 DDL 与统计
.\.venv\Scripts\python.exe scripts\migrate_audit.py --apply    # 自动备份后执行
```

脚本会先备份到 `data/backups/travelai-audit-<时间戳>.db`；`CREATE ... IF NOT EXISTS` 可重复执行。应用启动时也会自动建表，脚本用于显式迁移与留档。

## 3. 回滚方式

```powershell
.\.venv\Scripts\python.exe scripts\migrate_audit.py --rollback
```

等价 SQL（同样先备份）：

```sql
DROP INDEX IF EXISTS idx_audit_actor;
DROP INDEX IF EXISTS idx_audit_created;
DROP TABLE IF EXISTS audit_events;
DROP INDEX IF EXISTS idx_sessions_user;
```

回滚只删除本轮新增对象，不触碰 `users`、`sessions` 行、`trips`、`trip_versions`、`agent_runs` 等已有数据。若需要恢复数据本身，用备份文件覆盖停机状态下的数据库。

## 4. 测试结果

- `pytest -q`：**59 passed**（原 34 项核心 + 既有认证/安全测试全部保留 + 本轮 5 项新增）。
- 新增覆盖：
  1. 重置脚本要求显式确认、拒绝错误确认、拒绝大小写不匹配的用户名、dry-run 不改数据；
  2. 重置后旧会话全部失效、旧密码不可登录、新随机密码可登录、密码只在 stdout 出现一次且不进入审计；
  3. 登出所有设备后另一设备的会话返回 401；
  4. 审计包含登录成功/失败/被退避/越权/密码重置/批量登出六类事件，且不包含密码、会话 token、Cookie 名、原始用户名；
  5. 审计查询 CLI 只读可用、按事件过滤正确。
- `scripts/http_smoke.py`：PASS。
- 真实进程验证：`admin_reset_password.py` 重置临时账号后旧 Cookie 立即 401；`logout-all` 返回 `revoked=2`，两个客户端均 401；`audit_query.py --username` 可命中该账号的登录与登出事件（标识为哈希值）。

## 5. 审计能回答什么

- 某账号在某时间段内的登录成功/失败次数，以及失败原因分类（未知账号、密码错误、停用、IP 退避、账号退避）。
- 是否有人尝试访问不属于自己的行程/运行（含目标 ID、路径、方法、来源 IP）。
- 某账号的密码是否被管理员重置、重置时吊销了多少会话。
- 某账号是否主动执行了“登出所有设备”。
- 事件是否来自 CLI（`ip="cli"`）还是 HTTP（真实 IP）。
- 按哈希标识关联同一账号的多次事件（`audit_query.py --username` 自动哈希比对）。

## 6. 审计不能回答什么

- **不能还原原始用户名/IP 归属**：用户名只存哈希；IP 是直连或（开启信任代理时）XFF 首段，NAT/代理后无法定位到自然人。
- **不能证明“没有发生”**：审计写入是尽力而为（失败只告警），极端情况下可能缺行；哈希链只能发现单条记录被改写或删除，整库回滚到旧备份仍无法识别。
- **不能覆盖未记录的行为**：注册、普通登出、行程读取/修改本身不记录，只有越权 404 与登录相关事件入库。
- **不能做实时告警**：只有落库与查询，没有阈值告警与聚合报表；留存清理由 `scripts/audit_retention.py` 手动或计划任务触发。
- **不能替代会话审计**：没有记录会话 ID/设备指纹，无法回答“哪台设备在使用”。
- **不能证明密码安全**：审计不记录密码、token、Cookie，因此无法从审计判断是否发生凭据泄露，只能看到使用行为。
- **不是合规级审计**：哈希链只能发现单条篡改，无 WORM 存储、无外部时间戳，仅适合最小排查用途。

## 7. 剩余风险与后续

1. 审计留存：`scripts/audit_retention.py --days 180` 已提供；上线时纳入计划任务，并定期 `scripts/verify_audit_chain.py` 校验哈希链。
2. 管理员脚本本身无二次身份校验，依赖服务器登录权限（建议限制为运维账号可执行）。
3. 越权审计依赖额外一次 owner 查询，高并发下有轻微开销。
4. 自助改密/忘记密码仍未提供；当前恢复路径只有管理员重置。
5. 多实例部署时审计写入走同一 SQLite，写竞争会增加，需要迁移到集中存储。
