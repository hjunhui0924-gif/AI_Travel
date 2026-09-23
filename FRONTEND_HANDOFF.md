# AI_Agent 后端—前端交接

> 本文以当前后端代码为准。前端只负责展示和用户交互，不从 Markdown 猜测日期、地点或行程项，也不直接调用高德、12306、航班、天气或 Tavily。

## 1. 产品边界

产品是面向“懒人”的旅行规划 Agent：用户用自然语言说明大致出发时间、目的地、人数和偏好，后端负责补齐信息、查询可用 adapter、安排每日清单、保存版本，并在用户修改要求后重规划。

联网搜索是显式开关：`search_enabled=false` 时后端不得调用网页搜索；`true` 时网页搜索只用于发现地点和热度信号。网页候选必须经过地图/POI 验证才进入地点卡。不要显示或保存大众点评评价正文，也不要把“网红/热门”当成评分。

产品范围是旅行规划，不提供通用问答。消息先经过关键词/规则优先的旅行意图识别；规则未命中时只允许一次受约束的大模型范围分类和旅行字段抽取。进入可执行的旅行需求后，旅行 Supervisor 由大模型根据需求选择 provider 工具（铁路、航班、地点、路线、天气或联网搜索），并决定本轮是 `clarify`、`answer`、`plan` 还是 `refuse`；工具参数、权限、来源、数据真实性和最终计划门控仍由代码校验。模型确认非旅行、输出无效或调用失败时，后端返回稳定的范围拒答或安全回退，不泄漏内部异常，也不继续调用通用聊天模型。

目的地范围较宽或关键条件不足时，后端返回 `ClarificationRequest` 而不是创建空计划。前端应展示有限路线选项或自定义输入；用户补充后，后端合并 `pending_query` 再执行正式规划。

一次聊天请求的主链路为：

```text
HomeView / ChatComposer
  → POST /chat（multipart/form-data）
  → FastAPI 鉴权与附件解析
  → stream_chat：关键词/规则识别 →（未命中时）模型范围分类
  → 旅行 Supervisor：模型选择工具 → provider 结果 → 模型决定动作
  → 代码校验动作与数据 → 仅在明确规划且有有效数据时生成 TravelPlan
  → SSE activity/source/text/done
  → Pinia chat/plan store → 消息、行程、日历、RouteMap
```

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
- guest capability 由服务端同时校验创建时间和最近使用时间，超过 7 天后旧 token 不再有效；空线程可重新获得新 capability，已有计划/回合的过期线程不会被静默重新绑定。
- 登录成功若返回 `claimed_thread`，说明当前 guest 线程已通过 capability 校验并归属该账号；前端可以继续使用这个原始 `guest_...` ID，直到用户主动新建/切换账户线程。未返回 `claimed_thread` 时，登录用户应切换到账户线程，不能使用未归属的 guest ID。
- 登录/注册弹窗只应由用户主动点击账户菜单触发；首次进入页面不自动弹出，游客可以不登录直接使用。

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

`sessions` 中每项包含：`thread_id`、`title`、`created_at`、`updated_at`。列表按最近更新时间倒序返回；前端切换会话时应使用这里返回的账户线程 ID，不要自行拼接 `thread_...`。

### `GET /history/{thread_id}`

返回当前会话的有序消息：

```json
{
  "status": "success",
  "messages": [
    {
      "role": "user",
      "content": "帮我安排杭州三日游",
      "attachments": [],
      "image_urls": [],
      "search_enabled": true
    },
    {
      "role": "assistant",
      "content": "已为你整理好行程。",
      "activities": [],
      "sources": [],
      "answer_segments": [
        {"text": "已为你整理好行程。", "source_ids": []}
      ],
      "search_enabled": true,
      "plan_id": "plan_xxx",
      "plan_version": 1
    }
  ]
}
```

助手消息中的 `activities`、`sources`、`answer_segments`、`plan_id` 和 `plan_version` 可能为空；旧 checkpoint 历史可能缺少部分可选字段，前端应按空数组或 `null` 兼容。后端已经移除内部 metadata 和 `[[cite:...]]` 标记，前端只渲染 `content` 或结构化引用字段。

### `DELETE /history/{thread_id}`

