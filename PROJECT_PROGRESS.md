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
- 航班 bridge 的 auto 路径已移除默认 dummy fallback；Flight MCP 现在必须显式启用。
- 已新增旅行搜索发现层和证据对象；搜索关闭时不会创建 Tavily searcher 或调用网页搜索。
- 自动化测试目录已建立，覆盖存储、日期、搜索开关、POI 无结果、重规划、锁定项、航班 demo 隔离、API 和 SSE。

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

- 状态：已完成，待对抗式审查
- 目标：持久化 TravelPlan、版本、日历数据和 SSE 输出。
- 已完成：领域 dataclass、SQLite 版本库、旅行回合存储、thread_id 绑定、`/chat done.trip_plan`。
- 验证：`python -m pytest -q` 14 passed；`python -m compileall` 通过；SSE 烟测确认 `done.trip_plan` 和版本递增。

### Phase 2：搜索开关、附近探索和证据

- 状态：进行中
- 目标：接入搜索开关、POI 验证、热度信号、来源和新鲜度。
- 已完成：`services/travel_search.py` opt-in 发现层；网页候选必须经过地图解析才进入 POI 卡；评分、热度、距离和来源字段分离。
- 验证：搜索层 fixture 已覆盖关闭、开启、来源保留和失败；地图无结果不生成虚构 POI。

### Phase 3：重规划、冲突和测试

- 状态：进行中
- 目标：用户修改偏好或天气后生成新版本，保留锁定项并标出冲突。
- 已完成：当前计划继承、锁定项保留、日期越界冲突、独立重规划 HTTP 接口。
- 验证：pytest 覆盖锁定项、日期边界、adapter 空结果、版本 API、SSE 和删除恢复。

### Phase 4：对抗式审查与前端交接

- 状态：待开始
- 目标：独立代码审查、修复 Critical/Important 问题，生成包含接口和 JSON 示例的前端交接文档。

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
- 是否在后端契约未稳定前修改前端文件。

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
