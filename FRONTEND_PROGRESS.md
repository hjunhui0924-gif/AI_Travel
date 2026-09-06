# AI_Agent 前端进程

> 本文件是前端开发的持续进程记录，对应后端的 PROJECT_PROGRESS.md。每次前端迭代前必须先阅读本文件与 FRONTEND_HANDOFF.md；完成阶段后立即更新。

## 1. 定位与边界

- 前端是展示和交互 adapter，后端是旅行计划的唯一事实来源。
- 不从 Markdown / final_text / 来源摘要反向解析日期、行程项或引用关系。
- 不直接调用高德、12306、航班、天气、Tavily；不自行排序评分/距离/热度。
- 不隐藏 `risks`、`conflicts`、`adapter_status`、`is_demo`。
- 来源标题、摘要、URL 一律按不可信外部数据处理：Markdown 渲染走 DOMPurify；只有 `http/https` URL 可点击，新标签页必须 `noopener,noreferrer`。

## 2. 技术栈与工程结构

- Vue 3 + TypeScript + Vite 6 + Pinia 3，marked + DOMPurify。
- 源码：`frontend/`；构建产物：`vite build` 输出到 `static/`（`base=/static/`，`outDir=../static`，`emptyOutDir=false`）。
- FastAPI 无需改动：`/` 每次读取 `static/index.html`，`/static` 挂载资产。
- 品牌图片放在 `frontend/public/`（favicon 用）和 `frontend/src/assets/`（组件内 import，构建期指纹化）。
- 开发模式：`npm run dev`（端口 5173，base `/static/`，API 前缀代理到默认 `127.0.0.1:8001`）。
- 常用命令：`npm run typecheck`（vue-tsc）、`npm run build`。

### 目录结构

```
frontend/
  index.html                # 入口，favicon 引用 /static/travel-mark.png
  vite.config.ts            # base、outDir、dev proxy
  src/
    types/api.ts            # 与 FRONTEND_HANDOFF / agents/schemas.py 对齐的契约类型
    api/http.ts             # fetch 封装（credentials:include、错误规范化）
    api/index.ts            # 全部端点 + /chat SSE（fetch+ReadableStream 按空行分帧，兼容 LF/CRLF）
    stores/auth.ts          # 登录态
    stores/session.ts       # 线程/会话、guest id 生成与切换、删除
    stores/chat.ts          # 消息、SSE 流式、附件校验（类型/10MB）、搜索开关
    stores/plan.ts          # TravelPlan、日历、选中日期、PATCH/replan、来源栏、版本预览
    components/             # HomeView、AppSidebar、ChatView、MessageItem、ChatComposer、
                            # PlanDock、PlanPanel、ItineraryTab、PlanCalendar、RouteMap、
                            # PlanItemCard、TransportCard、ReplanForm、SourcesTab、StatusTab、
                            # AuthModal、ConfirmDialog
    styles/main.css         # 旅行主题全局样式（无 UI 框架，全部手写）
    styles/interaction-pass.css # 澄清卡片、转场与交互细节
    utils/guest.ts          # guest_<32hex> 线程 id 生成/持久化
    utils/amap.ts           # 高德 JS API 2.0 加载与交互地图适配
    utils/markdown.ts       # marked + DOMPurify + safeExternalUrl
    assets/destinations/    # 首页景点背景图
```

## 3. 已确认的前端决策

