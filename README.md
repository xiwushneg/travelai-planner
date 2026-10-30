# TravelAI · 旅行规划工作台

一个可直接操作的旅行规划 Web 系统：**Vue 3 前端 + FastAPI 后端 + 确定性排程 + SQLite 不可变版本**。

本版本不再只是后端 API。打开首页即可填写需求、生成行程、调整某一天、查看版本差异与参考资料，并导出行程 JSON；无需手写请求。

> 当前是功能完整的单城市演示系统，不是生产级在线旅游平台。内置东京演示数据，票价、开放时间和攻略未实时核验；不伪造实时天气、路况、库存或支付能力。

## 普通使用：启动一次，直接打开网页

要求 Python 3.11+。交付包已包含构建好的前端，**日常使用不需要 Node.js**。

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 **http://127.0.0.1:8000/**。

- 旅行工作台：`/`
- Swagger API 调试：`/docs`（仅开发环境；`TRAVELAI_ENV=production` 时 `/docs`、`/redoc`、`/openapi.json` 全部关闭）
- 健康检查：`/health`
- SQLite 数据：默认保存在 `./data/travelai.db`，重启后行程仍在

如果端口被占用，可把 `--port 8000` 改为其他端口，并同步修改访问地址。

## 页面上可以做什么

1. **创建行程**：填写目的地、日期、同行人，搜索和筛选候选景点，标记必去项，选择偏好。
2. **调整约束**：设置每日起止时间、景点上限、门票与市内交通预算、通勤估算参数，以及是否保留午餐和证据检索。
3. **查看日程**：按日期浏览时间线，查看游览时段、通勤估算、门票估算和规划警告。
4. **修改单日**：重新选择某一天完整的景点集合；可锁定其他日期，也可允许其他日期重排。
5. **比较版本**：查看新增、移除、移动、改时和未变化日期；可读取历史版本，历史版本只读。
6. **查看参考资料**：显示检索摘要、发布方、来源链接与核验状态。
7. **恢复与导出**：通过“最近行程”继续规划；导出当前行程 JSON。

失败不会伪装成成功：约束不可行时展示冲突原因与建议；版本过期时提示刷新；未排入的普通候选会明确列出。

## 技术结构

```text
旅行工作台（Vue 3 + Vite + Lucide）
               │ 同源 /api/v1
               ▼
FastAPI routes → PlanningService → 确定性 scheduler
                     │                    │
                     ├→ 可选本地证据检索    └→ 约束、成本与冲突
                     └→ Repository → SQLite 版本与运行记录
```

核心排程不依赖外部 LLM、Chroma 或在线服务；相同输入与景点事实得到相同结果。

## 开发与重新构建前端

只有修改 Vue 源码时才需要 Node.js 20+。

```bash
# 终端 1：后端
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload

# 终端 2：前端热更新，API 自动代理到 8000
cd frontend
npm ci
npm run dev
```

前端开发地址为 `http://127.0.0.1:5173/`。修改完成后构建：

```bash
cd frontend
npm ci
npm run build
```

`frontend/dist` 会由 FastAPI 自动托管。生产构建、源码和 lockfile 均包含在交付包中；`node_modules` 不包含。

## 测试与演示

```bash
python -m pip install -r requirements-dev.txt
pytest -q
python scripts/demo.py
python scripts/http_smoke.py --output docs/evidence/fullstack-http-smoke.json
```

- 后端、版本、检索与静态页面集成测试位于 `tests/`。
- `scripts/demo.py` 使用临时 SQLite 数据库演示 v1 → v2，不污染正式数据。
- 前端构建：`cd frontend && npm ci && npm run build`。
- 实际验证结果见 `docs/verification.md` 和 `docs/evidence/`。

## 配置

