# 数据导入与地图路线扩展

## 1. 两种方式各自解决什么问题

- **JSONL / CSV 导入**：管理你自己的景点与攻略目录。景点参与确定性排程，攻略参与本地证据检索。
- **地图适配器**：只估算景点之间的通勤距离和时间，不替代景点目录，也不负责核验开放时间、票价或攻略真实性。
- 默认仍是 `local` 离线估算；没有配置百度服务端 AK 时不会访问百度服务。

## 2. 准备数据文件

可直接复制 `data/templates/` 下的四份模板：

| 文件 | 用途 |
|---|---|
| `spots.example.jsonl` | 景点 JSONL，每行一个完整 JSON 对象 |
| `spots.example.csv` | 景点 CSV，首行必须是表头 |
| `guides.example.jsonl` | 攻略 JSONL |
| `guides.example.csv` | 攻略 CSV |

模板使用虚构的“示例城”，仅用于验证流程，不是实际旅行信息。编码统一为 UTF-8；CSV 可用 UTF-8 BOM。JSONL 空行会忽略，JSON 数组不是 JSONL 的替代格式。

### 2.1 景点字段

| 字段 | 必填 | 说明 |
|---|---:|---|
| `id` | 是 | 全局唯一字符串；重复导入同一 ID 表示更新，不是新增副本 |
| `name` / `city` | 是 | 名称与目的地城市；城市需与创建行程的 `destination` 一致 |
| `category` | 是 | 分类，如 `park`、`museum`、`亲子` |
| `latitude` / `longitude` | 否 | WGS84 数值坐标；缺少任一点坐标时无法计算该段通勤 |
| `address` / `rating` | 否 | 地址与评分 |
| `visit_duration_minutes` | 否 | 建议停留分钟数，默认 120 |
| `tags` | 否 | JSONL 中为数组；CSV 中用 `|` 分隔，如 `公园|亲子` |
| `adult_ticket_cny` | 是 | 成人票价，单位人民币元 |
| `child_ticket_cny` / `senior_ticket_cny` | 否 | 儿童、老人票价 |
| `opening` | 是 | 开放时间对象，见下文 |
| `source` | 是 | 来源与核验状态，见下文 |

`opening` 的真实结构是：

```json
{
  "open_time": "08:00",
  "close_time": "18:00",
  "closed_weekdays": [0],
  "closed_dates": ["2027-01-01"]
}
```

`closed_weekdays` 中周一为 0、周日为 6。CSV 中 `opening` 单元格放完整 JSON，并按 CSV 规则将内部双引号写成两个双引号。

### 2.2 攻略字段

必填：`id`、`title`、`city`、`content`、`source`；可选：`tags`。攻略只进入证据检索，不会被当作已核验事实。

### 2.3 来源规则

```json
{
  "status": "unverified",
  "name": "来源名称",
  "url": "https://example.com/source",
  "last_verified_at": "2026-10-29",
  "notes": "核验范围或备注"
}
```

- `status` 可为 `verified`、`third_party`、`estimated`、`unverified`。
- 标记 `verified` 时，必须同时提供 `source.url` 和 `source.last_verified_at`。导入器只检查字段完整性，不会替你访问网页证明内容真实。
- CSV 使用点号列名：`source.status`、`source.name`、`source.url`、`source.last_verified_at`、`source.notes`。
- 未知字段会被拒绝，避免把 PDF 示例中的旧字段悄悄写进数据库。

## 3. 先预检，再导入

在项目根目录执行。请把 `./data/travelai.db` 换成你的实际数据库路径；正在使用的数据库建议先备份。

```bash
# 1) 景点预检：不写数据库，错误报告可单独保存
python scripts/import_data.py spots ./spots.jsonl \
  --db ./data/travelai.db \
  --dry-run \
  --report ./spots.dry-run-report.json

# 2) 预检无错误后正式导入
python scripts/import_data.py spots ./spots.jsonl \
  --db ./data/travelai.db \
  --report ./spots.import-report.json

# 3) 攻略同样操作
python scripts/import_data.py guides ./guides.csv \
  --db ./data/travelai.db \
  --dry-run
python scripts/import_data.py guides ./guides.csv \
  --db ./data/travelai.db
```

`--db` 默认读取 `TRAVELAI_DB_PATH`，未设置时为 `./data/travelai.db`。`--dry-run` 不会创建或修改数据库。

### 3.1 返回码与报告

