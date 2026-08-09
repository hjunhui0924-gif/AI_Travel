# AI_Agent 项目进程

> 本文件是后端开发的持续进程记录。每次开始新的开发阶段、修复问题或继续任务前，必须先阅读本文件；完成阶段后立即更新。它记录目标、已确认决策、当前进度、边界、风险、验证证据和下一步，避免上下文压缩后丢失关键约束。

## 1. 产品目标

AI_Agent 的定位是“懒人旅行规划 Agent”：用户只需要用自然语言描述大概的出发时间、目的地、同行人和偏好，Agent 自动补齐必要信息、查询数据、筛选方案、生成每日行程，并在天气、交通或用户偏好变化时重新规划。

核心差异不是生成旅游攻略，而是维护一份有来源、可验证、可修改的旅行计划，减少用户搜索、比较和手动排程的工作。

## 2. 当前工作范围

### 本阶段必须完成

- 后端旅行计划领域状态和持久化。
- 旅行分支与 thread_id 的关联，避免旅行回答绕过历史状态。
- 结构化 TravelPlan 输出，为前端日历和清单提供稳定数据。
- search_enabled 对网页搜索的明确控制。
- 附近餐厅/景点/网红地点的候选发现、地点验证、排序和来源记录。
- 交通、地图、天气、搜索结果的来源和新鲜度信息。
- 重规划基础：保留已确认项，识别受影响项，生成新版本和冲突。
- 后端测试、接口契约和对抗式代码审查。

### 后续阶段

- 前端日历、日清单、计划卡片和重规划交互。
- 前端交接 Markdown，必须以后端实际稳定的接口和 JSON 为准。
- 计划导出、提醒、分享和更复杂的实时监控。

## 3. 明确不做

- 本阶段不修改 static/index.html、static/main.js、static/style.css。
- 不直接爬取大众点评或复制未授权的评价文本。
- 不实现自动订票、支付和订单提交。
- 不把搜索结果摘要当成结构化事实。
- 不让前端从 Markdown 反向解析日历。
- 不把股票、通用办公问答等能力继续扩展为产品核心。
- 不把模型的私有推理过程写入日志或接口。

## 4. 已确认的产品和技术决策

- 后端是旅行计划的唯一事实来源；前端是展示和交互 adapter。
- TravelPlan 是持久化的核心领域状态，不是一次性 Markdown。
- search_enabled=true 时允许 Tavily/网页搜索；false 时不得调用网页搜索，使用模型已有知识处理非实时问题。
- 高德、12306、航班和天气属于领域数据 adapter；它们是否在关闭网页搜索时继续调用，需要与“实时数据开关”区分，不能混淆。
- 不使用生产 dummy 航班作为真实推荐；演示数据只能作为测试 adapter。
- 网红/热门是“热度信号”，必须和评分、距离、营业状态、路线适配度、查询时间分开呈现。
- 生产结果必须区分真实结果、无结果、接口失败、过期数据和演示数据。
- 统一使用 Asia/Shanghai 解释用户旅行日期和前端日历日期。

## 5. 当前代码基线与已知问题

- app.py 提供 FastAPI /chat 和 SSE 流。
- agents/agent.py 负责通用 Agent、旅行路由、搜索、checkpoint 和活动/来源缓冲。
- agents/travel_agent.py 负责旅行意图、交通、路线、POI、天气和时间轴编排。
- agents/schemas.py 已有 TravelQuery、TravelPlanResponse、TransportOption、TimelineItem、PoiRecommendation 等雏形。
- adapters/amap_adapter.py 已经能取得 POI、评分、人均、地址和距离等字段。
- services/poi_recommender.py 已按评分、距离和偏好做基础排序；营业状态仍保留为 unknown，不能当作营业事实。
- 旅行分支仍绕过通用 LangGraph 执行图，但现在通过 `travel_plans.db` 持久化 TravelPlan 版本和旅行回合，历史恢复不再依赖 Markdown。
- `trip_replan` 已读取当前 TravelPlan，并在生成新版本时保留 `locked/confirmed/booked` 项目，日期范围冲突会进入 `conflicts`。
- 重规划时日期范围外的已确认/锁定项目会进入结构化 `TravelPlan.out_of_range_items`，不会静默消失；该列表也支持计划项 PATCH。
- 首次旅行计划写入使用 `expected_version=0`，与 SQLite `BEGIN IMMEDIATE` CAS 配合，避免并发首次请求互相覆盖。
- 航班/铁路候选逐条隔离异常，路线 transit/driving/walking 按模式隔离异常；一个 provider 行或模式失败不会丢弃同批有效结果，并通过 `partial/failed` 状态和 diagnostics 暴露。
- guest capability 服务端校验创建时间和最近使用时间的 30 天 TTL；旧 checkpoint 只能通过登录用户主动提交已知 ID 的迁移接口归属，禁止自动迁移。
- 计划项生成后做同日 ID 去重，避免相同标题导致 PATCH 更新到错误项目。
- 航班 bridge 的 auto 路径已移除默认 dummy fallback；Flight MCP 现在必须显式启用。
- 已新增旅行搜索发现层和证据对象；搜索关闭时不会创建 Tavily searcher 或调用网页搜索。
- 自动化测试目录已建立，覆盖存储、日期、搜索开关、POI 无结果、重规划、锁定项、航班 demo 隔离、API 和 SSE。
- `utils/weather_utils.py` 已增加高德 API 错误码、有限重试、请求节流和地址缓存；同一轮路线规划不会重复突发请求高德。
- `adapters/rail_12306_adapter.py` 已缓存站点字典，并为会话初始化和余票查询增加单请求超时与整条查询总超时，避免 12306 风控/网络异常拖住整次规划。
- `adapters/ctrip_flight_adapter.py`、`bridges/flight_mcp_bridge.py` 已识别 Ctrip H5 的 whaleguard/HTTP 432/页面结构变化，并把失败原因传给上层；不会把被拦截结果伪装成空航班或 dummy 航班。
- 新增 `services/integration_health.py`：使用 `python -m services.integration_health --live` 检查高德、12306、Tavily、Ctrip H5 和 Flight MCP，输出不含密钥的结构化状态。

