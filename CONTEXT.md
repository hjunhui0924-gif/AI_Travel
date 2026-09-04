# AI_Agent 旅行规划上下文

本上下文定义旅行规划领域的 canonical language。它描述用户和产品中的领域概念，不记录代码实现细节。

## 旅行计划

**TravelPlan**：
用户正在维护的一次完整旅行计划，包含旅行日期、地点、同行人、偏好、每日安排、来源、风险和版本。
_Avoid_: 攻略、聊天回复、一次性结果

**TravelRequirement**：
用户对旅行目标的自然语言表达，包括大概时间、目的地、人数、预算、兴趣和限制。
_Avoid_: Prompt、查询参数

**PlanDay**：
旅行计划中的一个自然日期，以及该日期下按时间排列的安排。
_Avoid_: 日历格子

**PlanItem**：
PlanDay 中的一项具体安排，例如交通、住宿、景点、餐饮、休息或提醒。
_Avoid_: 推荐列表项

## 事实与约束

**TravelFact**：
已经被用户确认或被可信来源支持的旅行事实，例如车票、酒店、预约、营业时间和地点。
_Avoid_: 模型猜测、搜索摘要

**TravelConstraint**：
旅行计划需要满足的条件。硬约束必须满足，软约束用于排序和取舍。
_Avoid_: 提示词要求

**Evidence**：
支持某个 TravelFact 或推荐结论的来源、查询时间、有效期和可信度说明。
_Avoid_: 引用文本、模型解释

**PlanVersion**：
一次生成或重规划后的完整 TravelPlan 快照，用于比较变更和恢复历史。
_Avoid_: 聊天轮次

## 地点发现

**NearbyDiscovery**：
围绕旅行计划中的酒店、车站、景点或路线，发现附近餐饮和游玩候选，并结合热度、评分、距离、时间和偏好进行排序。
_Avoid_: 搜索结果堆叠

**PopularitySignal**：
表示地点近期受关注程度的证据，例如多个近期来源提及、平台热度或明确的热门标签；它不等同于评分。
_Avoid_: 网红分数

## 需求澄清

**DestinationScope**：
目的地的范围层级，包括单个城市、省份或更宽泛的区域。范围越宽，越需要先确认城市组合才能形成可执行的 TravelPlan。
_Avoid_: 地图缩放级别

**ClarificationRequest**：
在生成 TravelPlan 前，向用户补齐关键 TravelRequirement 的一次短问题，可带有有限的路线选项；它不是失败回复，也不是最终计划。
_Avoid_: 模型自由追问、错误计划

**TravelScopeClassification**：
对无法被规则确定的消息进行一次受约束的范围判断，只回答它是否与旅行规划相关，并可返回有限的旅行字段候选；它不承担通用问答职责，也不能绕过完整性校验直接调用工具。
_Avoid_: 通用聊天兜底、无边界工具调用

**TravelSupervisor**：
在旅行需求已经具备可执行的最低条件后，由大模型根据用户目标选择必要的旅行数据工具和调用顺序，并决定本轮是追问、直接回答、生成 TravelPlan 或拒答；工具权限、参数、来源、有效数据门控和最终 TravelPlan 仍由代码校验。
_Avoid_: 无校验的模型工具调用、把模型建议当成事实

**SupervisorDecision**：
TravelSupervisor 对本轮旅行交互做出的受约束动作，包括 `clarify`、`answer`、`plan` 和 `refuse`。`plan` 只有在用户明确需要规划且获得有效 provider 数据时才可落地为 TravelPlan。
_Avoid_: 把一次工具查询自动升级为行程计划

**TransportPage**：
一类交通 provider 候选的分页结果，包含当前偏移、返回数量、总数量、是否还有下一页和筛选条件；它不是用户已购买的车票或航班。
_Avoid_: 车票订单、模型生成的班次