- 返回码 `0`：预检通过，或正式导入事务已提交。
- 返回码 `2`：文件、格式或记录校验失败；不会写入本批任何记录。
- 标准输出与 `--report` 文件均为 JSON，包含 `total_records`、`valid_records`、`inserted`、`updated`、`unchanged` 和带行号的 `errors`。
- 严格模式：只要一条记录有错，整批不提交。`rejected_records` 表示本批未提交的总记录数，不只是格式错误的行数。
- 同一文件出现重复 ID 也会被拒绝。
- 正式导入使用单事务幂等 upsert：新 ID 记为 `inserted`，内容变化记为 `updated`，完全相同记为 `unchanged`。

## 4. 导入后的生效方式

- 每次创建或修改行程都会读取数据库中的最新景点与攻略，不必重启服务。
- 导入景点可参与后续规划；导入攻略与演示/自定义攻略按 ID 合并，数据库中同 ID 攻略优先。
- **历史行程版本不会被回写**。已保存版本保留当时的景点、票价、路线和证据快照；需要新数据时，应基于当前版本创建一个新版本。
- 演示种子用于快速体验，不是生产数据。正式使用时设置 `TRAVELAI_SEED_DEMO=false`，并导入自己的目录。

## 5. 地图模式配置

在启动后端前设置环境变量，修改后重启服务：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TRAVELAI_MAP_PROVIDER` | `local` | `local`、`mock` 或 `baidu` |
| `BAIDU_MAP_AK` | 空 | 百度地图服务端 AK；只放在后端环境，不提交到仓库 |
| `BAIDU_ROUTE_MODE` | `walking` | DirectionLite 服务：`walking`、`driving` 或 `transit` |
| `TRAVELAI_MAP_TIMEOUT_SECONDS` | `4` | 单次在线请求超时，允许 0.5–20 秒 |
| `TRAVELAI_MAP_MAX_CALLS_PER_PLAN` | `40` | 单次规划的去重在线调用上限，允许 0–200 |

### 默认本地模式

```bash
export TRAVELAI_MAP_PROVIDER=local
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

本地模式沿用原排程算法：Haversine 球面距离 ÷ 行程中的平均速度，再加通勤缓冲。它不是道路导航。

### 百度 DirectionLite 模式

```bash
export TRAVELAI_MAP_PROVIDER=baidu
export BAIDU_MAP_AK='你的服务端AK'
export BAIDU_ROUTE_MODE=walking
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

请根据实际请求方式选择 `BAIDU_ROUTE_MODE`，并确保百度控制台已开通对应 Web 服务、AK 类型和配额。这里的全局路线服务应与你的出行方式一致；若行程的 `transport.mode` 与该配置不同，应以实际百度路线来源标识为准，不要把它理解为另一种交通方式的结果。

- 请求 `coord_type=wgs84`，读取首条路线的 `distance`（米）与 `duration`（秒），再加应用通勤缓冲。
- 百度 DirectionLite 的一次响应不表示实时路况；健康检查中的 `realtime_traffic` 始终为 `false`。
- AK 不进入前端、行程版本或错误报告。日志中也不要打印环境变量或完整百度请求 URL。
- 本适配器不把百度返回的景点、路线或攻略批量灌入本地目录；在线路线结果仅在单次规划内缓存。

### 降级与可观测性

- 配置 `baidu` 但缺少 AK：直接回退本地估算，不访问网络。
- 超时、网络错误、非成功状态或响应结构异常：受影响路段回退本地估算，并在行程 `warnings` 中说明。
- 达到单次规划在线调用上限：后续未缓存路段回退本地估算。
- 每个通勤段的 `transport_from_previous` 包含：
  - `basis`：`haversine_estimate`、`baidu_directionlite`、`mock_route` 或降级原因；
  - `provider`：`local_estimate`、`baidu_directionlite`、`mock` 或 `local_fallback`；
  - `is_online`：该路段是否来自本次在线地图结果。
- `GET /health` 仅返回非敏感的 `map_provider` 与 `map_mode`，可用于确认是否发生缺 AK 回退。

`mock` 是确定性离线测试模式，不访问网络，也不应用于假装真实在线路线。

## 6. 运维建议

1. 先在临时数据库验证模板和真实数据，再对正式库导入。
2. 导入前备份 SQLite 数据库及其 WAL 文件；导入后查看 JSON 报告和 `/health`。
3. 景点开放时间、票价、闭馆日和攻略来源需要定期重新核验；最后核验日期不是永久有效证明。
4. 百度 AK 设置合理配额、服务端权限与告警；不要将 AK 放进 Vue、浏览器请求、截图或压缩包。
5. 大量数据可拆成语义明确的批次逐批预检和导入；每批内部仍是全量校验、单事务提交。