## 6. 领域模型约定

详见根目录 CONTEXT.md。当前 canonical terms：

- TravelPlan：用户正在维护的一次完整旅行计划。
- TravelRequirement：用户用自然语言表达的出行目标、时间、目的地和偏好。
- TravelFact：已经被用户或可信来源确认的事实，例如车票、酒店、预约和营业时间。
- TravelConstraint：计划必须满足或尽量满足的硬约束和软偏好。
- PlanDay：某个自然日期的旅行安排。
- PlanItem：某天的一项交通、住宿、景点、餐饮、休息或提醒。
- Evidence：支持某个事实或推荐的来源、查询时间、有效期和可信度信息。
- PlanVersion：一次生成或重规划后的完整计划版本。
- NearbyDiscovery：围绕酒店、车站、景点或路线发现附近餐饮和游玩候选的领域能力。

## 7. 预期后端与前端 seam

后端向前端提供：

- 聊天文本和活动事件。
- 来源卡片。
- 结构化 trip_plan。
- 日历日期摘要。
- 日期下的 PlanItem 清单。
- 冲突、风险和计划版本。

前端不得承担：

- 交通/POI/天气/搜索调用。
- 地点去重和评分排序。
- 路线可行性判断。
- 重规划决策。
- 从 Markdown 猜测日期或行程项目。

## 8. 阶段状态

### Phase 0：进程文档与领域词汇

- 状态：已完成
- 产物：PROJECT_PROGRESS.md、CONTEXT.md
- 结果：已确认产品定位、后端/前端职责、搜索开关规则、禁止爬取大众点评和主要风险。

### Phase 1：旅行计划状态与结构化契约

- 状态：已完成，第一轮审查已完成，第二轮复查中
- 目标：持久化 TravelPlan、版本、日历数据和 SSE 输出。
- 已完成：领域 dataclass、SQLite 版本库、旅行回合存储、thread_id 绑定、`/chat done.trip_plan`。
- 验证：`python -m pytest -q` 14 passed；`python -m compileall` 通过；SSE 烟测确认 `done.trip_plan` 和版本递增。

### Phase 2：搜索开关、附近探索和证据

- 状态：已完成，已通过两轮审查修复来源泄漏、受限域名和证据 ID 问题
- 目标：接入搜索开关、POI 验证、热度信号、来源和新鲜度。
- 已完成：`services/travel_search.py` opt-in 发现层；网页候选必须经过地图解析才进入 POI 卡；评分、热度、距离和来源字段分离。
- 验证：搜索层 fixture 已覆盖关闭、开启、来源保留和失败；地图无结果不生成虚构 POI。

### Phase 3：重规划、冲突和测试

- 状态：已完成，已通过两轮审查修复锁定项、CAS、历史合并、结构化失败状态和日期外项目保留问题
- 目标：用户修改偏好或天气后生成新版本，保留锁定项并标出冲突。
- 已完成：当前计划继承、锁定项保留、日期越界冲突、独立重规划 HTTP 接口。
- 验证：pytest 覆盖锁定项、日期边界、adapter 空结果、版本 API、SSE 和删除恢复。

### Phase 4：对抗式审查与前端交接

- 状态：已完成，交接文档已更新，Critical/Important 问题已修复并完成最终验证
- 目标：独立代码审查、修复 Critical/Important 问题，生成包含接口和 JSON 示例的前端交接文档。