应用启动时读取环境变量，修改后需重启：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TRAVELAI_DB_PATH` | `./data/travelai.db` | SQLite 文件路径 |
| `TRAVELAI_ENV` | `development` | `production` 时启用启动自检；非法值拒绝启动 |
| `TRAVELAI_COOKIE_SECURE` | `false` | 会话 Cookie 的 `Secure`；`production` 下必须为 `true`，否则拒绝启动 |
| `TRAVELAI_TRUST_PROXY` | `false` | 仅在可信反向代理后开启，用于采信 `X-Forwarded-For` |
| `TRAVELAI_AUDIT_KEY` | `travelai-audit` | 审计用户名 HMAC 密钥；生产应设置为随机长串 |
| `TRAVELAI_SEED_DEMO` | `true` | 空景点库时写入东京演示数据 |
| `TRAVELAI_EVIDENCE` | `true` | 启用本地词法参考资料检索 |

`.env.example` 只是模板；Python 不会自动加载 `.env`，请通过 shell、容器或进程管理器注入。

## Docker

```bash
docker compose up --build
```

Dockerfile 使用 Node 构建前端，再用 Python 运行同源应用，数据存入 `travelai_data` 卷。配置已提供；是否实际构建验证以 `docs/verification.md` 为准。

### 生产部署（HTTPS + Secure Cookie）

应用只监听 HTTP，TLS 必须在反向代理终止：

1. 设置 `TRAVELAI_ENV=production`、`TRAVELAI_COOKIE_SECURE=true`、`TRAVELAI_AUDIT_KEY`（至少 16 位随机串）；任一缺失或使用默认值时启动直接失败。
2. 容器端口仅绑定 `127.0.0.1:8000`，由 Nginx/Caddy 转发并配置证书；仓库提供 `deploy/nginx.conf.example` 作为起点。
3. 反向代理需覆写 `X-Forwarded-For`；只有在此前提下才可设置 `TRAVELAI_TRUST_PROXY=true`，否则限流与审计会采信可伪造的 IP。
4. 验证方式：登录响应的 `Set-Cookie` 必须同时包含 `HttpOnly`、`Secure`、`SameSite=lax`，且通过 `https://` 访问时浏览器能保存会话。
5. 注册每日清理任务（会话 + 失效登录计数）：`powershell -ExecutionPolicy Bypass -File scripts\register_cleanup_task.ps1`；日志写入 `logs/cleanup.log`，卸载用 `Unregister-ScheduledTask -TaskName TravelAI-CleanupSessions`。容器化部署时脚本已打进镜像，改用
   `docker exec <容器> python scripts/cleanup_sessions.py --json`（重置密码、审计查询同理）。
6. 审计留存与完整性：`python scripts/audit_retention.py --days 180 --dry-run` 预览，去掉 `--dry-run` 执行；定期用 `python scripts/verify_audit_chain.py --json` 校验哈希链与锚点（断链或疑似整库回滚时退出码为 1）。旧库首次启用需执行 `python scripts/migrate_audit_chain.py --apply`（历史记录保持 legacy，仅新记录入链），随后执行 `python scripts/verify_audit_chain.py --update-anchor` 建立锚点基线；锚点文件 `<db>.audit-anchor.json` 要独立保管，不要与 `.db` 备份一起回滚。

完整验收记录见 `docs/security-acceptance.md`。

## 目录入口

- `frontend/src/App.vue`：工作台状态与完整页面流程
- `frontend/src/components/`：景点选择、日程、单日编辑、版本对比、最近行程
- `app/main.py`：应用装配、静态页面与前端 fallback
- `app/api/routes.py`：HTTP 契约
- `app/service.py`：创建、修改、比较与证据编排
- `app/core/scheduler.py`：确定性排程内核
- `app/repository.py`：SQLite、不可变版本、乐观锁与最近行程
- `docs/architecture.md`：详细分层和实际调用链
- `docs/api.md`：接口说明
- `docs/nginx-deployment-checklist.md`：公网部署 Nginx 配置检查清单

## 明确边界

- 内置东京演示数据仅用于体验；现已支持通过后端 CLI 导入景点与攻略，但没有浏览器上传控制台、多租户或完整生产运维体系。
- 默认通勤仍为球面距离和固定速度估算；可选百度 DirectionLite 仅提供一次路线距离/时长结果，不是实时路况。缺 AK、请求失败或超过限次会显式回退本地估算。
- 无实时天气、机酒库存、预订或支付；百度真实公网接口需用户自己的 AK、权限与配额，本交付未声称完成真实 AK 联调。
- 排程是确定性启发式搜索，不保证数学全局最优；搜索上限与真正无解的区分仍可改进。
- 预算仅覆盖已排景点门票与市内交通估算，不是完整旅行报价。
- 本轮没有前端源码改动，沿用已构建页面；未执行跨浏览器或真实移动设备视觉验收，详见验证报告。

真实出行前必须独立核验官方开放时间、票价、预约、天气和交通。
