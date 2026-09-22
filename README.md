# AI Travel Agent

一个基于 `FastAPI + LangChain + LangGraph` 的“懒人旅行规划 Agent”。
用户只需描述大致出发时间、目的地和偏好，Agent 会整理交通、天气、路线、周边地点和每日行程，并将计划保存为可修改、可重规划的结构化数据。

## 当前定位

这个项目适合以下场景：

- 查询航班、比对出发时间与价格区间
- 查询高铁与出行衔接方案
- 生成城市内路线、通勤建议、到达后周边推荐
- 结合天气、地点和行程背景生成更可执行的旅行计划
- 让不同账号分别保留自己的聊天记录，避免多人共用时串记录

## 主要能力

- 旅行问答工作台：围绕“从哪里出发、什么时候走、到哪里、怎么去”来组织回答
- 机票查询链路：支持通过 VariFlight、Flight MCP Bridge 接入授权航班数据
- 高铁查询链路：支持 12306 方向的高铁信息整理
- 路线规划：支持地点解析、城市内路径建议与周边推荐
- 天气辅助：支持高德天气查询，为出行建议补充天气上下文
- 文件问答：支持上传 PDF、Word、Excel、Markdown、文本、图片等作为补充上下文
- 搜索补充：前端可手动开启联网搜索，用于时效性信息补充
- 旅行需求澄清：目的地范围或关键条件不完整时，先返回结构化问题和有限选项，用户补充后继续规划
- 旅行范围识别：规则优先；规则未命中时由大模型做受约束的旅行范围分类和字段抽取，非旅行问题礼貌拒答
- 交互式路线地图：配置高德 Web 端 JS API 后支持缩放、拖拽、路线自适应和起终点标记
- 流式回答：旅行规划、澄清和范围拒答均通过 SSE 分段回传，前端按增量文本渲染
- 可中断生成：生成期间发送按钮切换为停止按钮，立即停止前端流式展示，并通知后端在安全编排边界停止
- 大模型主导决策：关键词/规则先做低成本意图识别，未命中时由大模型范围兜底；旅行 Supervisor 再选择工具并决定追问、回答或生成计划，代码负责权限、参数、来源、数据真实性和最终计划门控
- 交通候选分页：首次展示 5 条车次/航班，支持“加载更多”或继续询问“再给我 N 条”，每页保留总数与查询来源
- 可视化回答：对出行类问题优先渲染更结构化的结果面板，而不是纯文本长回复
- Codex 风格工作进展：模型可生成简短公开摘要，代码展示真实工具状态和结果；后端按 Supervisor/Provider 实际事件通过 SSE 逐条发送，前端按到达顺序显示，不展示未经筛选的隐藏思维链
- 多会话管理：支持新建、切换、删除历史会话
- 登录与会话隔离：不同账号只能看到自己的聊天记录
- 游客模式：未登录时可直接使用；游客会话按最近活动滑动保留 7 天，过期后由后台清理
- 计划能力：小日历按需加载、日期清单、版本预览、锁定项、冲突检测、Markdown/JSON 导出和只读分享
- 句子级来源：联网搜索回答可将句子与网页来源关联，前端支持来源侧栏查看

## 登录与历史记录隔离

当前版本已经支持账号体系：

- 注册 / 登录 / 退出登录
- 游客模式临时会话
- 登录后可将当前浏览器持有 capability 的游客会话合并到账号
- 不同用户之间的会话完全隔离

隔离规则如下：

- 已登录用户只能访问自己创建的会话
- A 用户不能读取或删除 B 用户的会话
- 游客只能访问 `guest_...` 临时会话
- 游客 capability 超过 7 天未活动后，后台清理聊天、旅行计划、分享快照和关联附件

登录信息和会话数据目前保存在本地 SQLite：

- 用户、登录态、会话归属数据库：`resources/ai_agent_threads.db`

## 界面特点

- 左侧会话列表 + 右侧聊天工作台
- 左下角账户模块，点击后弹出登录 / 注册 / 退出菜单
- 旅行类问题优先展示路线、时间轴、天气、周边推荐等结构化卡片；无关问题会礼貌拒答
- 目的地范围或关键条件不足时先进行结构化澄清，不生成“目的地待定”的空计划
- 首屏搜索框保持单一输入面，提交时从搜索框位置自然展开到工作台，避免整页闪变
- 景点卡片悬停带意图确认和交叉淡入淡出；顶部栏下滑收缩为液态玻璃样式，回到顶部恢复
- 首页首个风景目的地为北海公园；路线面板优先展示高德交互地图，未配置浏览器 Key 时降级为静态路线图
- 切换历史会话时自动定位到底部
- 支持滚动查看历史、回到底部按钮、搜索结果侧抽屉