删除当前会话的聊天历史、旅行计划版本、旅行回合、guest capability 和关联的 OSS 上传对象，返回：

```json
{"status":"success"}
```

这是不可逆操作，前端应在删除前二次确认；删除后应从会话列表移除该线程并清空当前视图。

### `POST /threads/migrate-legacy`

登录用户主动迁移自己仍持有的旧 checkpoint 会话。请求必须显式提供旧线程 ID；后端不会把所有无归属旧历史自动分配给当前用户，也不会接受不在旧 checkpoint 列表中的 ID。旧 checkpoint 创建时没有 capability 绑定，因此该兼容接口不能验证旧 HttpOnly token；它只接受长度足够、随机性较高的 `guest_...` 命名空间，并拒绝 `default` 等共享/歧义 ID。其他旧 ID 需要人工恢复。新创建的游客线程不走此接口，必须使用当前 guest capability 合并。

```json
{"thread_ids":["guest_old_uuid"]}
```

单次请求最多处理前 100 个 `thread_ids`；需要迁移更多历史时应分批提交，并根据 `migrated_thread_ids` 与 `skipped_thread_ids` 更新本地状态。

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
| `request_id` | string | 否 | 本轮请求的随机 ID，用于生成过程中停止 |
| `files` | file[] | 否 | 后端支持的附件类型 |

响应 `Content-Type` 为 `text/event-stream`。每条 SSE 由 `event:` 和 `data:` 组成，`data` 是 JSON。

这是 `POST` 的 multipart 流式接口，不能使用原生 `EventSource`。前端应使用 `fetch` 配合 `ReadableStream`，按空行缓冲解析 SSE 帧；网络分片不能直接当作完整事件。请求必须继续携带 `credentials: "include"`。

事件：

```text
event: activity
data: {"stage":"progress","title":"你指定了高铁，我先查询 12306 的车次和席位。","detail":"","state":"completed","origin":"model","timestamp":"..."}

event: source
data: {"title":"西湖","url":"https://...","summary":"...","source_date":"...","evidence_id":"..."}

event: text
data: {"delta":"增量文本"}

event: done
data: {
  "ok": true,
  "final_text": "供聊天区展示的完整文本",
  "answer_segments": [
    {"text":"供聊天区展示的完整文本", "source_ids":[]}
  ],
  "activities": [],
  "sources": [],
  "clarification": null,
  "scope_refusal": false,
  "decision": "answer|clarify|plan|refuse",
  "decision_reason": "简要动作原因",
  "retryable": false,
  "retry_reason": "",
  "transport_options": [],
  "transport_page": null,
  "trip_plan": null,
  "attachments": []
}
```

