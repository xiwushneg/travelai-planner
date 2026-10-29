# TravelAI v1.1 交付验证记录

验证日期：2026-10-29。本文只记录实际执行过的检查，不把代码审阅当作运行验收。

## 结论

本版本在原 TravelAI 完整 Web 系统上增加了景点/攻略 JSONL、CSV 导入，以及默认离线的 Local、Mock、百度 DirectionLite 路线适配器。工作副本已通过编译检查、30 项自动化测试、独立 CLI 导入验收，以及真实 Uvicorn 进程上的页面/API/版本管理闭环验证。

这证明当前声明的单城市规划、目录导入和可降级路线估算链路可以运行；不等于生产级账号权限、真实旅行数据核验、百度真实公网接口、浏览器视觉兼容或高并发验收已经完成。

## 1. 编译与自动化测试

执行：

```bash
python -m compileall -q app scripts tests
pytest -q -p no:cacheprovider
```

最终结果：**30 passed, 1 warning**。唯一警告来自当前 FastAPI TestClient / Starlette 测试依赖的弃用提示，不是测试失败。

覆盖范围包括：

- 原有排程、不可行约束、确定性、服务层、局部修改、锁定日、版本冲突和结构化 diff。
- 最近行程、Web 工作台静态页面、SPA fallback、未知 API JSON 404、健康检查与来源模式。
- 四种模板的 dry-run 与正式导入：景点/攻略 × JSONL/CSV。
- dry-run 不创建数据库；首次导入新增、重复导入保持不变、修改后 upsert。
- 非法文件逐行报错、命令行退出码 2、整批不提交且不创建数据库。
- `verified` 来源缺少 URL 或最后核验日期时被拒绝。
- 导入景点参与后续排程，导入攻略参与证据检索，且不清空演示攻略。
- 本地 Haversine 算法、Mock 路线、百度响应解析与安全错误信息。
- 缺 AK 离线回退、单次规划缓存与在线限次、失败降级警告。
- 路线 `provider`、`basis`、`is_online` 来源标识进入行程。

未统计语句覆盖率，不把 30 项测试解释为完整覆盖。

## 2. 独立 CLI 导入验收

证据：[`docs/evidence/import-cli-smoke.json`](evidence/import-cli-smoke.json)。

对 `spots.example.jsonl`、`spots.example.csv`、`guides.example.jsonl`、`guides.example.csv` 分别执行预检、首次导入、重复导入，并重新读取 JSON 报告和结果文件核对：

| 检查 | 实际结果 |
|---|---|
| 四种模板 dry-run | 全部通过；`valid_records=1`；未创建数据库 |
| 四种模板首次导入 | `inserted=1, updated=0, unchanged=0` |
| 四种模板重复导入 | `inserted=0, updated=0, unchanged=1` |
| `--report` | 已写入并重新读取核对 |
| 含一条非法记录的景点文件 | 错误定位第 2 行；本批 2 条均未提交 |
| 非法文件命令行退出码 | `2` |
| 非法文件数据库副作用 | 未创建数据库 |

命令行的正常状态提示会输出到 stderr，JSON 报告输出到 stdout；验收以退出码、JSON 内容和数据库实际状态为准。

## 3. 真实 Uvicorn HTTP 验收

执行：

```bash
python scripts/http_smoke.py --output docs/evidence/fullstack-http-smoke.json
```

脚本在隔离临时目录启动真实 Uvicorn 进程，通过 `urllib` 发起 HTTP 请求，并在结束后清理服务与临时数据。

| 检查 | 实际结果 |
|---|---|
| `GET /health` | 200；`map_provider=local`；`map_mode=local_estimate` |
| 健康检查敏感信息 | 无 AK 字段；天气/路况实时能力均为 `false` |
| `GET /` | 200；返回前端 HTML |
| 前端 JS / CSS | 均返回 200，非空且内容类型正确 |
| 创建行程 | 201；行程版本为 v1 |
| 默认路线来源 | 通勤段为 `local_estimate` / `haversine_estimate` / `is_online=false` |
| 局部修改 | 200；创建 v2；锁定的第 2 天未改变 |
| 版本列表与历史版本 | `[1, 2]`；v1 仍可读取 |
| 结构化 diff | 检测到实际景点、通勤时间/费用变化与日程移动 |
| 过期版本修改 | 409；当前版本保持 v2 |
| 不满足约束的创建 | 422；返回不可行冲突，不创建版本 |
| SPA 页面路由 | 200；返回前端 HTML |
| 未知 `/api/*` | 404；JSON 错误，不返回前端 HTML |

机器可读结果保存在 [`docs/evidence/fullstack-http-smoke.json`](evidence/fullstack-http-smoke.json)。本次升级没有修改前端源码，沿用完整系统中的已构建 `frontend/dist`；HTTP 验收确认其可加载，不把它描述为新增导入界面。

## 4. 启动与数据边界核对

- 仅当景点库为空且启用 `TRAVELAI_SEED_DEMO` 时写入演示景点，不会在每次启动时覆盖已有导入目录。
- 每次创建/修改行程读取最新目录；历史行程版本保留当时的路线、票价和证据快照。
- 导入器先完成全文件解析与校验，再打开数据库写事务；dry-run 和格式错误不会触发建库。
- AK 只从后端环境读取；健康检查、行程模型与导入报告不包含 AK。

## 5. 未执行或未覆盖的验收

- **未使用真实百度 AK 调用公网 DirectionLite**。百度请求参数、响应解析和错误处理使用本地模拟 HTTP 响应测试；真实 AK 权限、配额、网络、坐标合规与服务条款仍需部署方联调。
- 未据此报告 Docker 镜像构建或 Compose 启动成功；配置已提供，但本轮未执行容器构建。
- 未执行前端生产重建、跨浏览器或真实移动设备视觉验收；本轮没有前端源码改动。
- 未进行多进程压力、崩溃恢复、权限渗透、大数据量性能或 SQLite 多实例写冲突测试。
- 无账号权限、多租户、在线预订、支付或生产运维体系；真实公网部署前必须补充认证授权、限流、审计与备份。
- 无实时天气或实时路况；一次 DirectionLite 响应不表示实时交通状态。
- 票价、开放时间、闭馆日和攻略不会由导入器自动联网核验。`verified` 仅约束来源字段完整，真实出行前仍须独立查询官方信息。
- 检索仍是本地词项匹配，不是向量数据库；没有浏览器上传控制台，导入入口为后端 CLI。