## 技术栈

- Backend: `FastAPI`
- Agent orchestration: `LangChain`, `LangGraph`
- Frontend: `Vue 3 + TypeScript + Vite + Pinia`
- Web search: `Tavily`（可选）
- Map / Weather: `AMap Web API`（可选）
- 旅行识别：规则优先，未命中的消息由大模型做旅行范围分类和结构化抽取兜底，不提供通用问答
- Flight bridge: 本地 bridge / MCP / HTTP 适配（可选）
- File parsing: `pypdf`, `python-docx`, `openpyxl`, `xlrd`

## 项目结构

```text
AI_Agent/
├── app.py
├── README.md
├── requirements.txt
├── .env.example
├── agents/
│   ├── agent.py
│   ├── schemas.py
│   └── travel_agent.py
├── adapters/
│   ├── amap_adapter.py
│   ├── flight_mcp_adapter.py
│   ├── variflight_adapter.py
│   └── rail_12306_adapter.py
├── bridges/
│   └── flight_mcp_bridge.py
├── services/
│   ├── auth_service.py
│   ├── flight_service.py
│   ├── poi_recommender.py
│   ├── rail_service.py
│   ├── route_service.py
│   ├── trip_extractor.py
│   ├── trip_planner.py
│   └── weather_service.py
├── frontend/                 # Vue3 + TypeScript 源码
│   ├── src/components/       # 首页、聊天、计划面板、路线地图
│   ├── src/stores/            # auth、session、chat、plan
│   └── src/utils/amap.ts      # 高德 JS API 加载与地图适配
├── static/                   # Vite 构建产物，由 FastAPI 提供
├── resources/
├── uploads/
└── utils/
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

PowerShell 可使用：

```powershell
Copy-Item .env.example .env
```

至少准备一组可用的大模型配置，然后按需补充旅行相关能力配置。

### 3. 启动项目（开发模式）

分别打开两个终端。

终端一，启动 FastAPI：

```bash
python app.py
```

终端二，启动 Vue：

```bash
cd frontend
npm install       # 首次运行执行一次
npm run dev
```

浏览器访问：

```text
http://127.0.0.1:5173
```

旅行 Agent 后端默认运行在 `http://127.0.0.1:8001`。如需修改端口，可通过 `AI_AGENT_PORT` 设置，并同步更新 `frontend/vite.config.ts` 中的代理目标。

如果只验证 FastAPI 提供的构建产物，先在 `frontend/` 执行 `npm run build`，再运行 `python app.py` 并访问 `http://127.0.0.1:8001`。

## 用户输入与响应流程

```text
用户输入（例如“江苏五日游”）
        ↓
HomeView / ChatComposer 组装 FormData
        ↓
POST /chat → FastAPI 鉴权、附件解析、输入校验
        ↓
stream_chat 读取 current_plan / pending_query
        ↓
规则意图识别
   ┌──────┴────────┐
命中旅行          未命中规则
   │                    ↓
   │              大模型范围分类
   │             ┌──────┴──────┐
   │          是旅行问题     非旅行问题
   │             │              ↓
   └──────┬──────┘          SSE 范围拒答
          ↓
旅行规划 Supervisor 由大模型判断需要哪些工具及本轮动作
          ↓
高德 / 天气 / 交通 / 搜索工具按需调用
          ↓
大模型读取工具结果 → clarify / answer / plan / refuse
          ↓
代码校验动作和数据 → 仅在 plan 条件满足时生成 TravelPlan
          ↓
SSE → Pinia → 行程面板、日历、RouteMap
          ↑
条件不足 → ClarificationRequest → 用户补充后再次提交
```

工作进展的显示由真实执行事件驱动：每当模型决策、工具调用或 provider 返回产生一条 activity，后端就通过 SSE 推送一条，前端立即追加；没有新事件时不会预先展示下一步。工作进展面板在聊天工作台内独立滚动，桌面高度上限约 250px，移动端约 200px，避免长列表挤出正文。

信息不足时不会创建“目的地待定”的空计划，也不会提前调用路线、POI、天气或交通适配器。未指定景点时，系统会从已经通过地图验证的城市景点推荐中选择路线锚点，不会从未经验证的文本地点生成路线。只有用户明确需要规划且拿到有效 provider 数据时才生成 TravelPlan；生成后不自动展开面板，而是在“行程计划”按钮显示红点，`RouteMap` 优先使用高德 JS API 进行缩放、拖拽和自动适配。