- `activity` 用于显示 Codex 风格的公开工作进展和真实执行轨迹。`origin=model` 表示模型返回了经安全过滤的短摘要；`origin=system/provider` 表示代码记录的流程或数据源状态。不要把 `detail` 当作事实字段，也不要将任何 activity 当作模型逐字思维链。
- 生成中的消息按 SSE activity 到达顺序逐条显示：每收到一条真实阶段事件就立即追加，不使用固定计时器伪造进度。工作进展面板在工作台内有独立滚动和高度上限，前端不要依赖其完整高度推动正文布局。
- `source` 和 `done.sources` 只用于来源卡；优先用 `evidence_id` 去重。来源可能是来源卡字段，也可能是完整 `Evidence` 字段，未知字段应忽略。
- `text.delta` 只做聊天文本增量拼接；旅行规划正文、澄清回复和范围拒答都会拆成多个增量事件，前端不应等待完整正文后再渲染。
- `done.transport_options` 和 `done.transport_page` 仅用于“加载更多”类后续交通查询；如果本轮生成了完整计划，交通候选的权威列表仍在 `done.trip_plan.transport_options`。
- 旅行请求完成时，只有 Supervisor 决定 `plan` 且通过有效数据门控，`done.trip_plan` 才是结构化 TravelPlan；查询/推荐类回答的 `done.trip_plan` 为 `null`。超出旅行范围的问题 `done.trip_plan` 为 `null`，并将 `done.scope_refusal` 设为 `true`。
- 当旅行需求缺少关键条件时，`done.trip_plan` 为 `null`，`done.clarification` 为 `{code,prompt,options}`；前端应展示澄清问题和结构化选项，不要把这一轮当成失败。
- `clarification.options` 的每项包含 `key`、`label`、可选的 `description` 和 `value`。有 `value` 的选项可以直接作为下一轮 `message` 提交；没有 `value` 的选项表示需要用户自行输入。
- 关键词/规则未识别的消息会经过一次旅行范围分类；分类为非旅行问题时，系统只返回范围说明，不调用通用问答 Agent。分类为旅行后，Supervisor 继续决定需要的工具和动作。
- `done.final_text` 是可直接展示的文本，不包含内部 metadata 标记。前端不要从它解析日历。
- `done.answer_segments` 是句子级引用的权威结构；只有其中存在有效网页引用时才按它渲染引用组件，否则直接渲染 `done.final_text` 的完整 Markdown。不要把两条路径同时完整渲染造成重复文本。
- 发生异常时收到 `event: error`，数据为 `{ "message": "..." }`；这类错误可能仍然以 HTTP 200 的 SSE 响应返回，也可能没有 `done`，不能只依赖 HTTP 状态码判断成功。鉴权/输入错误可以返回具体提示；服务内部异常只返回稳定的通用提示，详细异常只写服务端日志。前端应结束 loading、保留已收到内容，并避免自动重复提交同一请求。
- `done.retryable=true` 表示 provider 暂时失败或超时，前端可显示有限次数的“重试”操作；不要从 `final_text` 或诊断文案猜测是否可重试。当前前端最多允许 2 次手动重试，每次复用原始用户输入和附件。

生成中的停止：前端为每次 `/chat` 请求生成随机 `request_id` 并作为表单字段提交；用户点击停止时调用 `POST /chat/cancel`，请求体为 `{ "thread_id": "...", "request_id": "..." }`，同时立即 abort 当前 `fetch` 流。取消接口使用与 `/chat` 相同的账号/guest capability 鉴权，只会通知服务端在下一个安全编排边界停止。已经进入 provider 的单次网络请求可能先完成，不能承诺硬终止；前端应保留已收到的文本并标记为已停止。`activity` 展示经过过滤的公开工作进展和真实执行状态，不展示模型内部思维链。

附件限制：图片支持 `.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`；文档支持 `.pdf`、`.txt`、`.md`、`.csv`、`.docx`、`.doc`、`.xlsx`、`.xls`。单个文件最大 10MB。`.doc` 只返回兼容性提示，不保证可靠解析。图片附件的 `image_url` 可能是内联 `data:` URL 或有时效的 OSS URL，不应假设它永久有效；历史消息中的图片通过用户消息的 `attachments`/`image_urls` 返回，而 `done.attachments` 当前只列出文本附件。

推荐处理逻辑：以 `done` 作为本轮结束信号；如果 `done.trip_plan` 非空，刷新计划卡、日历和来源卡，但保持行程面板关闭，在“行程计划”按钮显示未读红点；用户主动打开面板后清除红点。不要仅凭某一条 `activity` 判断计划已保存。

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
  "destination_scope": "city",
  "destination_cities": [],
  "travelers": 2,
  "preferences": ["美食", "慢游"],
  "summary": "...",
  "days": [],
  "transport_options": [],
  "transport_pages": [],
  "out_of_range_items": [],
  "route_plans": [],
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

