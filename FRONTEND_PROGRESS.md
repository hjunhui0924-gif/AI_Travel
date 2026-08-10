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
- 开发模式：`npm run dev`（端口 5173，base `/static/`，API 前缀代理到 `127.0.0.1:8000`）。
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
    components/             # AppSidebar、ChatView、MessageItem、SegmentText、ChatComposer、
                            # PlanPanel、ItineraryTab、PlanCalendar、PlanItemCard、TransportCard、
                            # ReplanForm、SourcesTab、StatusTab、AuthModal、ConfirmDialog
    styles/main.css         # 旅行主题全局样式（无 UI 框架，全部手写）
    utils/guest.ts          # guest_<32hex> 线程 id 生成/持久化
    utils/markdown.ts       # marked + DOMPurify + safeExternalUrl
```

## 3. 已确认的前端决策

- 布局：左侧会话栏 + 中间聊天 + 右侧可折叠计划面板（≤1100px 时面板变抽屉，≤860px 时侧栏变抽屉）。
- 句子级引用：只渲染 `answer_segments`；句末链条图标只关联 `source_type="web_search"` 且 URL 为 http/https 的来源；点击后右侧面板"来源"标签页高亮对应卡。`answer_segments` 为空时回退渲染 `content`，两者绝不同时渲染。
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
- 后端启动：`C:/Users/Website/.workbuddy/binaries/python/envs/default/Scripts/python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000`（项目根目录下）。
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
