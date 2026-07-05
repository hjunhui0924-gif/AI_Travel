# AI Travel Agent

一个基于 `FastAPI + LangChain + LangGraph` 的旅行与出行规划助手项目。  
当前产品形态已经不是“通用 AI 助手”，而是围绕出行场景做了定向能力编排，重点覆盖机票、高铁、路线、天气、周边推荐、文件辅助问答，以及多用户会话隔离。

## 当前定位

这个项目适合以下场景：

- 查询航班、比对出发时间与价格区间
- 查询高铁与出行衔接方案
- 生成城市内路线、通勤建议、到达后周边推荐
- 结合天气、地点和行程背景生成更可执行的旅行计划
- 让不同账号分别保留自己的聊天记录，避免多人共用时串记录

## 主要能力

- 旅行问答工作台：围绕“从哪里出发、什么时候走、到哪里、怎么去”来组织回答
- 机票查询链路：支持通过 Flight MCP Bridge / Ctrip 适配层接入航班数据
- 高铁查询链路：支持 12306 方向的高铁信息整理
- 路线规划：支持地点解析、城市内路径建议与周边推荐
- 天气辅助：支持高德天气查询，为出行建议补充天气上下文
- 文件问答：支持上传 PDF、Word、Excel、Markdown、文本、图片等作为补充上下文
- 搜索补充：前端可手动开启联网搜索，用于时效性信息补充
- 可视化回答：对出行类问题优先渲染更结构化的结果面板，而不是纯文本长回复
- 多会话管理：支持新建、切换、删除历史会话
- 登录与会话隔离：不同账号只能看到自己的聊天记录
- 游客模式：未登录时可临时使用，关闭网页后自动清理游客会话

## 登录与历史记录隔离

当前版本已经支持账号体系：

- 注册 / 登录 / 退出登录
- 游客模式临时会话
- 历史记录迁移到管理员账户
- 不同用户之间的会话完全隔离

隔离规则如下：

- 已登录用户只能访问自己创建的会话
- A 用户不能读取或删除 B 用户的会话
- 游客只能访问 `guest_...` 临时会话
- 游客关闭网页后，临时会话会自动删除

登录信息和会话数据目前保存在本地 SQLite：

- 用户、登录态、会话归属数据库：`resources/ai_agent_threads.db`

## 界面特点

- 左侧会话列表 + 右侧聊天工作台
- 左下角账户模块，点击后弹出登录 / 注册 / 退出菜单
- 旅行类问题优先展示路线、时间轴、天气、周边推荐等结构化卡片
- 切换历史会话时自动定位到底部
- 支持滚动查看历史、回到底部按钮、搜索结果侧抽屉

## 技术栈

- Backend: `FastAPI`
- Agent orchestration: `LangChain`, `LangGraph`
- Frontend: 原生 `HTML + CSS + JavaScript`
- Vector retrieval: `ChromaDB`
- Web search: `Tavily`（可选）
- Map / Weather: `AMap Web API`（可选）
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
│   ├── ctrip_flight_adapter.py
│   ├── flight_mcp_adapter.py
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
├── static/
│   ├── index.html
│   ├── main.js
│   ├── style.css
│   └── travel-mark.png
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

至少准备一组可用的大模型配置，然后按需补充旅行相关能力配置。

### 3. 启动项目

```bash
python app.py
```

浏览器访问：

```text
http://127.0.0.1:8000
```

## 环境变量说明

### 模型配置

- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_API_KEY`
- `LLM_BASE_URL`

兼容备用配置：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `DASHSCOPE_API_KEY`
- `DASHSCOPE_BASE_URL`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`

### 文件检索 / Embedding

- `EMBEDDING_API_KEY`
- `EMBEDDING_BASE_URL`
- `EMBEDDING_MODEL`
- `OPENAI_EMBEDDING_MODEL`

如果不单独配置，会优先复用主模型 key。

### 搜索与时效信息

- `TAVILY_API_KEY`

### 地图 / 天气

- `AMAP_WEB_API_KEY`

### 航班 Bridge

- `FLIGHT_MCP_ENABLED`
- `FLIGHT_MCP_MODE`
- `FLIGHT_MCP_COMMAND`
- `FLIGHT_MCP_TIMEOUT_SECONDS`
- `FLIGHT_BRIDGE_MODE`
- `FLIGHT_MCP_HTTP_URL`

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

- 本地 `bridges/flight_mcp_bridge.py`
- Flight MCP 兼容命令
- 兼容 HTTP 返回的航班服务
- 本地演示 dummy 数据

示例配置：

```env
FLIGHT_MCP_ENABLED=true
FLIGHT_MCP_MODE=command
FLIGHT_MCP_COMMAND=python bridges\\flight_mcp_bridge.py
FLIGHT_BRIDGE_MODE=package
FLIGHT_MCP_TIMEOUT_SECONDS=120
```

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

## 文档检索说明

上传文本附件后，系统会先解析内容，再进入本地 Chroma 检索流程。  
当前策略更偏向“旅行问答主流程 + 文件补充上下文”，适合：

- 行程单解读
- 酒店 / 车票 /机票截图辅助识别
- 攻略文档补充问答
- 会议出差资料和行程需求一起提问

Chroma 运行时目录默认写入：

- `resources/chroma_runtime/`

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

## 后续建议

- 增加邮箱验证码注册 / 找回密码
- 为账号体系补充管理员后台
- 给航班结果增加更强的去重、排序与异常兜底
- 把 SQLite 迁移到 MySQL / PostgreSQL，便于正式部署
- 增加 Docker 部署与回归测试脚本