- 布局：左侧会话栏 + 中间聊天 + 右侧可折叠计划面板（≤1100px 时面板变抽屉，≤860px 时侧栏变抽屉）。
- 句子级引用：只有 `answer_segments` 中存在有效网页引用时才使用句子引用组件；句末链条图标只关联 `source_type="web_search"` 且 URL 为 http/https 的来源，点击后右侧面板“来源”标签页高亮对应卡。没有有效引用时渲染完整 Markdown `content`，保证标题和列表正常显示；两条路径绝不同时渲染。
- 计划项 PATCH 始终携带 `expected_version`；409 时刷新计划并用横幅提示用户基于最新版本重试，不静默覆盖。confirmed/booked 强制 `locked=true`；单独 `locked=false` 回退 suggested。
- 计划来源优先级：`done.trip_plan` > `GET /travel/plans/{id}` > 日历/日期接口；PATCH/replan 成功后立即用响应里的完整 plan 更新本地版本号。
- 日历是抽屉：有计划自动展开并高亮行程日期；无计划默认收起，可点开自由翻看任意月份（带"今天"标记）；用户手动展开/收起后记住选择。
- 历史版本预览为只读（预览时不允许 PATCH/重规划），切回"最新"才可修改。
- 游客线程只有在已有内容（有消息或有标题记录）时才显示删除键；删除一律二次确认。
- 登录后通常切换到账户线程（`POST /threads`/`GET /sessions`）；若登录响应返回 `claimed_thread`，说明原 guest id 已归属当前账号，允许继续使用该 id 保留合并后的上下文。
- 登录/注册弹窗只由用户主动点击账户菜单打开；不在首次进入或初始化时自动弹出，游客无需登录即可使用。
- `seat_count` 展示为"查询时库存，仅供参考，不代表锁座或出票"；`is_demo` 标记"演示数据，不可购票"。

## 4. 视觉主题（旅行风）

- 暖纸底色 `#f3eee1` + 深青主色 `#0e5f54` + 赤陶点缀 `#c45f3c` + 暖阳 `#e9a13b`；DM Serif Display 衬线标题、IBM Plex Mono 用于日期/单号/状态。
- 聊天空状态：航线掠过山峦插画 + 大衬线标题；用户消息为明信片卡（左侧暖阳色边条）。
- 交通候选卡：机票/车票式——时间日期分行、虚线航路、票价赤陶色，卡中部撕票虚线 + 两侧打孔（面板底色为暖纸色以衬托纸卡）。
- 日清单：虚线时间轴轨道，锁定项节点实心青色；徽章为邮票式大写描边。
- 计划头：顶部色带的"登机牌"卡；顶栏与面板标题行统一 56px 高、共用分隔线，标题左对齐。

## 5. 当前状态与验证

- `npm run typecheck` 通过；`npm run build` 通过（gzip 后 JS ~71KB、CSS ~5.9KB）。
- 冒烟（隔离 venv `C:\Users\Website\.workbuddy\binaries\python\envs\default`，需补装 `tzdata`）：`/health`、`/`、静态资产、`/auth/me`、guest `/chat` SSE、guest capability cookie、`/travel/plans`、`/calendar`、`/history` 均符合契约。
- **真实 LLM 端到端已通过（2026-08-10 晚，额度已恢复）**：guest 线程实测生成旅行计划（v2，8/15–8/19，10 条交通候选）→ 跨月重规划（v3，8/28–9/2，覆盖 8 月和 9 月）→ 日历接口按日返回 item_count → PATCH 确认+锁定（v3→v4，CAS 生效）→ 过期 expected_version 正确返回 HTTP 409。迷你热力日历翻页逻辑与数据核对一致（planMonths=[2026-08, 2026-09]，8 月视图 next 可点/prev 置灰，9 月视图反之）。

## 6. 已知待办 / 后续方向

- ~~真实 LLM 链路端到端复测~~（2026-08-10 已通过：生成 → 跨月重规划 → PATCH CAS → 409）。
- POI 卡（`PoiRecommendation`）目前后端只在聊天文本中呈现，TravelPlan JSON 未持久化；若后端补充结构化字段，前端在"行程"标签页增加附近推荐区。
- 旧 checkpoint 迁移（`POST /threads/migrate-legacy`）暂未做 UI，需要时加"导入旧会话"入口（仅限用户显式提交 guest_ id，单次最多 100 个）。
- `GET /travel/plans/{id}/calendar` 与 `/days/{date}` 已接入日历摘要 + 按日加载；计划版本切换时会带 `version` 读取对应摘要和日期清单，避免一次拉取所有 PlanItem。
- 移动端细节打磨（计划面板全屏抽屉手势；侧栏遮罩、composer safe-area 已完成）。
- 计划导出与只读分享已接入；提醒仍属于后续能力。

## 7. 环境备忘