`route_plans` 是已通过地点解析的路线预览数据。每个路线包含 `mode`、`origin`、`destination`、`duration`、`distance`、`origin_location`、`destination_location`、`polyline`（经度/纬度点数组），以及可选的 `origin_place_id`、`destination_place_id`、`source_ids`、`departure_bucket`、`duration_minutes`、`distance_meters`、`estimated_cost`、`cost_currency` 和 `cost_scope`。缺少新增字段的旧计划必须继续可读。用户未指定景点时，后端会从已通过地图验证的城市景点推荐中选择路线锚点；不会从未经验证的回答文本生成路线。前端不要从 `detail` 或回答文本解析路线；`RouteMap.vue` 在配置 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_SECURITY_JS_CODE` 时优先加载高德 JS API 2.0，支持缩放、拖拽、路线自动适配和起终点标记；没有浏览器端 Key 时调用 `GET /travel/plans/{thread_id}/map`（可选 `?version=n`）读取服务端静态地图。没有路线几何时接口返回 404；静态地图请求异常时接口返回基于真实折线的 SVG 示意图，并带 `X-Route-Map-Fallback: true` 响应头。浏览器端 Key 必须限制高德控制台域名白名单，服务端 Web Service Key 不得写入 `VITE_*` 变量。

有明确地点的路线查询可能返回有界有向矩阵的部分结果。前端应把 `route_segments` 作为最终排程边，把 `route_plans` 作为地图预览边；缺边、预算截断和 provider 失败由计划状态/诊断展示，不能根据地点名称自行补画路线，也不能把缓存命中或静态时长显示为实时路况。

### `TravelPlan.transport_pages`

每种已请求的交通类型可有一个分页状态：

```json
{
  "mode": "rail",
  "offset": 0,
  "limit": 5,
  "returned_count": 5,
  "total_count": 38,
  "has_more": true,
  "filter": "high_speed"
}
```

首次结果通常只展示 5 条。前端可在行程面板调用 `GET /travel/plans/{thread_id}/transport?mode=rail|flight&offset=5&limit=5`，或提交“再给我 3 条高铁”等后续消息。接口返回的候选必须直接展示 provider 字段和来源，不能由模型补造。`filter=high_speed` 表示铁路结果仅包含 G、D、C 字头；没有真实票价时 `price` 为空，不得显示货币符号。

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

没有计划时不会返回 `timezone` 等计划字段，而是返回：

```json
{
  "status":"success",
  "plan_id":null,
  "version":null,
  "days":[]
}
```

前端按 `days[].date` 高亮日历；没有对应项的日期不高亮。`has_conflicts=true` 应有独立冲突提示。`out_of_range_item_count>0` 表示完整计划中的 `out_of_range_items` 有内容；日历投影不会为这些日期生成虚假的日期格子。

### `GET /travel/plans/{thread_id}/days/{YYYY-MM-DD}`

返回当天完整清单：`{status, plan_id, version, day}`。日期不在当前投影范围时 404，不要用聊天文本补全。

### `GET /travel/plans/{thread_id}/versions/{version}`

返回指定版本：`{status:"success", plan: TravelPlan}`。不存在或无权限均为 404。

### `GET /travel/plans/{thread_id}/transport`

返回当前计划中已请求交通类型的下一页候选：

```text
GET /travel/plans/{thread_id}/transport?mode=rail&offset=5&limit=5&version=1
```

响应包含 `options`、`page`、`sources` 和 `errors`。`page.total_count` 是 provider 返回并经过筛选后的候选总数，`page.has_more` 表示是否还可继续加载。前端首次展示 5 条时，下一页通常从 `offset=5` 开始。重复请求会重新调用对应 provider，不应让大模型补写候选。

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

状态约束：允许的 `status` 为 `suggested`、`confirmed`、`booked`、`skipped`、`cancelled`。`confirmed/booked` 必须配合 `locked=true`；`locked=true` 时只能使用 `confirmed/booked`。只提交 `locked=true` 会将项目置为 `confirmed`，只提交 `locked=false` 会将项目置为 `suggested`。`item_id` 也可以指向 `plan.out_of_range_items` 中的项目。

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

成功返回：`{status:"success", plan: TravelPlan, final_text: string, answer_segments: {text:string,source_ids:string[]}[], sources: Evidence[]}`。这是普通 JSON 请求，不是 SSE。后端会保留已确认/锁定项、增加新版本，并把无法满足的日期变化写入 `conflicts`。

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
- 修改/重规划：无权限可能为 403，版本冲突为 409，读取 `current_version`。
- 重规划未生成计划为 502。
- 普通后端异常通常为 500，正文为 `{status:"error", message}`。
- `/chat` 的鉴权、文件类型、文件解析和空消息错误也可能表现为 HTTP 200 内的 SSE `error` 事件；解析错误事件后停止当前 loading，不要重复提交同一请求。

计划来源优先级：`done.trip_plan` > `GET /travel/plans/{thread_id}` > 日历/日期接口。每次 PATCH/replan 成功后用响应中的完整 `plan` 更新本地版本号，避免继续使用旧 `expected_version`。

## 10. 当前不应实现的前端行为

- 不从 `final_text`、`summary` 或来源摘要提取日期和 PlanItem。
- 不自行排序评分、距离或地点热度。
- 不把网页搜索标题直接当作已验证地点。
- 不把 `rating`、`popularity_signal`、`opening_status="unknown"` 混为一谈。
- 不隐藏 `risks`、`conflicts`、`adapter_status` 或 `is_demo`。
- 不在前端保存或展示大众点评评价正文。
- Markdown 展示必须进行 HTML/XSS 安全过滤；来源标题、摘要和 URL 都是不可信外部数据。
- 来源链接只允许 `http`/`https`，新标签页打开时使用 `noopener,noreferrer`；其他协议只展示文本，不允许点击跳转。
- 不修改 `static/` 现有文件作为本阶段后端交接的一部分；接入时请另行评估 UI 改造范围。

## 11. 句子级联网搜索引用

后端通过结构化字段暴露句子级引用。前端不能从 Markdown 解析引用标记，也不能猜测哪一个来源支持哪一句话。

### SSE `event: done` 示例

```json
{
  "final_text": "第一句。 第二句。",
  "answer_segments": [
    {"text": "第一句。", "source_ids": ["web_abc123"]},
    {"text": " 第二句。", "source_ids": []}
  ],
  "sources": [
    {
      "evidence_id": "web_abc123",
      "source_type": "web_search",
      "provider": "Tavily",
      "title": "公开页面",
      "url": "https://example.com/article",
      "summary": "..."
    }
  ]
}
```

按顺序渲染每个 `answer_segments` 项。只有 `source_ids` 中存在有效来源时才显示句末链条图标；图标旁的数字是有效来源数量。使用 ID 到 `done.sources` 中解析，只在右侧来源栏显示该句对应的来源卡。点击来源时，在新标签页打开其 `http`/`https` URL，并使用 `noopener,noreferrer`。

`GET /history/{thread_id}` 的助手消息和 `POST /travel/plans/{thread_id}/replan` 的 JSON 响应也包含同样的 `answer_segments` 字段。`source` SSE 事件会在来源清理、去重后且 `done` 之前发送；应以 `done.sources` 作为完整来源列表。

SSE 来源卡使用 `summary`；`TravelPlan` 和重规划响应中的 `Evidence` 使用 `snippet`。两者都只是来源摘要，不是网页正文。前端来源卡可以显示 `summary ?? snippet`，且只能让 `http`/`https` URL 可点击。

只有 `source_type="web_search"` 的来源可以作为句子引用。地图 POI、天气、铁路、航班和路线来源仍可作为来源卡或结构化计划证据，但不能渲染为网页搜索句末图标。没有来源支持时保留 `source_ids: []`，不显示图标。

模型内部的 `[[cite:web_...]]` 标记已经由后端移除，绝不能显示。流式 `text.delta` 已经清理；`done.final_text` 是规范的干净文本。`search_enabled=false` 绝不能产生网页搜索句子引用。

如果历史消息或兼容旧数据中的 `answer_segments` 为空，直接渲染清理后的 `content`，不要自行补造引用关系。

## 12. 工作台滚动与层叠契约（2026-09-03）

- 工作台模式下 `.workspace-chat-surface` 是固定尺寸的消息玻璃容器；输入区 `.composer` 独立位于其下方，页面风景背景仍在两者外独立渲染。
- `.workspace-chat-scroll-region` 是唯一允许纵向滚动的元素。外层 `.chat-scroll` 和页面本身必须保持 `overflow: hidden`，流式消息增长不能改变工作台高度，也不能触发顶部栏收缩。
- 消息玻璃容器宽度上限为 860px（移动端自适应到可用宽度），与 780px 的独立输入框保持接近的视觉比例；输入区不得被消息玻璃背景包住，也不应在两者之间添加不透明横向分隔层。工作台底边保持透明。
- 移动端计划通知浮层应位于独立输入区上方，不能覆盖搜索字段或发送按钮。
- 切换到工作台时，如果需要重置滚动位置，应定位 `.workspace-chat-scroll-region`，不要把 `.workspace-chat-surface` 当作滚动根。