完成的旅行计划在聊天区只显示摘要；车票、路线、天气、POI 和风险详情放在行程计划面板中，避免把原始 provider 数据堆成一长段文本。聊天工作台的消息容器使用局部液态玻璃层，页面风景背景保持独立；等待内容增长时使用稳定滚动槽，减少水平跳动。

旅行 Supervisor 的边界是“模型决定需要什么工具和本轮动作，代码决定工具能否执行以及是否允许保存计划”：API Key、用户权限、联网搜索开关、参数合法性、超时、来源绑定、演示数据标记和最终 `TravelPlan` 校验仍由代码控制。查询车票/航班/地点时默认只回答，不创建行程；模型决策无效、模型失败或没有有效 provider 数据时，不保存计划并向用户说明原因。

提示词边界：Supervisor 的 `SystemMessage` 只包含固定角色、工具白名单、调用限制、联网搜索权限和动作协议；用户原话、服务端提取的旅行字段和附件元数据统一放在独立的 `HumanMessage` 中，并标记为不可信数据。范围分类器同样不允许用户内容进入系统消息。用户内容、附件内容和网页搜索结果都不能改变系统规则、工具权限或会话权限。

## 环境变量说明

### 模型配置

- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_TIMEOUT_SECONDS`（默认 15 秒；模型请求超时后进入安全降级）

兼容备用配置：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `DASHSCOPE_API_KEY`
- `DASHSCOPE_BASE_URL`
- `DASHSCOPE_MODEL`（当前默认 `qwen3.8-flash`；配置 DashScope Key 后优先使用）
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`

### 搜索与时效信息

- `TAVILY_API_KEY`

### 地图 / 天气

- `AMAP_WEB_API_KEY`
- `AMAP_STATIC_MAP_KEY`（可选；静态地图权限与 Web Service Key 分开时使用）
- `VITE_AMAP_JS_KEY`（可选；浏览器端高德 JS API 2.0）
- `VITE_AMAP_SECURITY_JS_CODE`（可选；高德 JS API 安全密钥）

`VITE_*` 变量会进入浏览器构建产物，只能使用已在高德控制台配置域名白名单的 Web 端 Key；不要把 Web Service Key 或其他服务端密钥写入 `VITE_*` 变量。

### 交通候选分页

`TravelPlan.transport_pages` 记录每种交通类型的 `offset`、`limit`、`returned_count`、`total_count`、`has_more` 和筛选条件。前端可调用：

```text
GET /travel/plans/{thread_id}/transport?mode=rail|flight&offset=5&limit=5
```

分页接口只重新查询并整理 provider 数据，不由大模型补写车次或航班。铁路查询表达“高铁/动车”时只保留 G、D、C 字头；12306 余票接口没有可靠票价时不显示金额。

12306 查询使用持久化站点字典缓存和短时车次缓存；默认单次 HTTP 超时 8 秒、总超时 18 秒，不默认轮询多个备用 endpoint。查询失败或超时后，聊天区会提供有限次数的“重试”按钮，不会无限自动重试。

### 航班 Bridge

- `FLIGHT_MCP_ENABLED`
- `FLIGHT_MCP_MODE`
- `FLIGHT_MCP_COMMAND`
- `FLIGHT_MCP_TIMEOUT_SECONDS`
- `FLIGHT_BRIDGE_MODE`
- `FLIGHT_MCP_HTTP_URL`
- `VARIFLIGHT_API_KEY`
- `VARIFLIGHT_API_URL`
- `VARIFLIGHT_HTTP_TIMEOUT_SECONDS`
- `VARIFLIGHT_MAX_RETRIES`
- `VARIFLIGHT_RETRY_BACKOFF_SECONDS`
- `VARIFLIGHT_CITY_CODE_ALIASES_JSON`

### 观测

- `LANGSMITH_API_KEY`
- `LANGSMITH_TRACING`
- `LANGSMITH_PROJECT`

### OSS

- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET`

## 航班查询说明

项目当前的航班能力是桥接式的，不是写死在单一接口里。

支持的接入方式包括：

- Flight MCP 兼容命令/HTTP bridge
- VariFlight 官方航班 MCP HTTP provider
- Flight MCP 兼容命令
- 兼容 HTTP 返回的航班服务
- 本地演示 dummy 数据（仅测试，默认禁止）

示例配置：

```env
FLIGHT_MCP_ENABLED=true
FLIGHT_MCP_MODE=variflight
VARIFLIGHT_API_KEY=your_key
VARIFLIGHT_API_URL=https://mcp.variflight.com/api/v1/mcp/data
VARIFLIGHT_HTTP_TIMEOUT_SECONDS=20
VARIFLIGHT_MAX_RETRIES=1
```

`FLIGHT_MCP_MODE=variflight` must be explicit; a VariFlight key will not silently
override an existing command/http provider. Unknown three-letter codes
are rejected instead of being sent as city codes. Extend the built-in map with
`VARIFLIGHT_CITY_CODE_ALIASES_JSON`, for example `{"LJG":"LJG"}`.

`variflight` 模式通过 VariFlight 的 `getFlightPriceByCities` 接口查询航班方案、价格、舱位和 provider 返回的可售数量；它要求城市/机场能转换为 IATA 城市码。结果仍然只是实时查询候选，不代表已经锁座、出票或自动订票。

航班数据源必须是已授权且能稳定返回结构化数据的 VariFlight、Flight MCP、HTTP 服务或命令适配器。项目不再包含携程 H5 爬取或探测代码；携程 H5 已确认受到 `whaleguard`/HTTP 432 风控，不能作为生产航班数据源。

## 外部服务联调

不要只检查 `.env` 里是否存在 Key。项目提供了不输出密钥和响应正文的健康检查：

```bash
python -m services.integration_health --live
```

也可以单独检查：

```bash
python -m services.integration_health --live --only amap
python -m services.integration_health --live --only rail_12306
python -m services.integration_health --live --only tavily
python -m services.integration_health --live --only variflight
python -m services.integration_health --live --only flight_mcp
```

高德和 12306 的请求已经有缓存、节流和总超时；外部接口受限时会保留结构化失败状态，不会伪造 POI、车次或航班。航班查询应配置授权的 VariFlight、Flight MCP 或官方/合作方航班 API。

旅行计划中的路线预览使用高德路径规划返回的折线数据。后端默认通过高德静态地图服务渲染图片，Web Service key 不会下发到浏览器；静态地图请求异常时会返回基于真实路线折线生成的 SVG 示意图。要在浏览器中启用可缩放、可拖拽的高德 JS 地图，请申请 Web 端（JS API）Key 和安全密钥，并在项目根目录 `.env` 配置 `VITE_AMAP_JS_KEY`、`VITE_AMAP_SECURITY_JS_CODE`，然后重新构建前端。浏览器 Key 必须在高德控制台配置域名白名单；没有这两个变量时仍使用静态地图。

## 文件支持

文本类：

- `.pdf`
- `.txt`
- `.md`
- `.csv`
- `.docx`
- `.doc`
- `.xlsx`
- `.xls`

图片类：

- `.png`
- `.jpg`
- `.jpeg`
- `.webp`
- `.gif`

## 文件上下文说明

上传文本附件后，系统会在服务端解析并按页、工作表或段落切分为有界上下文，随后由旅行范围分类与旅行 Supervisor 按当前需求使用。当前策略更偏向“旅行问答主流程 + 文件补充上下文”，适合：

- 行程单解读
- 酒店 / 车票 /机票截图辅助识别
- 攻略文档补充问答
- 会议出差资料和行程需求一起提问

系统不会再为附件创建本地向量库；无法可靠提取的内容会保留明确提示，不会被当作已验证事实。

## 适用场景

- 出差行程助手
- 旅游路线规划
- 城市内交通与天气辅助
- 机票 / 高铁 / 路线联动问答
- 带账户隔离的团队共用旅行工作台

## 已知说明

- 联网搜索默认关闭，需要前端手动开启
- `.doc` 兼容性有限，优先建议使用 `.docx`
- 航班能力依赖桥接配置或外部服务，不同环境返回效果可能不同
- 项目当前使用本地 SQLite 与本地缓存目录，适合单机开发和演示
- `.env` 中如果放了真实密钥，不应提交到公开仓库
- 当前不实现自动订票、支付、锁座或订单提交；航班/车次结果只是查询时的候选信息

## 后续建议

- 增加邮箱验证码注册 / 找回密码
- 为账号体系补充管理员后台
- 给航班结果增加更强的去重、排序与异常兜底
- 把 SQLite 迁移到 MySQL / PostgreSQL，便于正式部署
- 增加 Docker 部署与回归测试脚本

## 开发验证

后端测试：

```bash
python -m pytest -q
python -m compileall -q agents services adapters bridges app.py tests
```

前端检查：

```bash
cd frontend
npm install
npm run typecheck
npm run build
```

更多接口、SSE 事件、TravelPlan JSON、游客 capability 和前端接入边界，见
[`FRONTEND_HANDOFF.md`](FRONTEND_HANDOFF.md)。持续开发状态见
[`PROJECT_PROGRESS.md`](PROJECT_PROGRESS.md) 和 [`FRONTEND_PROGRESS.md`](FRONTEND_PROGRESS.md)。
