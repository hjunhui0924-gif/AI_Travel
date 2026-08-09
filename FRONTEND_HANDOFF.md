# AI_Agent 后端—前端交接

> 本文以当前后端代码为准。前端只负责展示和用户交互，不从 Markdown 猜测日期、地点或行程项，也不直接调用高德、12306、航班、天气或 Tavily。

## 1. 产品边界

产品是面向“懒人”的旅行规划 Agent：用户用自然语言说明大致出发时间、目的地、人数和偏好，后端负责补齐信息、查询可用 adapter、安排每日清单、保存版本，并在用户修改要求后重规划。

联网搜索是显式开关：`search_enabled=false` 时后端不得调用网页搜索；`true` 时网页搜索只用于发现地点和热度信号。网页候选必须经过地图/POI 验证才进入地点卡。不要显示或保存大众点评评价正文，也不要把“网红/热门”当成评分。

## 2. 会话与请求通用规则

所有请求使用同源 `fetch`，不要手动读取 HttpOnly cookie；如前后端跨域部署，必须配置 `credentials: "include"` 并由部署层配置 CORS。

### 登录用户

- 用户线程 ID 通常形如 `thread_<...>`，由 `POST /threads` 返回。
- 认证 cookie：`ai_agent_session`，HttpOnly。
- 登录用户不能使用 `guest_...` 命名空间。

### 匿名用户

- 前端生成或复用形如 `guest_<十六进制字符串>` 的 `thread_id`。
- 首次访问 guest 线程时，后端设置 `ai_agent_guest` HttpOnly capability cookie；前端不需要也不能读取它。
- 生产 HTTPS 部署时设置环境变量 `AI_AGENT_COOKIE_SECURE=true`（或 `APP_ENV=production`/`ENVIRONMENT=production`），后端会为登录和 guest cookie 设置 `Secure`；本地 HTTP 开发默认不设置该标志。
- 后续请求必须由浏览器自动携带该 cookie。没有 cookie、cookie 错误或线程已归属其他用户时，旅行接口返回 404。
- guest capability 由服务端同时校验创建时间和最近使用时间，超过 30 天后旧 token 不再有效；空线程可重新获得新 capability，已有计划/回合的过期线程不会被静默重新绑定。
- 登录用户不要继续向同一接口发送 guest 线程 ID；登录后应切换到账户线程。

## 3. 认证接口

### `GET /auth/me`

返回当前登录状态：

```json
{
  "status": "success",
  "authenticated": true,
  "user": {
    "id": 7,
    "username": "alice",
    "display_name": "Alice",
    "avatar_label": "A",
    "created_at": "2026-08-09T10:00:00+00:00"
  }
}
```

未登录时 `authenticated=false`、`user=null`。

### `POST /auth/register` / `POST /auth/login`

使用 `multipart/form-data`：`username`、`password`，注册可附带 `display_name`。成功后设置 `ai_agent_session`，响应为 `{status, user}`。失败为 `{status:"error", message}`，注册参数错误 400，登录失败 401。

### `POST /auth/logout`

清除登录 cookie，返回 `{status:"success"}`。它不会自动删除 guest capability。

### `POST /threads`

登录后创建账户线程，返回：

```json
{
  "status": "success",
  "thread": {
    "thread_id": "thread_ab12cd34",
    "title": "新对话",
    "created_at": "...",
    "updated_at": "..."
  }
}
```

### `GET /sessions`

登录后返回当前用户的账户线程列表：`{status:"success", sessions:[...]}`。

### `POST /threads/migrate-legacy`

登录用户主动迁移自己仍持有的旧 checkpoint 会话。请求必须显式提供旧线程 ID；后端不会把所有无归属旧历史自动分配给当前用户，也不会接受不在旧 checkpoint 列表中的 ID。为避免旧的 `default` 等共享命名造成越权，当前接口只接受 `guest_...` 随机命名空间；其他旧 ID 需要人工恢复。

```json
{"thread_ids":["guest_old_uuid"]}
```

返回：

```json
{
  "status":"success",
  "migrated_thread_ids":["guest_old_uuid"],
  "skipped_thread_ids":[]
}
```

