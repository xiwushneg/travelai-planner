# 通勤与时长修复变更记录

范围：仅通勤（`travel_between` / 日通勤总计）与时长字段。未改动 API 路径、既有响应字段名、数据库 schema，未引入新依赖。

## 1. 问题根因

1. `travel_between` 只要任一端缺少 `latitude/longitude` 就返回 `None`。午餐、休息、无坐标锚点因此被排程循环直接跳过：既没有路段记录，也没有任何标记，`total_travel_km` 落到默认 `0.0`，看起来像“已计算且为 0”。
2. 即使把坐标继承给无坐标项，`LocalRouteProvider` 也固定叠加 `buffer_minutes`，同址路段返回 15 分钟，导致“到达午餐时间”超出午餐开始时间，整日方案被判不可行（HTTP 实测时景点被全部移出，只剩午餐）。
3. 接口没有时长字段，前端自行用起止时间换算，并对缺失值做 `|| 120` 之类兜底，把“未知”伪装成“建议值”。
4. 日通勤总计只有数字，没有“是否算全”的语义，前端无法区分“完整计算”与“部分未计入”。

## 2. 修改点

| 文件 | 改动 |
| --- | --- |
| `app/core/models.py` | 新增 `TransportGap`；`ScheduleItem.duration_minutes`（唯一时长字段，`model_validator` 由起止时间派生，历史版本读取时补齐）；`DayPlan.transport_complete`、`DayPlan.uncomputed_legs`，并按“缺失路段”反推完整性 |
| `app/core/scheduler.py` | 抽出 `_travel`（同址返回 `0 km / 0 分钟`、`basis=same_location`，不叠加缓冲）；新增 `_effective_coordinates`（景点必须自带坐标，午餐/锚点继承上一个已定位项，否则继承下一个）；通勤循环逐段计算，算不出即写 `TransportGap` 与 `warnings`，`transport_complete = not uncomputed_legs`，排序评分优先完整方案 |
| `frontend/src/components/ItineraryTimeline.vue` | 删除前端时长换算；时长直读 `duration_minutes`；无路段但有 gap 显示“通勤未计算”；日总计显示“（N 段未计入）”并逐段列出原因；票价 0 显示“免费” |
| `frontend/src/App.vue` | 顶部“通勤估算”汇总增加“（N 段未计入）”提示 |
| `frontend/src/components/SpotPicker.vue` | 移除 `|| 120` 兜底，建议时长直接来自接口 |
| `frontend/src/styles.css` | gap 文案与汇总警示的样式 |
| `tests/test_scheduler.py` | 新增 `test_lunch_cross_day_transport_is_complete`、`test_missing_spot_coordinates_reported_as_gap_not_silent_zero`（均为含午餐的跨日行程） |
| `scripts/http_smoke.py` | 临时目录 `ignore_cleanup_errors=True`；`basis` 断言允许 `same_location` |

修复过程中另排除一处自身引入的问题：内层 `gaps`（`list[TransportGap]`）遮蔽了外层同名时间窗列表，排列搜索第二轮即 `too many values to unpack`，已改名为 `uncomputed`。

## 3. 行为变化

- 时长、票价、通勤 km、通勤分钟在接口层各只有一套命名：`duration_minutes`、`ticket_cost_cny`、`distance_km`、`travel_minutes`。前端零换算、零兜底。
- 午餐/休息会得到显式的 `0 km / 0 分钟 / basis=same_location` 路段；日总计与各腿之和严格相等。
- 缺坐标时该日 `transport_complete=false`，`uncomputed_legs` 逐段给出 `from_item_id`、`to_item_id`、`reason`，`warnings` 写明“N 段通勤缺少坐标未计入总计”。
- 旧版本（历史静默跳过午餐）读取后标为“部分未计入”，不再用 0.0 冒充完整。

## 4. 验证证据

- `pytest -q`：34 passed（原 32 + 新增 2）
- `scripts/http_smoke.py`：PASS，证据见 `docs/evidence/fullstack-http-smoke.json`
- HTTP 实测（`http://127.0.0.1:8000`，前端 `npm run build` 后）：北京 2 日含午餐，第 2 天 3 项（博物馆 180 分钟 → 午餐 `0 km / 0 分钟` → 故宫 `1.26 km`），`transport_complete=true`、`uncomputed_legs=[]`、总计与各腿之和一致
- 历史版本实测：`transport_complete=false`，每日 2 条 gap（午餐一段、景点一段）

## 5. 未覆盖的边界情况

- 无坐标锚点：按 gap 记录，不做“区域中心点”兜底。
- 整天只有午餐：无位移可算，`transport_complete=true` 且 `0.0 km`，属真实无位移而非漏算。
- 历史数据完整性靠“有项缺腿”反推，无法区分“当年刻意不算”与“当年漏算”。
- 行程以午餐开头且其后没有已定位景点：仍会留 gap。
- 跨午夜、跨日锚点仍不支持（模型只允许同日窗口）。
- 在线路线降级后 `basis` 变为 `haversine_estimate_after_*`，仍计入总计，前端未单独提示。
- 前端 `dist` 必须重新构建才生效。
