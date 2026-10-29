# Web 与 API 说明

日常用户直接打开 `/` 使用工作台，无需调用 API。以下接口供二次开发与调试，在线 Schema 以 `/docs` 和 `/openapi.json` 为准。

## 页面与健康

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 已构建的 Vue 旅行工作台；未构建时返回构建提示 |
| GET | `/assets/*` | 前端 JS/CSS 静态资源 |
| GET | `/health` | 服务、规划器与实时能力状态 |
| GET | 非 API 前端路径 | SPA fallback 到首页 |
| GET | 未知 `/api/*` | JSON 404，不返回前端 HTML |

`realtime_weather` 与 `realtime_traffic` 当前固定为 `false`。`/health` 另返回非敏感的 `map_provider` 和 `map_mode`：默认本地模式分别为 `local` 和 `local_estimate`；缺 AK 回退时 `map_mode` 为 `local_fallback_baidu_ak_missing`。响应不会返回 AK。百度 DirectionLite 的一次路线响应也不表示实时路况。

## 基础数据与最近行程

### `GET /api/v1/spots?city=东京`

返回 `items`、`total`、`facets`。景点事实包含营业窗口、推荐时长、票价、标签与来源状态。`city` 可选。

### `GET /api/v1/trips?limit=20`

返回最近行程摘要，供工作台“最近行程”恢复。`limit` 为 1–50。

```json
{
  "trips": [{
    "trip_id": "trip-...",
    "version": 2,
    "destination": "东京",
    "start_date": "2027-04-01",
    "end_date": "2027-04-02",
    "updated_at": "2026-10-28T...",
    "day_count": 2
  }]
}
```

## 创建行程

`POST /api/v1/trips`

```json
{
  "spec": {
    "destination": "东京",
    "start_date": "2027-04-01",
    "end_date": "2027-04-02",
    "party": { "adults": 2, "children": 0, "seniors": 0 },
    "selected_spot_ids": ["tokyo-sensoji", "tokyo-ueno-park"],
    "must_include_spot_ids": ["tokyo-sensoji"],
    "max_spots_per_day": 2,
    "include_lunch": true
  },
  "include_evidence": true
}
```

成功返回 `201`，包含 `run_id`、`trip_id`、`version` 和 `result.plan`。

- `party` 总人数至少 1。
- `pace`：`relaxed/balanced/packed`，默认每日景点上限为 2/3/4；`max_spots_per_day` 可覆盖。
- `anchors`：不可移动的预约或交通事项。
- `transport`：平均速度、缓冲与每公里估算成本。
- `budget_cny`：只约束 `budget_scope` 描述的范围，默认不含机酒餐饮。

## 读取、历史与比较

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/trips/{trip_id}` | 当前版本 |
| GET | `/api/v1/trips/{trip_id}?version=1` | 指定历史版本 |
| GET | `/api/v1/trips/{trip_id}/versions` | 版本号与创建时间 |
| GET | `/api/v1/trips/{trip_id}/diff?from_version=1&to_version=2` | 景点级差异 |

diff 包含 `added`、`removed`、`moved`、`rescheduled` 和 `unchanged_days`，只读比较不会重新排程。

## 修改某一天

`POST /api/v1/trips/{trip_id}/modify-day`

```json
{
  "expected_version": 1,
  "day_number": 2,
  "desired_spot_ids": ["tokyo-shibuya-sky"],
  "lock_other_days": true,
  "include_evidence": true
}
```

成功返回 `200`、新版本和 `diff`。

- `desired_spot_ids` 是目标日完整集合，数组顺序不是强制游览顺序。
- 空数组可清空目标日普通景点；锚点与午餐仍可保留。
- `lock_other_days=false` 允许其他日期重排，但目标日集合仍精确。
- 重复景点或与其他锁定日冲突会返回 `422`。
- 旧版本返回 `409`，前端应读取最新版本后由用户重新确认，不自动覆盖。

## 运行与证据

- `GET /api/v1/runs/{run_id}`：请求、状态、错误、结果、行程与时间；当前 API 同步执行。
- `POST /api/v1/evidence/search`：`city/query/top_k`，返回城市硬过滤的本地词法检索结果；`top_k` 为 1–10。

## 错误约定

| HTTP | 场景 | 响应 |
|---|---|---|
| 404 | 行程、版本或运行不存在 | `detail` |
| 409 | 版本已过期 | `code=VERSION_CONFLICT`、当前与期望版本 |
| 422 | 模型或修改参数校验失败 | FastAPI 校验详情或文本 `detail` |
| 422 | 排程不可行 | `detail.code=PLANNING_INFEASIBLE`、`run_id`、`conflicts` |

不可行响应不返回 `plan`，也不创建新版本。常见冲突：`unknown_spot`、`required_spot_unavailable`、`must_spot_unavailable`、`contradictory_constraints`、`capacity_exceeded`、`no_feasible_schedule_found`、`budget_exceeded`。