- 沙箱安全删除钩子会拦截 `rm`/`Remove-Item` 及 Vite `emptyOutDir` 的目录清空；清理 `static/` 旧文件需用 PowerShell 逐个 `-LiteralPath` 删除，且构建配置保持 `emptyOutDir: false`。
- 托管 Python（Windows）缺时区数据，运行后端前必须 `pip install tzdata`。
- 后端启动：项目根目录执行 `python app.py`，默认监听 `127.0.0.1:8001`；如修改 `AI_AGENT_PORT`，需同步更新 `frontend/vite.config.ts` 的代理目标。
- 前端开发：另开终端执行 `cd frontend`、`npm install`（首次）和 `npm run dev`，访问 `http://127.0.0.1:5173`。
- `D:\develop\python\pythonProject\.venv` 已损坏（指向不存在的解释器），不要使用。

## 8. 变更日志

### 2026-08-10

- 删除旧原生 JS 前端（static/index.html、main.js、style.css），Vue3+TS+Vite 重写并全量接入 FRONTEND_HANDOFF 契约。
- 完成聊天主区（SSE 流式、activity、句子级引用链、附件、搜索开关）、右侧计划面板三标签页（行程/来源/状态）、PATCH CAS、重规划、版本预览、登录/注册/guest 会话管理。
- 第二轮：游客空线程不显示删除键；面板空状态加地图插画与说明；旅行主题视觉重设计。
- 第三轮：日历改为抽屉（有计划自动展开，无计划可点开翻看任意月份 + 今天标记）；顶栏 56px 对齐修复；建立本进程文档。
- 第四轮：聊天空状态（新会话页）下方新增迷你热力日历（MiniCalendarHeat.vue）——纯视觉不可点击；有计划日期按当天项目数分三档点亮，无计划时展示极淡的确定性装饰纹理并标注"生成计划后，行程日期会在这里点亮"（不伪造数据）；今天有赤陶描边。
- 第五轮：迷你日历重做——加周一~周日表头；格子 1fr 铺满卡片；去掉装饰纹理，空格子统一色、有计划日期单一深青高亮；左右翻页只在"有计划"的月份间跳转（跳到最近的有计划月份，方向上没有计划月份时按钮置灰不可点；浏览过去月份时可一键回到当前月）。随后用真实 LLM 链路完成端到端测试（见第 5 节）。
- 第六轮：完成前端对抗式审查修复——SSE assistant 消息改为通过响应式数组更新，修复流式文本/引用不刷新的问题；补充线程代际校验、AbortController 和历史请求竞态保护，避免切换会话/登录注销时计划串线；历史版本预览同步日历；无 `done` 的断流明确标记失败；严格限制来源和图片 URL；修复初始化重复请求、损坏 guest 标题导致启动失败、状态页 `needs_attention` 误报、移动端侧栏遮罩、时区日期计算与删除错误提示。
- 第六轮验证：`npm run typecheck` 与 `npm run build` 通过；Playwright 独立 HTTP SSE 冒烟通过 `citation_flow`、来源侧栏和安全外链属性检查；构建产物仅保留当前 `index.html` 引用的 JS/CSS/图片，历史 hash 包已清理。
- 第七轮：取消首次进入自动登录弹窗；保留主动账户菜单登录/注册入口；接入游客 7 天生命周期和登录合并后的 guest 线程继续聊天契约，日历/日期接口按版本懒加载、计划导出与只读分享保持可用。

### 2026-08-28

- 第八轮：收敛探索页交互——首屏搜索改为安静的单一输入面，快捷建议降为文字入口；提交时从搜索框真实位置展开共享过渡层，再进入工作台，避免页面瞬间替换的突兀感。
- 第八轮：用户消息气泡统一四边内边距并移除造成底部留白的段落 margin；顶部栏补齐中文产品名与图标，滚动时收缩为液态玻璃胶囊，移动端同步检查无横向溢出；首页首个景点确认为北海公园。
- 第八轮验证：`npm run typecheck`、`npm run build` 通过；Playwright Chromium 检查桌面/390px 移动端首页、滚动收缩恢复、回车提交转场、工作台气泡和控制台错误，均通过。
- 第九轮：修复搜索转场背景闪变——将目的地背景与遮罩提升为 `main-column` 内唯一的常驻层，探索页与工作台共用同一背景上下文；转场层改为透明裁剪展开，不再绘制不透明整屏色块；切换期间锁定滚动位置，避免探索页末尾卡片闪出。
- 第九轮验证：Playwright Chromium 多时间点检查确认转场前后背景图片一致、过渡层背景透明、探索页末尾卡片未闪出、转场结束后层正确卸载；桌面与移动端无浏览器错误；`npm run typecheck` 与 `npm run build` 通过。