旧 checkpoint 没有历史 capability 绑定，只有真正知道旧线程 ID 的用户才能发起这一步；不要在登录后自动批量调用。

## 4. 聊天 SSE 接口

### `POST /chat`

请求为 `multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `message` | string | 否 | 用户自然语言问题；只上传附件时可为空 |
| `thread_id` | string | 是 | 账户线程或 guest 线程 |
| `search_enabled` | boolean string | 否 | `true` 才允许网页搜索，默认 `false` |
| `files` | file[] | 否 | 后端支持的附件类型 |

响应 `Content-Type` 为 `text/event-stream`。每条 SSE 由 `event:` 和 `data:` 组成，`data` 是 JSON。

事件：

```text
event: activity
data: {"stage":"tool","title":"...","detail":"...","state":"completed","timestamp":"..."}

event: source
data: {"title":"西湖","url":"https://...","summary":"...","source_date":"...","evidence_id":"..."}

event: text
data: {"delta":"增量文本"}

event: done
data: {
  "ok": true,
  "final_text": "供聊天区展示的完整文本",
  "activities": [],
  "sources": [],
  "trip_plan": null,
  "attachments": []
}
```

- `activity` 可用于显示“正在分析、查询地点、整理行程”等过程；不要把 `detail` 当作事实字段。
- `source` 和 `done.sources` 只用于来源卡；优先用 `evidence_id` 去重。来源可能是来源卡字段，也可能是完整 `Evidence` 字段，未知字段应忽略。
- `text.delta` 只做聊天文本增量拼接。
- 旅行请求完成时 `done.trip_plan` 为结构化 TravelPlan；普通聊天为 `null`。
- `done.final_text` 是可直接展示的文本，不包含内部 metadata 标记。前端不要从它解析日历。
- 发生异常时收到 `event: error`，数据为 `{ "message": "..." }`；可能没有 `done`，前端应结束 loading 并保留已收到内容。

推荐处理逻辑：以 `done` 作为本轮结束信号；如果 `done.trip_plan` 非空，直接刷新计划卡、日历和来源卡。不要仅凭某一条 `activity` 判断计划已保存。

## 5. TravelPlan 结构

`done.trip_plan`、计划查询接口和重规划接口中的 `plan` 字段结构相同：

```json
{
  "plan_id": "plan_xxx",
  "thread_id": "guest_xxx",
  "version": 1,
  "timezone": "Asia/Shanghai",
  "start_date": "2026-09-02",
  "end_date": "2026-09-04",
  "requested_days": 3,
  "projected_days": 3,
  "calendar_truncated": false,
  "projection_end_date": "2026-09-04",
  "origin": "上海",
  "destination": "杭州",
  "travelers": 2,
  "preferences": ["美食", "慢游"],
  "summary": "...",
  "days": [],
  "transport_options": [],
  "out_of_range_items": [],
  "facts": [],
  "constraints": [],
  "conflicts": [],
  "risks": [],
  "alerts": [],
  "diagnostics": [],
  "adapter_status": {
    "rail": "empty",
    "flight": "not_configured",
    "route": "not_requested",
    "poi": "success",
    "weather": "success",
    "web_search": "disabled"
  },
  "sources": [],
  "search_enabled": false,
  "status": "draft",
  "created_at": "...",
  "updated_at": "...",
  "previous_version": null
}
```

`calendar_truncated=true` 时，`end_date` 仍是真实请求结束日期，`days` 只投影当前前 31 天；使用 `projection_end_date` 和 `projected_days`，不要从 `risks` 文本推断。

### PlanDay

```json
{
  "date": "2026-09-02",
  "day_number": 1,
  "title": "第 1 天",
  "summary": "西湖、附近餐厅",
  "items": [],
  "has_conflicts": false
}
```

### PlanItem

```json
{
  "item_id": "item_xxx",
  "item_type": "transport",
  "title": "前往 杭州：G123",
  "date": "2026-09-02",
  "start_time": "23:10",
  "end_time": "01:20",
  "location": "",
  "address": "",
  "detail": "rail；2小时10分",
  "status": "suggested",
  "locked": false,
  "source_ids": ["transport_001_rail"],
  "estimated_cost": "",
  "travel_minutes": null,
  "confidence": "source_backed",
  "end_date": "2026-09-03",
  "is_demo": false,
  "seat_count": 10
  }
  ```

`seat_count` is the provider-reported available quantity for the selected
fare. `null` means no reliable count was supplied. It is informational only:
it does not hold a seat, issue a ticket, or guarantee booking.

### `TravelPlan.transport_options`

The complete provider-backed candidate list is structured here; do not parse
the rendered chat text. Each option includes `mode`, `title`, `depart_date`,
`arrive_date`, `depart_time`, `arrive_time`, `duration`, `price`, `provider`,
`seats` (airline/cabin/airport labels), `seat_count`, `source_ids`, and
`is_demo`. `source_ids` point to `TravelPlan.sources`. A non-null
`seat_count` is still only a query-time provider quantity and never a booking
or ticket guarantee.

```json
{
  "mode": "flight",
  "title": "MU6549",
  "depart_date": "2026-08-16",
  "arrive_date": "2026-08-17",
  "depart_time": "23:30",
  "arrive_time": "00:40",
  "duration": "1h10m",
  "price": "230",
  "provider": "VariFlight",
  "seats": ["MU", "经济舱", "上海浦东(PVG) T1 -> 杭州萧山(HGH) T3"],
  "seat_count": 10,
  "source_ids": ["transport_001_flight"],
  "is_demo": false
}
```

`date` 是开始日期；跨午夜交通另有 `end_date`，并且如果抵达日期在当前日历投影内，抵达日会有 `item_type="transport_arrival"` 的清单项。`is_demo=true` 时必须明显标记“演示数据，不可购票”。

`out_of_range_items` 保存重规划后仍需保留、但原始日期不在当前日历投影范围内的 confirmed/locked/booked 项（例如已确认酒店或车票）。它们不会被塞进错误日期的 `days[].items`，前端应在冲突区域单独展示原始 `date`，并允许通过同一个 PATCH 接口解锁或取消。

计划项状态：`suggested`、`confirmed`、`booked`、`skipped`、`cancelled`。`confirmed/booked` 必须 `locked=true`；后端重规划会保留 `locked` 或 `confirmed/booked` 项并把日期越界写入 `conflicts`。

### Evidence 与地点评分

`TravelPlan.sources` 中的 `Evidence` 至少包含：`evidence_id`、`source_type`、`provider`、`title`、`url`、`retrieved_at`、`valid_until`、`freshness`、`reliability`、`supports`、`is_demo`。PlanItem 用 `source_ids` 关联它们。

POI 卡使用 `name`、`category`、`address`、`distance`、`rating`、`rating_source`、`estimated_cost`、`opening_status`、`popularity_signal`、`popularity_source`、`freshness`、`source_ids`、`website_url`。`rating` 为空时不要显示“暂无评分”以外的推断；`popularity_signal` 是热度信号，不等于评分；网页发现未通过地图验证的地点不会进入 POI 卡。

## 6. 旅行查询接口

所有下列接口都要求账户线程的登录归属或 guest capability cookie。

### `GET /travel/plans/{thread_id}`

返回当前计划和版本摘要：

```json
{
  "status": "success",
  "plan": {"...": "TravelPlan"},
  "versions": [
    {"plan_id":"plan_xxx","version":2,"change_summary":"少走路","created_at":"..."},
    {"plan_id":"plan_xxx","version":1,"change_summary":"首次生成","created_at":"..."}
  ]
}
```

没有计划时 `plan=null`、`versions=[]`。

### `GET /travel/plans/{thread_id}/calendar`

用于小日历，不需要拉取完整 PlanItem：

```json
{
  "status":"success",
  "plan_id":"plan_xxx",
  "version":2,
  "timezone":"Asia/Shanghai",
  "requested_days":3,
  "projected_days":3,
  "calendar_truncated":false,
  "projection_end_date":"2026-09-04",
  "out_of_range_item_count":0,
  "days":[
    {
      "date":"2026-09-02",
      "day_number":1,
      "title":"第 1 天",
      "summary":"西湖",
      "item_count":2,
      "has_conflicts":false
    }
  ]
}
```

前端按 `days[].date` 高亮日历；没有对应项的日期不高亮。`has_conflicts=true` 应有独立冲突提示。`out_of_range_item_count>0` 表示完整计划中的 `out_of_range_items` 有内容；日历投影不会为这些日期生成虚假的日期格子。

### `GET /travel/plans/{thread_id}/days/{YYYY-MM-DD}`

返回当天完整清单：`{status, plan_id, version, day}`。日期不在当前投影范围时 404，不要用聊天文本补全。

### `GET /travel/plans/{thread_id}/versions/{version}`

返回指定版本：`{status:"success", plan: TravelPlan}`。不存在或无权限均为 404。

## 7. 修改计划项与重规划

### `PATCH /travel/plans/{thread_id}/items/{item_id}`

JSON 请求：

```json
{
  "locked": true,
  "status": "confirmed",
  "expected_version": 2
}
```

`locked` 和 `status` 至少提供一个。成功返回：

```json
{"status":"success","plan":{"...":"新版本"},"item":{"...":"更新后的 PlanItem"}}
```

每次成功修改都会生成新版本。强烈建议始终发送从最近一次 GET 或 `done.trip_plan` 读到的 `expected_version`；省略时后端仍会以本次读取到的版本做 CAS，但并发场景不如显式传递清晰。

版本过期返回 HTTP 409：

```json
{
  "status":"error",
  "message":"旅行计划版本已变化：expected=2, current=3",
  "current_version":3
}
```

收到 409 时重新 GET 当前计划，让用户确认后再提交，不能静默覆盖。

### `POST /travel/plans/{thread_id}/replan`

JSON 请求：

```json
{
  "message":"第二天少走路，把晚上的景点换成室内活动",
  "search_enabled":true,
  "expected_version":2
}
```

成功返回：`{status:"success", plan: TravelPlan, final_text: string, sources: Evidence[]}`。这是普通 JSON 请求，不是 SSE。后端会保留已确认/锁定项、增加新版本，并把无法满足的日期变化写入 `conflicts`。

## 8. adapter_status 与前端展示

不要仅凭 `items=[]` 判断失败。状态含义：

- `success`：取得至少一个可用结果。
- `empty`：已查询，但没有结果。
- `failed`：调用过程中失败或超时。
- `not_configured`：能力未配置/未启用，例如航班 MCP 或高德 key。
- `not_requested`：本轮不需要此 adapter。
- `disabled`：用户关闭了网页搜索。
- `partial`：部分搜索/来源成功，部分失败。

`diagnostics` 面向调试信息，`alerts` 面向用户可读提醒，`risks` 是计划层风险，三者都应保留但视觉层级可以不同。`status="needs_attention"` 时不要展示成无风险完成态。

## 9. 错误与刷新策略

- 认证接口：按 HTTP 状态码处理 400/401。
- 旅行查询：无计划、无权限、无版本通常为 404。
- 修改/重规划：版本冲突为 409，读取 `current_version`。
- 重规划未生成计划为 502。
- 普通后端异常通常为 500，正文为 `{status:"error", message}`。
- SSE 解析错误事件后停止当前 loading；不要重复提交同一请求。

计划来源优先级：`done.trip_plan` > `GET /travel/plans/{thread_id}` > 日历/日期接口。每次 PATCH/replan 成功后用响应中的完整 `plan` 更新本地版本号，避免继续使用旧 `expected_version`。

## 10. 当前不应实现的前端行为

- 不从 `final_text`、`summary` 或来源摘要提取日期和 PlanItem。
- 不自行排序评分、距离或地点热度。
- 不把网页搜索标题直接当作已验证地点。
- 不把 `rating`、`popularity_signal`、`opening_status="unknown"` 混为一谈。
- 不隐藏 `risks`、`conflicts`、`adapter_status` 或 `is_demo`。
- 不在前端保存或展示大众点评评价正文。
- 不修改 `static/` 现有文件作为本阶段后端交接的一部分；接入时请另行评估 UI 改造范围。