### Phase 5：外部环境联调与失败降级

- 状态：已完成；高德、12306、Tavily 已真实联调通过，Ctrip H5 已确认被风控拦截，Flight MCP 当前未配置可用数据源。
- 目标：确认 `.env` 中的凭证不仅存在，而且真实请求、响应解析和业务降级链路可用。
- 高德证据：地理编码、天气、文本 POI、周边 POI、步行路线、驾车路线和公交路线均返回成功；请求加入节流/重试/缓存。
- 12306 证据：广州南 -> 深圳北，动态未来日期返回 579 条原始车次；加入站点字典缓存、单请求超时和 45 秒总超时后，本次健康检查约 1.3 秒完成。
- Tavily 证据：真实搜索返回 5 条发现结果；搜索仍只有在 `search_enabled=true` 时允许进入旅行发现层。
- Ctrip 证据：`https://m.ctrip.com/html5/flight/sha-hgh-day-8.html` 返回 HTTP 432，正文为 `whaleguard block`；当前 H5 爬虫不可作为生产航班源，不再尝试绕过风控。
- Flight MCP 证据：当前 `.env` 未设置 `FLIGHT_MCP_ENABLED/FLIGHT_MCP_MODE`；强制启用已安装的 `package` 模式进行真实查询无法得到航班结果，最终被 45 秒 bridge 总超时截断，不能视为可用航班结果。
- 解决方案：航班正式接入必须配置已授权的 Flight MCP 命令/HTTP 服务或官方/合作方 API；未配置或失败时返回 `not_configured/failed` 和提醒，不显示价格、班次或可预订暗示。Ctrip H5 只保留探测能力。

## 9. 每阶段必须检查的坑

- 是否把一次性回答误当成持久化计划。
- 是否在关闭搜索时仍然偷偷调用网页搜索。
- 是否把过期或 dummy 数据显示成真实数据。
- 是否没有保存来源和查询时间。
- 是否重规划时覆盖用户已确认或锁定的项目。
- 是否只测理想路径，没有测外部 adapter 失败。
- 是否把 provider 字段泄漏给领域模型或前端。
- 是否破坏访客会话、用户会话隔离和旧聊天历史。
- 是否把时区、跨天到达和日期边界处理错误。
- 是否在日期范围缩小时把已确认/锁定项目移出响应；应检查 `out_of_range_items`。
- 是否允许旧 checkpoint 通过任意 guest cookie 首次绑定，或让过期 capability 继续访问。
- 是否把同日同名项目生成相同 `item_id`，导致 PATCH 地址不唯一。
- 是否在后端契约未稳定前修改前端文件。
- 是否把 Ctrip H5 被风控后的空列表当作“当天没有航班”；应展示 `blocked/failed` 诊断并切换到授权航班源。
- 是否让 12306/高德的单个网络调用无限等待或在同一计划内重复请求；应遵守 adapter 的节流、缓存和总超时配置。

## 10. 变更日志

### 2026-08-09

- 确认后端先行、前端由另一个智能体负责。
- 确认后端完成后再生成前端交接 Markdown。
- 建立本进程文档，要求每次开发前先阅读。
- 确认产品为面向懒人的旅行规划 Agent，而非商务差旅产品。
- 确认附近热门地点采用“联网搜索发现 + 地图/POI 验证”的策略，不直接爬取大众点评。
- Phase 0 完成，开始实现后端 TravelPlan 状态和结构化契约。
- 新增 `agents/schemas.py` 的 TravelPlan/PlanDay/PlanItem/Evidence/Fact/Constraint 领域结构。
- 新增 `services/travel_store.py`：旅行计划版本、旅行回合和恢复能力，运行库为 `resources/travel_plans.db`。
- 新增 `services/travel_search.py`：仅在 `search_enabled=true` 时调用 Tavily，搜索结果只作为地点发现/热度信号。
- `/travel/plans/{thread_id}`、`calendar`、`days/{date}`、`versions/{version}` 和 `replan` 接口已接入。
- 修正航班 adapter：未显式启用时关闭，auto 查询失败返回空结果，不再返回伪装成真实航班的 dummy。
- 增加计划项 `PATCH` 确认/锁定接口；锁定项会在后续重规划中保留。
- 修复日期日字段被误识别为行程天数、SSE 线程池 ContextVar 丢失结构化计划、旅行重规划路由和来源卡重复等问题。
- 增加 turn-only 线程的存储层用户归属校验、guest capability cookie 端到端测试、旧 checkpoint 与 chat/travel turn 交错历史测试。
- 增加通用联网搜索的 Dianping 过滤、无效响应状态测试和网页搜索状态机测试。
- 增加 `FRONTEND_HANDOFF.md`，记录 SSE、TravelPlan、日历、计划项 CAS、guest cookie、来源和错误契约。
- 修复旧 checkpoint 的任意 guest cookie 劫持：旧 checkpoint 不能首次 mint capability；已有合法绑定仍可验证。
- 增加 guest capability 服务端 TTL（创建时间/最近使用时间均超过 30 天则失效）；空线程可重新生成新 token，有持久化数据的线程不会静默重绑。
- 增加登录用户主动迁移旧 checkpoint 的 `POST /threads/migrate-legacy`，仅接受用户显式提交且仍存在的 `guest_...` 随机线程 ID，不自动认领 `default` 等歧义历史。
- 增加 `TravelPlan.out_of_range_items` 和日历 `out_of_range_item_count`，保留日期范围外的 confirmed/locked/booked 项并支持 PATCH。
- 首次旅行计划保存显式传 `expected_version=0`；航班单条结果、路线各模式失败互不阻断；同日重复项目自动生成唯一 `item_id`。
- 完成真实外部联调：高德七类子检查通过，12306 广州南到深圳北返回 579 条车次，Tavily 返回 5 条搜索结果；Ctrip H5 确认 HTTP 432/whaleguard，Flight MCP package 受 45 秒总超时保护后明确失败。
- 新增高德节流、有限重试和地址缓存；12306 站点缓存、单请求/总超时；航班 bridge 错误透传和进程树清理；新增 `services/integration_health.py` 和外部 adapter 回归测试。