### 2026-08-29

- 第十轮：修复景点卡片悬停导致背景切换过快的问题。鼠标悬停加入 220ms 意图确认，离开或快速移动会取消待切换；键盘聚焦仍立即切换；背景交叉淡入调整为 760ms。
- 第十轮验证：悬停回归确认 80ms 短停和快速扫过均不换图，持续停留后才换图；真实浏览器策划杭州 3 天游成功生成结构化计划（2026-09-02 至 2026-09-04，日历 3 天，行程项 4 条，地点/天气状态成功），行程面板与状态页可读取；`pytest -q` 为 102 passed、1 warning，`npm run typecheck` 与 `npm run build` 通过。

### 2026-08-29 路线预览

- 背景切换改为 280ms 悬停意图确认 + 920ms 交叉淡入淡出，快速扫过景点卡片不会连续换图；键盘聚焦仍可立即切换。
- 高德路径规划现在保留已验证地点之间的 `origin_location`、`destination_location` 和 `polyline`，写入 `TravelPlan.route_plans`；新增 `GET /travel/plans/{thread_id}/map`，服务端优先调用高德静态地图，不向浏览器暴露 Key。
- 当前 Web Service key 若没有静态地图权限，地图接口返回基于真实高德折线的 SVG 示意图；前端行程面板展示路线图、起终点和距离摘要，不再因静态地图权限不足出现空白或 502。
- 修复常见“杭州三日游”未进入结构化旅行规划的问题，并改进“包含西湖、灵隐寺和河坊街”地点列表解析，确保路线查询使用城市范围内的地点坐标。
- 验证：真实浏览器路线流程生成杭州 3 天游并保存 2 段路线（折线点数 505/537），路线图面板显示；悬停淡入淡出回归、桌面/移动端浏览器审计、`pytest -q`（109 passed、1 warning）、`compileall`、`npm run typecheck`、`npm run build` 均通过。

### 2026-08-29 清理

- 清理了 `static/assets/` 中不再被当前 `static/index.html` 引用的旧 Vite hash 构建产物和历史景点图片；重新构建后仅保留当前入口所需的 JS、CSS、品牌图和 7 张首页景点图。
- 清理了未被源码引用的旧 `MiniCalendarHeat.vue`、目的地 SVG、重复头像/根目录图片及空的旧 `mcp_server` 缓存目录；保留当前旅行 Agent、航班桥、测试、历史兼容逻辑和图片来源说明。
- 清理后验证：入口资源引用缺失数为 0；`pytest -q` 为 102 passed、1 warning；`npm run typecheck`、`npm run build` 和 FastAPI 生产入口浏览器冒烟均通过。

### 2026-08-30

- 第十一轮：前端与旅行范围边界同步——规则未识别的消息展示为旅行范围分类结果；非旅行问题只显示范围说明，不进入通用问答。
- 第十一轮：接入结构化澄清展示。`done.clarification` 会渲染路线选项和自定义城市输入，用户补充后复用原始 `pending_query` 继续规划；澄清轮次不被标记为失败。
- 第十一轮（历史行为）：普通城市请求没有显式景点时，使用已通过地图验证的景点推荐作为路线锚点；当时收到 `done.trip_plan` 后自动打开行程面板。该行为已在 2026-09-03 改为按钮红点通知。
- 第十一轮：旅行正文、澄清和范围拒答均按小段拆分为 SSE `text.delta`，先刷新执行步骤，再逐段更新聊天正文；前端继续以 `done` 作为最终一致性信号。
- 第十一轮：统一记录首屏搜索、工作台转场、悬停背景淡入淡出、液态玻璃顶部栏、用户气泡和高德交互地图的当前行为，便于后续回归测试。
- 当前开发端口约定为：Vite `5173`，FastAPI `8001`；高德 JS API 使用 `VITE_AMAP_JS_KEY` 与 `VITE_AMAP_SECURITY_JS_CODE`，未配置时回退服务端静态路线图。
- 本轮验证：`python -m pytest -q` 为 **125 passed**、1 个既有 Starlette 依赖弃用警告；`python -m compileall -q agents services adapters bridges app.py tests`、`npm run typecheck`、`npm run build` 和 `git diff --check` 均通过；真实浏览器流程确认 72 个文本 SSE 事件、3 条路线图例、行程面板打开且无控制台错误。