## 11. 对抗式审查修复记录（2026-08-09）

第一轮独立审查曾发现以下合并前问题，当前工作树已加入修复和回归测试：

- guest 会话只允许匿名使用；登录用户必须使用账户线程 ID，匿名请求不能访问已归属用户的线程或旅行计划。
- 计划项 PATCH 与重规划使用 `expected_version` 做 SQLite 事务内 CAS；省略时后端以本次读取到的版本作为期望版本，过期写入返回 HTTP 409。
- 重规划遇到同 `item_id` 的建议项时，以用户已确认/锁定快照覆盖建议项，不能降级为 suggested/unlocked。
- 网页地点发现只保存来源元数据，不保存网页正文/评价摘要；Dianping 域名结果直接丢弃。网页 Evidence ID 按规范化 URL 稳定生成。
- `TravelPlan` 现在持久化 `alerts`、`diagnostics`、`adapter_status`；PlanItem 和 Evidence 保留 `is_demo`，前端不能把演示数据当作真实交通。
- 交通 adapter、路线、POI、天气和网页搜索失败会进入结构化状态；`success`、`empty`、`failed`、`not_configured`、`not_requested`、`disabled`、`partial` 等状态必须与自然语言风险同时展示。
- 交通方案增加 `depart_date/arrive_date`；PlanItem/TimelineItem 增加跨日结束日期。31 天以上计划保留真实 `TravelPlan.end_date`，日历只投影前 31 天并记录风险。
- 旅行回合和普通聊天回合进入统一有序 turn log；旧 checkpoint 与新 turn log 会按时间合并并按内容去重，避免迁移后丢失旧聊天。
- 计划增加 `requested_days`、`projected_days`、`calendar_truncated`、`projection_end_date`，前端不再从风险文本推断日历是否截断；跨午夜交通在抵达日期生成结构化 arrival 清单项。
- 通用网页搜索使用惰性初始化和受限来源过滤；高德 POI adapter 的网络失败不再静默伪装成空结果。

第二轮本地对抗审查补充检查了以下攻击面并加入回归测试：

- 旧 checkpoint 带任意 guest cookie 不能首次 mint capability；只有已有合法绑定可以验证。
- 旧历史迁移只接受登录用户显式提交的、仍存在且长度足够的 `guest_...` ID；`default` 等共享/歧义 ID 不允许自助认领。迁移后已归属当前用户的旧 guest ID 可以正常访问。
- guest capability 服务端校验 30 天创建/最近使用 TTL；空线程过期后生成新 token，带持久化数据的过期线程不重绑。
- 首次旅行计划路径明确传 `expected_version=0`；日期外锁定项保存在 `out_of_range_items`，同日重复项目经过全局 ID 去重；航班、铁路和路线 partial/failed 状态会保留可用结果并进入 diagnostics。

最终验证证据（后端基线 + 外部联调修复）：`python -m pytest -q` 为 **58 passed**，有 1 个既有 FastAPI/Starlette 弃用警告；`python -m compileall -q agents services adapters bridges app.py tests` 通过；`git diff --check` 无内容错误，仅报告 Windows 换行转换提示。`FRONTEND_HANDOFF.md` 的 TravelPlan `adapter_status/diagnostics` 契约仍然有效。