### 2026-09-02

- 第十二轮：交通查询严格传递 `travel_mode`；高铁/动车查询只保留 G、D、C 字头，12306 余票接口没有可靠票价时不显示货币金额；单一交通查询不再混入另一种交通方式。
- 第十二轮：行程计划中的交通候选增加 `transport_pages` 分页状态。首次展示 5 条，支持行程面板“加载更多”和继续输入“再给我 N 条高铁/航班”，返回结果仍绑定 provider、查询时间和来源。
- 第十二轮：旅行规划接入受约束的 Travel Supervisor。模型可以按需求选择铁路、航班、POI、路线、天气和联网搜索工具；工具白名单、参数、搜索权限、来源和计划保存仍由代码控制，决策失败时回退确定性路径。
- 第十二轮：完成计划的聊天回复改为摘要式版面，完整交通、路线、天气和地点详情保留在行程计划面板；聊天内容容器局部液态玻璃化，页面风景背景保持独立。
- 第十二轮：聊天滚动区增加 `scrollbar-gutter: stable`，减少流式内容增长时滚动条出现造成的左右跳动；补充桌面和 390px 移动端玻璃容器、无横向溢出和异步布局回归。
- 第十二轮验证：真实高铁、航班和杭州路线浏览器流程通过；高铁首次 5 条、加载更多后 10 条，自然语言追加 3 条成功；Supervisor 真实工具选择通过；全量 `pytest -q` 为 **137 passed**、1 个既有 Starlette 依赖弃用警告；`compileall`、`npm run typecheck`、`npm run build` 和 `git diff --check` 均通过。

### 2026-09-03

- 第十三轮：工作台固定为顶部栏与输入框之间的视口空间，外层页面不滚动，聊天液态玻璃容器承担唯一聊天纵向滚动；玻璃仅作用于聊天区域，风景背景保持可见。
- 第十三轮：TravelPlan 生成改为按钮通知模式。收到新的 `done.trip_plan` 时刷新计划数据但不自动打开面板，在“行程计划”按钮显示红点和“有新安排”，用户打开后清除提醒。
- 第十三轮：后端动作契约同步为模型决定 `clarify / answer / plan / refuse`，查询类不自动升级为行程；无有效 provider 数据时不保存计划。
- 第十三轮验证：1440×900 与 390×844 Chrome 布局检查通过；外层 `document` 无纵向/横向溢出，工作台内容增长后仅内部 `workspace-chat-surface` 的 `scrollHeight` 增长；`npm run typecheck`、`npm run build` 通过；后端全量 `pytest -q` 为 **152 passed**，仅有既有 Starlette 依赖弃用警告。
- 第十三轮补充修正：工作台宽度上限收敛为 1120px；移除聊天玻璃容器下沿硬边和内侧下阴影；工作台内部滚动不再触发顶部栏 compact 动画，顶部栏保持固定。
- 第十三轮补充验证：Chrome 1440×900 回归脚本确认工作台内容增长后高度不变、顶部栏位置不变、页面无溢出、控制台无错误；底部边界已透明化。

## 25. 工作台玻璃与搜索框分离（2026-09-03）

- `ChatView.vue` 将 `ChatComposer` 从消息玻璃容器中移出；`.workspace-chat-surface` 只包住消息列表，`.workspace-chat-scroll-region` 作为唯一聊天滚动根，搜索框在玻璃容器下方独立显示。
- 工作台消息玻璃容器宽度进一步收窄为 860px，与 780px 的独立输入框保持接近比例；移动端继续自适应可用宽度，输入区保持透明外层和独立的半透明输入框。
- 移动端计划通知浮层上移到搜索框上方，避免全宽通知卡覆盖输入控件。
- `App.vue` 的转场初始化改为定位 `.workspace-chat-scroll-region`；消息列禁止 flex 收缩，长回答可以正常滚动且不会改变工作台尺寸。
- 最终验证：开发入口真实杭州规划流程通过；1440×900 和移动端检查确认消息容器收窄、搜索框独立、页面无横向/纵向溢出，内部滚动有效，顶部栏保持固定，浏览器无严重错误。

## 26. 工作摘要与生成停止（2026-09-06）

- 执行步骤改为“工作摘要”：展示可验证的阶段、已选数据源、结果数量和状态，不展示模型内部思维链；摘要详情在工作流完成后仍可从消息中展开查看。
- 发送按钮在 SSE 请求进行时切换为可访问的停止按钮；点击后立即 abort 浏览器流、保留已收到的文本，并在消息中标记“已停止生成”。
- 新增 `POST /chat/cancel` 协作取消接口；服务端按线程和当前账号/guest capability 校验 request id，并在模型/Provider 编排边界停止，无法承诺硬终止已经发出的单次外部请求。
- 验证：前端 `npm run typecheck`、`npm run build`，后端取消回归和完整测试通过；Playwright 桌面流程确认停止按钮、部分文本、工作摘要和控制台状态均正常。

## 27. Qwen 与交通查询重试（2026-09-06）

- 发送失败或 provider 超时会在消息中显示结构化“重试本轮”按钮，最多 2 次手动重试；重试复用原始问题和当前请求附件，不自动无限循环。
- `/chat` 的 `done.retryable/retry_reason` 与历史消息契约已同步；前端请求增加 45 秒客户端总等待保护，超时会保留已有文本并提供重试。
- Qwen3.7 Flash 已作为 DashScope 默认模型；明确交通查询跳过多轮 Supervisor，等待期间仍显示“查询旅行数据”工作摘要。

## 28. 性能与液态玻璃优化（2026-09-06）

- 流式合帧（`frontend/src/stores/chat.ts`）：SSE `text.delta` 不再每个增量立即触发整条消息重渲染；增量先缓冲，用 `requestAnimationFrame` 按动画帧合并提交一次（done/error/断流/finally 路径同步 flush，保证尾部内容不丢）。目标：把长回答“逐 token 全量重解析 + 整块 innerHTML 替换 + 强制滚动”的高频布局开销收敛为一帧一次。
- 液态玻璃收敛（`frontend/src/styles/interaction-pass.css` 末尾新增覆盖段）：工作台消息玻璃容器 blur 26px→16px（saturate/contrast 同步下调），顶部栏 12→8、紧凑胶囊 20→11、输入框 15→9、底部行程浮层 18→10；主容器补 30% 强度的液态玻璃细节（上缘径向高光带、border 提亮、inset 顶部发丝高光），底色/叠色微加深补偿可读性。观感由“深磨砂”收敛为“清透玻璃”，背景轮廓更清晰、文字对比度保持不变。
- 动画期间暂停毛玻璃重采样：状态切换（`.is-transitioning`）与全屏照片交叉淡化（`.app-background` enter/leave）期间，上述玻璃层临时切半透明实色兜底（`backdrop-filter: none`），动画结束立即恢复——消除“照片逐帧变化 × 多层 blur 逐帧重采样”的掉帧源。
- 涉及文件仅上述两个；未改动 main.css、未涉及 FRONTEND_HANDOFF 契约参数；不在本地引入演示开关/组件。
- 验证：`npm run typecheck` 通过；`npm run build`（临时 outDir，未写 `static/`）通过——CSS 107.38 kB（gzip 18.97 kB）、JS 242.97 kB（gzip 86.75 kB）。视觉对照此前已在沙盒副本 `frontend-glass-demo` 用「现状/推荐版」两态开关回归通过；该副本随后已清理。
- 回退：仅需移除 interaction-pass.css 末尾的“性能与液态玻璃 pass”段，并把 chat.ts `send()` 中“流式合帧”注释块还原为逐 delta 直接 `current.content += delta`（勿用 `git checkout` 整文件回退，避免覆盖本会话之前本地已有的未提交改动）。
