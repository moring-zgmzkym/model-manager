# ModelDock 项目索引（改代码前必读）

> 用途：在动任何文件之前，先在这里查清「这个文件负责什么、谁依赖它、改它要连带改哪里」，避免改一处崩一片。
> 扫描基准：2026-10-19，仓库 D:\AI\model-manager；覆盖根目录脚本、backend/、frontend/src/、plugins/（不含 .venv、node_modules、dist 等依赖与产物）。
> 最近校对：2026-10-19 22:25，文件行数、API 路由行号、符号表与 §5 契约已按当前代码核对。
> 行号基于该次扫描；文件行数变化较大时请按 §9 重新核对。

---

## AI 速读（新会话从这里开始）

**项目是什么**：ModelDock —— 本地模型管理器。一个单进程 FastAPI 后端（backend/modeldock）+ React SPA（frontend/src），负责启动/停止本机模型进程，并把这些模型统一到一个工作台里调用。数据目录 data/（config.json、logs、uploads、outputs）。

**四个名词**：

| 名词 | 含义 | 主要位置 |
| --- | --- | --- |
| capability | 用户看到的能力（对话/生图/视频…），决定显示哪个面板 | catalog.py、capabilities.ts |
| protocol | 后端调用协议（openai_chat / comfyui / gpt_sovits…），存在 binding.protocol | catalog.py、invoke.py |
| service | 一个模型进程，key = model.id 或 model.id:capability | process.py、app.py |
| chat job | 一次对话生成的后台任务；HTTP/SSE 只是查看器，切页、刷新不结束任务 | chatjobs.py、ChatPanel.tsx |

**七条硬约束（违反会坏）**：

1. 浏览器不直连模型服务；所有调用必须经 invoke.py（CORS、上传转本地路径、作品入库都在这里）。
2. catalog.py 是能力与协议的唯一真源；前端另有 PANELS 映射，两处必须对齐。
3. PANELS 里每个 id 必须在 ui.tsx 的 PANEL_ICONS 有图标，否则 Studio.tsx:82 直接崩溃。
4. 服务 key 只能是 model.id 或 model.id:capability.type。
5. 生成物一律走 outputs.save_output()，不要自己拼路径或写文件。
6. data/config.json 只允许新增带默认值的字段（没有迁移层）。
7. 对话生成必须留在后端 ChatJob；前端切页/刷新只重新订阅。不要在 ChatPanel unmount 时取消请求或清掉 md-chat-job-<id>。

**改代码的标准流程**：

1. 用 §2 找文件、§3.2/§4.2 找符号、附录 B 查精确行号。
2. 若动到 §5 的契约，先列出全部同步点再动手。
3. 后端 → types.ts → 前端 → 测试。
4. 按 §7.1 验证（后端 pytest、前端 npm run build）。
5. 更新本索引：契约变了改 §5，新文件补 §2 与附录 B，行号大偏移按 §9 校对，进度写进 §10。

**不要做**：

- 不要在后端 import backend/servers/*.py（导入即 argparse + 加载模型）。
- 不要改 process.py 里 _set 的状态字符串（前端徽标、CSS 圆点依赖）。
- 不要用字符串拼接替代 outputs.safe_child() 的路径校验。

## 0. 怎么用这份索引

1. 定位文件：§2 有一句话职责；§3 / §4 有逐文件的关键符号与依赖。
2. 判断影响面：§3.1 与 §4.1 是依赖图，向上找「谁用我」，向下找「我用谁」。
3. 跨端改动：先读 §5 契约表——本项目绝大多数「改完出问题」都发生在契约不同步。
4. 按场景改：§6 给出每类需求必须同步的文件清单。
5. 改完验证：§7 有命令与手工回归路径；§8 列了容易被踩的脆弱点。

---

## 1. 系统全景

### 1.1 技术栈

| 层 | 技术 | 关键约束 |
| --- | --- | --- |
| 启动器 | Python 3.10+ | run.py 负责建前端产物、起 uvicorn、开浏览器/原生窗口 |
| 后端 | FastAPI + uvicorn + pydantic v2 + httpx + psutil | 单进程；modeldock 包 |
| 模型进程 | 任意命令行 | subprocess + shell=True，Windows 下独立进程组 |
| 前端 | React 19 + TypeScript 5.9 + Vite 8 + react-router 7 | tsc -b 严格模式，noUnusedLocals |
| 样式 | 原生 CSS + CSS 变量 | 单文件 styles.css，浅/深主题 |
| 存储 | JSON 文件 | data/config.json、data/outputs/index.json |

### 1.2 启动链

~~~text
start.bat / start.sh
  └─ 首次运行：python -m venv .venv + pip install -r requirements.txt
     └─ .venv/Scripts/python.exe run.py [--host --port --window --no-open --rebuild]
        ├─ ensure_frontend(): frontend/dist/index.html 不存在且装了 npm 时 npm install + npm run build
        └─ uvicorn.Server(modeldock.app:app)
           └─ lifespan: Store() → load_plugins() → sync_services(每个模型) → pm.start_monitor()
              退出时：chat_jobs.shutdown() → 按 stop_models_on_exit 停服（跳过 keep_running_on_exit）
~~~

### 1.3 请求链与数据链

~~~text
浏览器 (React)
  ├─ REST  /api/*        → app.py → detect / probe / process / invoke / outputs / system
  ├─ SSE   /chat         → ChatJobManager（后台任务）→ invoke.chat_stream → 模型服务
  │                        └─ 切页/刷新：任务继续；回来自动轮询 /api/chat/jobs/{id}
  ├─ WS    /api/events   → ProcessManager._emit → store.tsx 就地更新状态与日志
  └─ 静态  /assets, /    → frontend/dist（导入时判断目录是否存在）

模型服务（OpenAI 兼容 / Ollama / ComfyUI / GPT-SoVITS / Omni / Gradio / 自定义 HTTP）
  └─ 生成物 → outputs.save_output() → data/outputs/<日期>/ + index.json → /api/files/outputs/...
~~~

### 1.4 三条设计主线（改代码时不要破坏）

1. **浏览器永远不直连模型服务**：所有调用经后端代理——解决 CORS、把上传转成本地路径（GPT-SoVITS 需要文件路径）、把结果写入作品库。
2. **能力（capability）是 UI 词汇，协议（protocol）是调用词汇**：catalog.py 定义两者映射；前端按 capability.type 选面板，后端按 binding.protocol 选处理器。二者解耦是扩展性的基础。
3. **生成与查看分离**：对话由后端 ChatJob 生成，页面只是 viewer；切页、刷新、断线都不取消任务。不要为了“方便”把生成重新绑回组件生命周期。

---

## 2. 文件清单（按目录）

### 2.1 根目录

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| README.md | 133 | 用户文档：功能、占位符、清单/插件/自定义接口三种扩展方式、对话任务与思考开关 |
| run.py | 79 | 启动器：ensure_frontend、argparse、uvicorn、pywebview 可选窗口 |
| requirements.txt | 12 | 管理器自身依赖（含 pypdf / python-docx 文档解析）；新增后端 import 必须同步 |
| start.bat / start.sh | 10 / 9 | 建 .venv、装依赖、跑 run.py |
| .gitignore | 10 | 忽略 .venv/、data/、demo-data/、frontend/node_modules、dist 与 tsbuildinfo |

### 2.2 后端 backend/

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| demo.py | 98 | 生成 demo-data 下的 5 个仿真模型目录，用 mock 服务当启动命令 |
| modeldock/__init__.py | 3 | 版本号 0.1.0（/api/health 与 OpenAPI 版本来源） |
| modeldock/app.py | 558 | FastAPI 应用：全部 REST/WS 路由、服务同步、SPA 静态托管、ChatJob 路由 |
| modeldock/catalog.py | 78 | **词汇表真源**：13 种能力、11 种协议、12 种适配器 |
| modeldock/chatjobs.py | 182 | 对话后台任务：脱离 HTTP 连接继续生成、快照、取消、30 分钟 TTL |
| modeldock/comfyui.py | 296 | ComfyUI 工作流客户端：上传图片、提交 /prompt、轮询 /history、下载结果 |
| modeldock/documents.py | 82 | 文档提取：txt/md/csv/代码 + PDF(pypdf) + DOCX(python-docx) |
| modeldock/detect.py | 1125 | 文件夹结构分析 + 能力推断（分层证据 + noisy-OR 合并） |
| modeldock/invoke.py | 682 | 统一调用代理：对话流、图像、TTS/克隆、ASR、向量、自定义 HTTP；Ollama think 开关 |
| modeldock/outputs.py | 114 | 生成物与上传文件落盘、索引、路径安全校验 |
| modeldock/paths.py | 29 | 目录常量 + MODELDOCK_DATA 数据目录解析 |
| modeldock/plugins.py | 45 | 从 plugins/*.py 载入 detect() 探测器 |
| modeldock/probe.py | 156 | 对运行中的服务探测能力（/v1/models、/openapi.json、Gradio /config） |
| modeldock/process.py | 395 | 进程生命周期、就绪检测、日志采集、外部服务接管、WebSocket 广播 |
| modeldock/schemas.py | 122 | pydantic 模型：Capability / LaunchConfig / ModelConfig / Settings 等 |
| modeldock/store.py | 74 | config.json 读写（临时文件 + os.replace 原子替换） |
| modeldock/system.py | 50 | CPU/内存/磁盘/nvidia-smi 资源统计（GPU 3 秒缓存） |
| servers/hf_chat_server.py | 154 | 内置 OpenAI 兼容对话服务（transformers + 流式 + 图像） |
| servers/diffusers_server.py | 96 | 内置 OpenAI 图像服务（/v1/images/generations 与 /edits） |
| servers/mock_server.py | 183 | GPU-free 模拟服务：按 --kind 暴露不同端点，测试与 demo 依赖它 |
| servers/omni_server.py | 215 | MiniMind-O 无头服务：复用项目 demo 的加载/推理函数，暴露 /health、/voices、/chat |
| tests/conftest.py | 77 | MODELDOCK_DATA 隔离夹具 + 5 种模型目录样本 models_root |
| tests/test_api.py | 208 | 端到端：启停、全能力调用、SoVITS、失败启动、探测保守性、断线任务恢复 |
| tests/test_detect.py | 183 | 识别层：5 个样本 + 清单覆盖 + 弱提示不启用 + 插件 + Ollama/ComfyUI + 预设 |
| tests/test_process.py | 16 | stop_all 跳过 keep_running_on_exit 的模型 |
| tests/test_sovits.py | 50 | GPT-SoVITS /tts 请求形状（mock HTTP） |
| tests/test_documents.py | 27 | txt/docx 提取与二进制拒绝 |
| tests/test_probe_ollama.py | 51 | Ollama /api/show 的 vision/thinking 能力探测 |
| tests/test_ollama_chat.py | 67 | Ollama /api/chat 原生流、图片与 num_ctx、think=false、上下文错误提示 |
| tests/test_chatjobs.py | 86 | ChatJob 脱离 viewer 继续生成、取消、结束后快照 |
| tests/test_comfyui.py | 37 | 工作流图：占位符替换、可选节点删除、嵌套引用清理 |

### 2.3 前端 frontend/

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| index.html / vite.config.ts / tsconfig.json / package.json | 13 / 14 / 17 / 25 | 构建与代理配置（/api 代理到 7777） |
| src/main.tsx | 22 | 挂载、主题预置、BrowserRouter + StoreProvider |
| src/App.tsx | 32 | 路由表 + 侧栏 + Toast |
| src/styles.css | 462 | 设计系统：CSS 变量、布局、全部组件样式 |
| src/lib/types.ts | 178 | 与后端对齐的 TS 接口（Model / Capability / Catalog 等） |
| src/lib/api.ts | 95 | request / api / upload / extractDocument / streamSSE + ApiError |
| src/lib/store.tsx | 216 | 全局状态、WebSocket、Toast、主题、useLogs |
| src/lib/capabilities.ts | 44 | PANELS / TASKS / has / panelsFor / confidenceLabel |
| src/lib/format.ts | 57 | 字节/时长/时间/问候语/配色/缩写 |
| src/lib/audio.ts | 94 | WAV 编码、转码、录音 hook |
| src/components/ui.tsx | 185 | 图标映射、状态徽标、电源键、头像、Modal、Toast、开关、表单 |
| src/components/Sidebar.tsx | 48 | 导航与主题切换 |
| src/components/AddModelDialog.tsx | 235 | 添加模型三步流：路径 → 识别复核 / 批量挑选 → 保存 |
| src/components/CapabilityEditor.tsx | 89 | 能力开关、添加、证据与置信度展示 |
| src/components/FileBrowser.tsx | 65 | 服务端目录浏览器（新增模型选路径） |
| src/components/LogViewer.tsx | 44 | 实时日志、自动吸底、关键词高亮分类 |
| src/components/Markdown.tsx | 55 | 极简 Markdown 渲染 + think 标签拆分 |
| src/pages/Overview.tsx | 188 | 总览：资源卡、任务入口、ModelCard（被 Models 复用）、最近作品 |
| src/pages/Models.tsx | 70 | 模型库：筛选 + 搜索 + 网格 |
| src/pages/ModelDetail.tsx | 345 | 模型详情 5 个页签：概览/能力/启动配置/日志/高级 |
| src/pages/Studio.tsx | 130 | 工作台外壳：模型列表 + 面板切换 |
| src/pages/Gallery.tsx | 110 | 作品库：图片/音频筛选、删除、Lightbox |
| src/pages/Logs.tsx | 53 | 多服务日志选择与筛选 |
| src/pages/Settings.tsx | 135 | 外观、运行环境、插件状态、清单示例 |
| src/panels/ChatPanel.tsx | 592 | 对话：流式、后台任务恢复、思考开关/折叠、图片/文件/语音输入、语音回答、参数抽屉、草稿持久化 |
| src/panels/ImagePanel.tsx | 182 | 文生图/图像编辑 + Lightbox（被 Gallery 复用） |
| src/panels/ActionPanel.tsx | 345 | 通用 action 面板：预设、动态表单、图片上传、视频/图片结果与历史；按 action id 去重 |
| src/panels/OmniPanel.tsx | 301 | MiniMind-O 原生页面：文字/图片/录音输入，流式文字 + 语音回复、音色选择、草稿持久化 |
| src/panels/AudioPanels.tsx | 297 | AudioInput、TtsPanel（SoVITS 无参考音频时禁用）、AsrPanel |
| src/panels/EmbeddingPanel.tsx | 62 | 文本向量与余弦相似度热力图 |
| src/panels/CustomPanel.tsx | 75 | 按 binding.fields 自动生成表单调用任意 HTTP |
| src/panels/WebuiPanel.tsx | 50 | iframe 嵌入模型自带界面 |
| plugins/_example_plugin.py | 31 | 插件示例（下划线开头不加载） |
| data/config.json | 1461 | 运行时数据（已忽略），能力里内嵌 ComfyUI 工作流，因此体积较大；行数随模型/预设变化 |

---

## 3. 后端索引

### 3.1 后端依赖图

~~~text
run.py ────────────────► modeldock.app:app
                          │
  app.py ────────────────┼──► schemas.py ◄── store.py, detect.py, invoke.py, probe.py
    │                    ├──► catalog.py ◄── detect.py, schemas.py（Capability 校验）
    │                    ├──► paths.py   ◄── store.py, outputs.py, plugins.py, process.py, system.py
    │                    ├──► detect.py  ◄── probe.py
    │                    ├──► probe.py
    │                    ├──► process.py ──► paths.py
    │                    ├──► invoke.py  ──► outputs.py ──► paths.py, schemas.new_id
    │                    ├──► chatjobs.py ──► invoke.chat_stream（由 app 注入 factory）
    │                    ├──► documents.py
    │                    ├──► outputs.py
    │                    ├──► system.py
    │                    └──► plugins.py
    │
    └── servers/*.py 由子进程独立运行，不被 import（不要在后端 import 它们）
~~~

> 注意：backend/servers/hf_chat_server.py 与 diffusers_server.py 在**导入时就执行 argparse 并加载 torch**。任何 import 它们的行为都会拖垮后端或直接退出。

### 3.2 逐文件：职责 / 关键符号 / 依赖关系 / 改动注意

#### backend/modeldock/documents.py（82 行）

- 关键：MAX_CHARS=200_000（:15）、TEXT_SUFFIXES（:16）、DocumentError（:24）、extract()（:61）。
- 支持：txt/md/csv/json/yaml/代码/日志等文本；PDF 用 pypdf；DOCX 用 python-docx；未知二进制类型返回 400。
- 注意：20MB 上限在 app.py 的路由里；文本超过 20 万字符会截断并标记 truncated。

#### backend/modeldock/paths.py（29 行）

- 关键：PACKAGE_DIR / BACKEND_DIR / PROJECT_DIR / SERVERS_DIR / FRONTEND_DIST / PLUGINS_DIR（:8-13）、data_dir()（:16）、sub_dir()（:26）。
- 被谁用：几乎全部后端模块。
- 注意：data_dir() 每次调用都会 mkdir 并读取环境变量 MODELDOCK_DATA；测试与 demo 都靠它隔离数据。改目录结构会同时影响 run.py 的 frontend/dist 判断与 process.py 的内置服务路径。

#### backend/modeldock/schemas.py（122 行）

- 关键：new_id()（:12，10 位 hex）、Capability（:19）、LaunchConfig（:47，含 keep_running_on_exit :61）、ModelConfig（:64）与 base_url()（:79）、Settings（:89）、DetectRequest / DetectResult（:98 / :102）、ProbeResult（:116）。
- 被谁用：store、app、detect、invoke、probe、outputs。
- 注意：
  - Capability._default_binding（:39）只在 binding.protocol 缺失时补默认协议；半截自定义 binding 不会被覆盖。
  - 字段增删直接影响**磁盘上的 config.json**：新增字段必须给默认值，否则旧配置执行 ModelConfig(**item) 时直接抛错。
  - ModelConfig.base_url() 是后端唯一的 URL 推导处；前端展示用的是 model_view 里的同名返回值。

#### backend/modeldock/catalog.py（78 行）

- 关键：CAPABILITIES（:13，13 项）、PROTOCOLS（:42，11 项）、ADAPTERS（:56，12 项）、default_binding()（:72）、catalog_payload()（:77）。
- 被谁用：detect.py（默认协议、排序）、schemas.py（校验器）、app.py（/api/catalog），并间接决定整个前端的展示。
- 注意：**这是词汇表的唯一真源**。新增能力/协议/适配器从这里开始，并同步 §5.1、§5.2 列出的位置。每个能力条目里的 panel 字段会被模型详情页用来做跳转目标，需与 frontend/src/lib/capabilities.ts 的 PANELS[].id 保持一致。

#### backend/modeldock/store.py（74 行）

- 关键：Store.load()（:24）、save()（:35，tmp + os.replace）、list_models()（:47，按 created_at）、upsert()（:53，刷新 updated_at）、delete()（:60）、used_ports()（:67）、update_settings()（:70）。
- 被谁用：app.py（store_ref 单例）、demo.py。
- 注意：进程内 RLock 保护，**没有跨进程锁**；同一数据目录不要同时跑两个后端。save() 每次写入全量模型列表。

#### backend/modeldock/detect.py（1125 行）

- 常量：ENABLE_THRESHOLD=0.5（:31）、SOURCE_RANK（:32）、SKIP_DIRS（:33）、SCRIPT_EXT（:37）、MAX_ENTRIES=5000 / MAX_DEPTH=3（:38-39）。
- 预设：_video_presets（:426，快速 864×480/49、本地最高 1152×640/22、官方 1344×768/360）、_qwen_image_presets（:439）、_qwen_edit_presets（:452）；VIDEO_HINTS（:420）用于泛化视频模型识别（Wan/HunyuanVideo/LTX/CogVideo/Mochi/Cosmos/SVD 等）。
- Ollama：detect_ollama（:677）解析 Modelfile 的 PARAMETER num_ctx 存入 defaults；GPT-SoVITS（:741）启动超时 600 秒，能力说明提示约 2~3GB 显存。
- 能力合并：cap()（:43，置信度 ≥ 阈值才默认启用）、_cap_key()（:54，custom 用 label 区分）、merge_caps()（:58，noisy-OR + 证据并集 + 按来源强度选 binding）。
- 扫描：Scan（:117）、scan()（:142）、local_python()（:165，识别整合包自带解释器）。
- 提示表：KEYWORDS（:184）、OMNI_KEYWORDS（:197）、NAME_RULES（:198）、COMPONENT_DIR_RULES（:207）、CONFIG_KEY_RULES（:212）；对应 readme_hints（:219）、name_hints（:236）、component_hints（:246）、config_key_hints（:256）。
- 源码嗅探：PORT_PATTERNS / sniff_port（:276 / :284）、ServerScript / sniff_server_scripts（:295 / :302）、find_scripts（:348）、script_command（:354）。
- ComfyUI 工作流：find_comfy_dir（:367）、comfy_models（:374）、_best_unet（:402）、_minimax_workflow（:460）、_qwen_workflow（:506）、_qwen_edit_workflow（:531）、detect_comfyui（:557）。
- 适配器：Draft（:729）、detect_gpt_sovits（:741）、detect_diffusers（:775）、detect_gguf（:801）、analyze_hf_config（:836）、detect_hf（:867）、detect_minimind（:918）、detect_script_project（:966）、apply_manifest（:1014）、prettify（:1042）。
- 入口：detect()（:1047）：URL → 远程适配器；否则按 gpt_sovits → diffusers → gguf → comfyui → ollama → minimind 优先级（:1064-1065）；都没有时再合并 hf + script_project（:1067-1077），叠加提示（:1080）、插件（:1085）、清单（:1102），最后按 CAPABILITIES 顺序排序（:1118）。
- 被谁用：app.py（/api/detect、/api/detect-batch、rescan）、probe.py（cap / merge_caps）、demo.py、测试。
- 注意：
  - 调整 ENABLE_THRESHOLD 会整体改变「默认开启」的行为，test_detect.py::test_single_weak_hint_stays_disabled 会先挂。
  - merge_caps 对 custom 以 label 为键：两个没有 label 的自定义能力会互相覆盖。
  - detect_hf 需要同时具备 config.json（含 architectures/model_type）**和**权重文件，否则返回 None——这是「明明有 config 却识别成 custom」的最常见原因。

#### backend/modeldock/comfyui.py（296 行）

- 关键：PLACEHOLDER（:38）、OUTPUT_KINDS（:39）、ComfyError（:48）、base_of()（:54）、pick_action()（:59）、_substitute()（:71）、_drop_node()（:84）、build_graph()（:99）、coerce_values()（:114）、action_fields()（:129）、graph_for()（:133）、_upload()（:143）、_fetch()（:160）、_error_from()（:172）、run()（:189）、interrupt()（:289）。
- 工作流占位符：双花括号。整串匹配时保留原始类型（数字仍是数字），嵌在字符串里则转成文本。
- 可选节点：节点带 "_if": "字段名"；该字段为空时删除节点，并删除所有指向它的输入（含嵌套列表里的引用）。MiniMax 的首帧/尾帧就是这样在 t2v 时消失的。
- run() 流程：image 字段 → resolve_upload → POST /upload/image → 换成 ComfyUI 文件名；POST /prompt；每 1.5 秒轮询 /history/{id}；把 images/videos/gifs/audio 逐个下载后交给 save_output。
- 注意：模板在提交前深拷贝，不会被改写；超时取 binding.timeout（默认 3600 秒）。

#### backend/modeldock/probe.py（156 行）

- 关键：ROUTE_CAPS（:18，路径正则 → 能力+协议）、VISION_NAME / AUDIO_NAME（:26-27）、_ollama_show（:30，POST /api/show 读 capabilities）、probe()（:64）。
- 三路探测：/v1/models（:75，OpenAI 兼容，回填 served_models）、/openapi.json（:97，按路由推断）、/config（:138，Gradio）与根路径兜底。
- Ollama：/api/show 报告 vision 时补 vision 能力，并把 chat 的 protocol 改成 ollama_chat；thinking / tools 写入 notes。
- 注意：**多路由保守策略**（:75-85）——同一服务同时暴露多个 OpenAI 路由时，非 chat 能力只给 0.35 置信度、默认不启用。test_api.py::test_probe_generic_server_is_conservative 与 test_probe_single_route_server 锁定了该行为，改阈值或权重会同时影响这两个测试。

#### backend/modeldock/process.py（395 行）

- 数据结构：ServiceSpec（:33，启动参数）、Service（:50，运行时状态 + snapshot() :63）。
- stop_all(managed_only, skip_model_ids)（:325）：退出时跳过 keep_running_on_exit 为真的模型。
- 工具：expand()（:79，只替换已知占位符，保留 JSON 花括号）、port_open()（:87）、kill_tree()（:96，整棵进程树）、pid_on_port()（:118）、decode()（:128，UTF-8/GBK 容错）。
- ProcessManager（:135）：subscribe / unsubscribe（:146 / :151）、_emit（:154）、emit_threadsafe（:161）、_set（:165，状态变化广播并触发 on_running）、_log（:178，落盘 + 广播）、register（:193）、get（:202）、forget（:205）、values（:209，占位符取值）、healthy（:213）、start（:226）、_reader（:271）、stop（:292）、stop_all（:325）、监控 start_monitor / shutdown / _monitor / _tick（:333 / :337 / :341 / :351）。
- 状态机：stopped → starting → running → stopping → stopped，异常为 error。
- 注意：
  - start() 会先检测端口占用：可接管且健康则标 external（:234），否则报「端口已被占用」。
  - register()（:193）在服务已运行（非 stopped/error）时**保留旧 spec**：改了启动配置但模型正在运行时不会立即生效，需要停止后重启。
  - _tick（:351）处理三类边界：启动脚本退出但端口仍活（转 external，:359）、外部服务连续 3 次健康失败转 stopped（:382-387）、外部已跑服务被自动接管（:390）。
  - 日志同时写 data/logs/<key 中的冒号换成下划线>.log。

#### backend/modeldock/invoke.py（682 行）

- 基础设施：InvokeError（:30，带 HTTP 状态）、client()（:36，长读超时 900s，trust_env=False）、find_cap()（:40，按类型顺序找第一个启用能力）、base_of()（:48，支持 binding 覆盖 base_url）、served_name()（:54）、headers_for()（:58，Bearer）、data_url_to_bytes / pcm16_to_wav / sniff_audio（:63 / :70 / :80）、raise_for（:92）。
- 对话：build_messages（:102，多模态分段）、sse（:125）、chat_stream（:223，含语音输出参数与 400 回退重试 :263）。
- Ollama 原生：build_ollama_messages（:135）、_ollama_error_message（:149）、ollama_chat_stream（:169）；chat_stream 在 protocol=ollama_chat 时走它，显式带 num_ctx，图片用裸 base64。payload 含 think 时写 body["think"]（:187-190）：false 直接回答，true 才产生 message.thinking。
- 图像：fetch_image_bytes（:325）、image_generate（:338，编辑走 multipart）。
- 语音：tts（:460，v1/v2 两套 GPT-SoVITS 协议）、transcribe（:518）、sovits_weights（:575，扫描 GPT_weights* 与 SoVITS_weights*）、sovits_set_weights（:591）。
- Omni：omni_chat_stream（:390）、omni_voices（:417）。
- 视频：video_generate（:432，ComfyUI 轮询）、video_interrupt（:445）。
- 向量：embed（:547，返回维度与余弦相似度矩阵）。
- 自定义：_render（:611，双花括号变量模板）、_pluck（:624，a.b[0] 取值）、custom_call（:635，表单/file/base64/二进制响应）。
- 注意：这里是**所有模型调用的唯一出口**；新增协议请加分支而不是在 app.py 里直接发请求，否则会失去统一错误处理与作品入库。

#### backend/modeldock/outputs.py（114 行）

- 关键：outputs_dir() / uploads_dir()（:23 / :27）、list_outputs()（:35）、save_output()（:49，日期/时分秒_编号.扩展名，索引保留 5000 条）、delete_output()（:71）、save_upload()（:85，编号_安全文件名）、resolve_upload()（:93，接受 id、/api/files/uploads/... 或绝对路径）、safe_child()（:108，防目录穿越）。
- 注意：save_output 返回的 url 与 resolve_upload 的解析规则是一对契约；改路径格式必须同时改这两处以及前端的 img/audio 使用点。

#### backend/modeldock/plugins.py（45 行）

- 关键：Detector（:18）、load_plugins()（:21），返回 (detectors, info)；跳过下划线开头文件；模块名 modeldock_plugin_<stem>；要求可调用 detect；异常捕获进 info[].error/trace。
- 注意：插件在每次调用时重新加载（/api/plugins/reload），但已注册的旧探测器会被整体替换。

#### backend/modeldock/system.py（50 行）

- 关键：gpus()（:17，nvidia-smi，3 秒缓存）、stats()（:37，CPU/内存/磁盘）。
- 注意：无 NVIDIA 时返回空列表；前端据此显示占位文案。

#### backend/modeldock/chatjobs.py（182 行）

- ChatJob：status 为 running / done / error / cancelled；聚合 content、reasoning、error、elapsed、usage；subscribers 是 SSE viewer 的队列。
- ChatJobManager：create（:91，创建 asyncio.Task）、_consume（:104，独立消费 invoke.chat_stream）、stream（:136，只读队列）、cancel（:158）、_prune（:165）、shutdown（:176）。
- 关键：viewer 断开时 stream 的 finally 只移除队列，**不取消 task**；任务继续写聚合状态。完成后保留 30 分钟，最多 30 个。
- 注意：内存态，后端重启即丢；前端靠 sessionStorage + 快照恢复。不要在 unmount 时清任务，否则又回到“切页就罢工”。

#### backend/modeldock/app.py（558 行）

- 单例与工具：store_ref / get_store()（:28 / :31）、python_default()（:35）、pm（:40）、chat_jobs（:41）、plugin_state / reload_plugins()（:42 / :45）。
- 服务镜像：main_spec()（:50）、sub_specs()（:57，key 为「模型 id:能力类型」）、sync_services()（:70）、model_view()（:76，模型 + base_url + runtime + services）、free_port()（:87）、merge_runtime()（:95，保留用户锁定项）、on_running()（:109，运行后探测并合并）。
- 生命周期：lifespan()（:130）；退出时先 chat_jobs.shutdown()，再按 stop_models_on_exit 停服，并跳过 launch.keep_running_on_exit 为真的模型（ComfyUI）。
- 中间件与错误：CORS 全开、InvokeError → JSONResponse、must()（:156）。
- 路由：见 §3.3。
- ChatJob 路由：POST /api/models/{id}/chat（:417，创建任务并返回 X-Chat-Job）、GET /api/chat/jobs/{job_id}（:427，快照）、POST /api/chat/jobs/{job_id}/cancel（:435）。
- 静态托管：FRONTEND_DIST 存在才挂 /assets 并注册 SPA 兜底（:547-553）——**该判断发生在模块导入时**。
- 注意：
  - needs_port()（:208）只在命令含 {port} 或命令为空时自动分配端口。
  - rescan()（:271）中「svc and svc.status == running or m.adapter == openai_remote」依赖运算符优先级（and 先于 or）：远程服务无论是否运行都会探测。改这段时请显式加括号。
  - create_model 与 update_model 都会 sync_services，但运行中的 spec 由 register 保留（见 process.py）。

#### backend/servers/*.py（独立子进程）

| 文件 | 端点 | 约定 |
| --- | --- | --- |
| hf_chat_server.py | /health、/v1/models、/v1/chat/completions | 导入即解析参数并加载模型；参数 --model/--name/--host/--port/--dtype/--device-map |
| diffusers_server.py | /health、/v1/images/generations、/v1/images/edits | 编辑端点依赖 python-multipart；--cpu-offload 省显存 |
| mock_server.py | 按 --kind 暴露 /v1/models、chat、embeddings、speech、transcriptions、images、/tts、权重切换、webui | --delay 模拟加载；parse_known_args 容忍未知参数 |
| omni_server.py | /health、/voices、/chat（SSE：delta/asr/audio/status/error/done） | 必须用 MiniMind-O 自己的 .venv 启动；导入 web_demo_omni.py 的函数，不运行 Gradio |

注意：测试大量依赖 mock 的端点存在性（例如 --kind image 没有 /v1/models，因此探测只能走 /openapi.json 与 /health）。改 mock 的端点集合会连带影响 test_api.py 的多个用例（全能力、SoVITS、两个探测）。

### 3.3 API 路由总表

| 方法 | 路径 | 实现 | 前端调用方 | 说明 |
| --- | --- | --- | --- | --- |
| GET | /api/health | app.py:164 | — | 版本与数据目录 |
| GET | /api/catalog | app.py:169 | store.tsx:89 | 能力/协议/适配器 + 插件状态 |
| POST | /api/plugins/reload | app.py:174 | Settings.tsx:98 | 重载插件并返回信息 |
| GET | /api/system | app.py:180 | Overview.tsx:63 | 资源统计 + 运行模型数 |
| GET | /api/settings | app.py:187 | store.tsx:75 | 设置 + 数据目录/解释器/平台 |
| PUT | /api/settings | app.py:193 | Settings.tsx:41 | 覆盖保存设置 |
| GET | /api/models | app.py:199 | store.tsx:70 | 全部 model_view |
| GET | /api/models/{id} | app.py:204 | 测试 | 单个 model_view |
| POST | /api/models | app.py:213 | AddModelDialog.tsx:55,71 | 新建（ID 冲突 409；自动端口） |
| PUT | /api/models/{id} | app.py:225 | ModelDetail.tsx:58；AudioPanels.tsx:120,128 | 整体替换，保留 created_at |
| DELETE | /api/models/{id} | app.py:237 | ModelDetail.tsx:87 | 先停相关服务再删记录 |
| POST | /api/detect | app.py:249 | AddModelDialog.tsx:35 | 单目录识别 |
| POST | /api/detect-batch | app.py:254 | AddModelDialog.tsx:39 | 扫描子目录，带 already_added |
| POST | /api/models/{id}/rescan | app.py:271 | ModelDetail.tsx:73 | 重新识别（文件 + 运行探测） |
| POST | /api/probe | app.py:290 | — | 任意 URL 探测 |
| POST | /api/models/{id}/start | app.py:295 | store.tsx:147 | 远程适配器且无命令时 400 |
| POST | /api/models/{id}/stop | app.py:304 | store.tsx:161；WebuiPanel.tsx:21 | 停止并回收进程树 |
| POST | /api/models/{id}/restart | app.py:311 | ModelDetail.tsx:127 | stop + start |
| POST | /api/models/{id}/services/{ctype}/{action} | app.py:318 | ModelDetail.tsx:94；WebuiPanel.tsx:19 | 附属服务 start/stop |
| GET | /api/logs/{key} | app.py:332 | store.tsx:206 | 最近日志（默认 1000 行） |
| DELETE | /api/logs/{key} | app.py:338 | ModelDetail.tsx:296 | 清空内存日志（文件仍在） |
| GET | /api/services | app.py:346 | — | 全部服务快照 |
| GET | /api/fs | app.py:352 | FileBrowser.tsx:21 | 目录浏览（最多 500 项） |
| POST | /api/open-folder | app.py:381 | ModelDetail / Settings / Gallery | 资源管理器打开目录 |
| POST | /api/models/{id}/omni/chat | app.py:401 | OmniPanel.tsx | MiniMind-O SSE：delta / asr / audio / status / done |
| GET | /api/models/{id}/omni/voices | app.py:412 | OmniPanel.tsx | 音色列表 |
| POST | /api/models/{id}/chat | app.py:417 | ChatPanel.tsx | 创建 ChatJob；SSE 事件流，响应头 X-Chat-Job |
| GET | /api/chat/jobs/{job_id} | app.py:427 | ChatPanel.tsx | 任务快照：status/content/reasoning/error/elapsed/usage |
| POST | /api/chat/jobs/{job_id}/cancel | app.py:435 | ChatPanel.tsx | 取消后台任务（停止按钮） |
| POST | /api/models/{id}/image | app.py:443 | ImagePanel.tsx:80；ActionPanel.tsx | OpenAI 图像或 ComfyUI action（含 qwen 编辑） |
| POST | /api/models/{id}/video | app.py:448 | ActionPanel.tsx | ComfyUI 视频任务（t2v / i2v / flf2v） |
| POST | /api/models/{id}/video/stop | app.py:453 | ActionPanel.tsx | 中断当前 ComfyUI 任务 |
| POST | /api/models/{id}/tts | app.py:458 | AudioPanels.tsx:146 | 合成/克隆 |
| POST | /api/models/{id}/asr | app.py:463 | AudioPanels.tsx:258 | 转写 |
| POST | /api/models/{id}/embed | app.py:468 | EmbeddingPanel.tsx:17 | 向量 + 相似度 |
| POST | /api/models/{id}/custom/{index} | app.py:473 | CustomPanel.tsx:27 | 按 custom_http 能力序号调用 |
| GET/POST | /api/models/{id}/sovits/weights | app.py:482,487 | AudioPanels.tsx:104,134 | 列出/切换 GPT 与 SoVITS 权重 |
| POST | /api/extract | app.py:493 | ChatPanel.tsx | 文档转文本（txt/md/pdf/docx，20 万字上限） |
| POST | /api/uploads | app.py:504 | api.ts:49 | 上传附件，返回 id/path/url/size |
| GET | /api/outputs | app.py:510 | Overview / Gallery / ImagePanel / AudioPanels | 作品索引（可按 kind/model 过滤） |
| DELETE | /api/outputs/{oid} | app.py:515 | Gallery / ImagePanel / AudioPanels | 删文件 + 索引 |
| GET | /api/files/{area}/{rel:path} | app.py:520 | img / audio 直接用 URL | area 取 outputs 或 uploads，safe_child 防穿越 |
| WS | /api/events | app.py:532 | store.tsx:101 | 状态/日志/模型变更推送 |
| GET | /{full:path} | app.py:550 | BrowserRouter | 仅当 dist 存在时注册 |

> `/chat` 的 SSE 只是任务查看器：浏览器断开、切到日志页或刷新都不会取消后台生成。刷新后 ChatPanel 用 GET /api/chat/jobs/{id} 的快照恢复；停止按钮才调用 cancel。

### 3.4 数据模型与持久化

**Capability**（schemas.py:19）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| type | str | 能力类型，应在 catalog.CAPABILITIES 内（否则按 custom_http 兜底） |
| enabled | bool | 是否在工作台显示 |
| source | manifest/files/runtime/user/plugin | 证据强度，决定合并时谁覆盖谁 |
| confidence | float | 0~1，≥0.5 默认启用 |
| evidence | str[] | 展示给用户的识别依据 |
| binding | dict | 协议与参数：protocol / path / url / api / fields 等 |
| label | str? | 自定义能力用来区分的名字（也是合并键） |
| actions | dict[] | 子功能（t2v/i2v/edit 等）：id / label / fields / defaults / presets / workflow |
| locked | bool | 用户手改过，重扫不覆盖 |

**LaunchConfig**（schemas.py:47）：command、stop_command、cwd、python（空=用设置里的默认解释器）、env、host、port、health_path、startup_timeout、adopt_external、keep_running_on_exit。

**ModelConfig**（schemas.py:64）：id（10 位 hex）、name、path、adapter、description、tags、color、launch、api_base、capabilities、defaults（model / api_key / voices 等运行时约定）、created_at / updated_at、base_url()。

**Settings**（schemas.py:89）：language、theme、port_range_start、default_python、model_roots、stop_models_on_exit。

**磁盘格式**（data/config.json，version: 1）

~~~json
{
  "version": 1,
  "settings": { },
  "models": [ ]
}
~~~

演进规则：只加可选字段；不要在 load() 里假设新字段存在；Settings(**raw) 与 ModelConfig(**item) 是硬校验，缺默认值会导致整个模型库加载失败。

**事件与生成物**

- 日志：data/logs/<key>.log（冒号替换为下划线），内存保留 3000 行。
- 上传：data/uploads/<id>_<安全文件名>。
- 作品：data/outputs/<YYYY-MM-DD>/<HHMMSS>_<id><ext> + data/outputs/index.json（最多 5000 条）。

### 3.5 能力 / 协议 / 适配器词汇表

**能力（13 种）与面板**

| type | 标签 | 协议 | 面板 panel |
| --- | --- | --- | --- |
| chat | 文本对话 | openai_chat | chat |
| vision | 图像理解 | openai_chat | chat |
| audio_input | 语音输入 | openai_chat | chat |
| speech_output | 语音输出 | openai_chat | chat |
| asr | 语音识别 | openai_transcription | asr |
| tts | 语音合成 | openai_speech | tts |
| voice_clone | 声音克隆 | gpt_sovits | tts |
| image_generation | 文生图 | openai_images | image |
| image_edit | 图像编辑 | openai_images | image |
| video_generation | 视频生成 | comfyui | video |
| embedding | 文本向量 | openai_embeddings | embedding |
| webui | 原生界面 | webui | webui |
| custom | 自定义接口 | custom_http | custom |

**协议（11 种）**：openai_chat、openai_images、openai_speech、openai_transcription、openai_embeddings、gpt_sovits、comfyui、omni、ollama_chat、webui、custom_http。上表列的是能力默认协议；运行时探测可以把 chat/vision 改成 ollama_chat（qwen3.5 就是这种情况）。

**适配器（12 种）**：hf_llm、hf_omni、gguf、diffusers、gpt_sovits、gradio_app、script_project、openai_remote、comfyui、ollama、omni、custom。适配器只影响展示与默认启动命令，不决定调用方式（调用方式由协议决定）。

### 3.6 服务与进程模型

- 服务 key：主服务 = model.id；附属服务 = model.id + 冒号 + capability.type（仅当能力 enabled 且 binding.command 非空）。
- 就绪判定：health_path 非空则 HTTP GET 且状态码 < 500；否则只测端口。
- 停止：有 stop_command 先跑；否则杀整个进程树；外部接管服务按端口找 PID 后杀（会跳过自身 PID）。
- 接管：adopt_external 为真且端口已开且健康 → 标记 external，不视为自己启动。
- 退出：lifespan 结束时先 chat_jobs.shutdown()，再按 stop_models_on_exit 只停自己管理的服务。

### 3.7 WebSocket 事件

| 事件 | 字段 | 触发点 | 前端处理 |
| --- | --- | --- | --- |
| hello | services[] | 连接建立（app.py:536） | 未使用（store 忽略） |
| status | Service.snapshot() + type=status | _set() 状态变化 | 按 key 更新 runtime 或 services[ctype] |
| log | key、t、line | _log() | 分发给 useLogs 订阅者 |
| model | id | 运行探测合并后（app.py:119） | 触发 refresh() 拉全量模型 |

---

## 4. 前端索引

### 4.1 依赖分层

~~~text
main.tsx ─► App.tsx ─┬─ Sidebar / Toasts(ui)
                     └─ pages/*
pages/* ─┬─ components/*（ui, Markdown, CapabilityEditor, LogViewer, AddModelDialog, FileBrowser）
         ├─ panels/*（Chat / Omni / Image(+Lightbox) / Action / Audio(Tts,Asr) / Embedding / Custom / Webui）
         └─ lib/*（api, store, capabilities, format, audio, types）
lib/store.tsx ─► lib/api.ts ─► fetch / WebSocket
~~~

关键反向依赖（移动/拆分文件时注意）：

- pages/Overview.tsx 导出 ModelCard，被 pages/Models.tsx 使用。
- panels/ImagePanel.tsx 导出 Lightbox，被 pages/Gallery.tsx 使用。
- components/ui.tsx 的 CAP_ICONS / PANEL_ICONS 被几乎所有页面间接使用。

### 4.2 逐文件说明

#### src/lib/types.ts（178 行）

- Status（:1）、Binding（:3）、CustomField（:15）、ActionField（:24）、ActionPreset（:36）、ActionSpec（:44）、Capability（:53，含 actions）、LaunchConfig（:65）、Runtime（:79）、Model（:91）、DetectResult（:110）、CapabilityInfo / Catalog（:125 / :133）、OutputItem（:140，kind 含 video）、LogLine（:154）、Settings（:159）、SystemStats（:171）。
- 注意：这是**前端唯一的后端契约镜像**。后端 model_view 或 Capability 加字段时先改这里；tsconfig 严格模式下未使用字段不会报错，但拼写错误会。

#### src/lib/api.ts（95 行）

- ApiError（:1）、request()（:9，统一 JSON/FormData 与错误提取）、api（:38）、upload()（:45）、extractDocument()（:53，文档转文本）、streamSSE()（:60，按空行切事件）。
- 注意：streamSSE 只认 data: 行且忽略解析失败；后端新增 SSE 事件类型只需在前端消费处处理，不用改这里。

#### src/lib/store.tsx（216 行）

- Store 接口（:13）：models / catalog / settings / loading / connected / refresh / refreshSettings / getModel / start / stop / onLog / toast / toasts / theme / setThemePref。
- StoreProvider（:37）：主题（:44-61，localStorage 键 md-theme）、toast（:63）、refresh（:69）、首次加载三请求（:88）、WebSocket 重连（:95-132）、8 秒轮询（:135）、start / stop 的乐观更新（:144 / :157）、onLog（:177）。
- useStore()（:194）、useLogs()（:200，历史 + 实时合并，上限 3000）。
- 注意：start 只合并返回的 runtime，其余字段靠 WS/轮询刷新；stop 先本地置 stopping 再对齐结果。

#### src/lib/capabilities.ts（44 行）

- PANELS（:4，面板顺序即工作台标签顺序，**第一项是默认面板**）、enabledCaps（:15）、has（:19）、panelsFor（:23）、TASKS（:29，总览页快捷入口）、confidenceLabel（:37）。
- 注意：这里与后端 catalog.CAPABILITIES[].panel 是**两份映射**；ModelDetail 用后端的 panel 做跳转，Studio 用前端的 PANELS 决定渲染。两边不一致会导致「点能力进了错误面板/回退到第一个面板」。

#### src/lib/format.ts（57 行）/ src/lib/audio.ts（94 行）

- format：bytes / duration / timeAgo / clock / greeting / modelColor / initials，纯函数、无副作用，可放心复用。
- audio：encodeWav（:4）、toWav（:30，用 AudioContext 转 16k 单声道）、blobToDataUrl（:47）、useRecorder（:56，MediaRecorder + 计时）。
- 注意：toWav 返回 WAV，是因为多数模型服务不接受 webm/opus；修改采样率会影响 ASR 与参考音频质量。

#### src/components/ui.tsx（185 行）

- CAP_ICONS（:10，**按能力 type**）、PANEL_ICONS（:26，**按面板 id**）、CapIcon（:37，缺图标回退 Sparkles）、CapChip（:42）、StatusBadge（:60）、PowerButton（:70）、ModelAvatar（:93）、Modal（:102）、Toasts（:127）、Switch（:141）、Empty（:150）、Field（:161）、Slider（:171）。
- 注意：PANEL_ICONS 缺条目时 Studio.tsx:82 会把 undefined 当组件渲染并崩溃；CAP_ICONS 有兜底，不会崩。

#### src/components/AddModelDialog.tsx（235 行）

- 三步：路径输入（:191）→ 单模型复核（:88）或批量挑选（:146）→ 保存并跳转。toModel()（:11）负责 DetectResult → Model（远程服务把 path 写进 api_base）。
- 注意：批量保存是**串行** await（:71），失败会中断后续；新增字段请同步 toModel，否则保存时丢失。

#### src/components/CapabilityEditor.tsx（89 行）

- 任何手动修改都会写 locked: true（:18）；addable（:15）允许重复添加 custom；只有 source === user 的能力可删除（:50）。

#### src/components/FileBrowser.tsx（65 行）/ LogViewer.tsx（44 行）/ Markdown.tsx（55 行）

- FileBrowser：调 /api/fs，files 模式可选文件（当前仅作为目录选择流程的扩展点）。
- LogViewer：cls()（:5）按关键字着色；吸底判定（:29）。
- Markdown：renderMarkdown（:16）与 splitThink（:51，解析 think 标签）。渲染走 dangerouslySetInnerHTML，esc() 已转义 HTML，新增语法时务必保持转义顺序。

#### src/pages/*

| 页面 | 关键点 | 注意 |
| --- | --- | --- |
| Overview.tsx | ModelCard（:14，被 Models 复用）、资源轮询 3s（:65）、任务入口（:133）、最近作品（:170） | TASK_ICONS（:12）缺 TASKS 新 id 会崩溃 |
| Models.tsx | FILTERS（:9）按能力组筛选 + 搜索 | 新能力组需加入 FILTERS[].caps 才能被筛到 |
| ModelDetail.tsx | 5 页签（:13）、脏检查（:51）、保存/重扫/删除（:54 / :70 / :85）、能力 JSON 应用、退出保留开关 | 保存提交整个 Model；runtime/services 由后端重新计算 |
| Studio.tsx | 模型侧栏、面板切换（:81）、未就绪提示（:93）、按 panelId 渲染（:107-121） | PANEL_ICONS 与 PANELS 必须同步；webui 不要求主服务就绪 |
| Gallery.tsx | 图片/音频分区、模型筛选、删除、Lightbox | 依赖 Lightbox 的导出位置 |
| Logs.tsx | 服务列表 = 主服务 + 附属服务（:9） | useState 初值只在首帧计算（:15），模型异步加载后可能仍为空，需要用户点选 |
| Settings.tsx | 主题、解释器、端口起点、数据目录、插件列表、清单示例 | PUT /api/settings 提交整份 draft（含只读展示字段），后端按 Settings 模型过滤 |

#### src/panels/*

| 面板 | 关键点 | 注意 |
| --- | --- | --- |
| ChatPanel.tsx | 流式渲染、ChatJob 快照恢复、思考开关与折叠、图片/文件/录音、语音回答开关、参数抽屉、停止 | 输入文本存 localStorage，对话/附件存 sessionStorage；发送时写 md-chat-job-<id>，切页/刷新后轮询 /api/chat/jobs/{id}；jobId 标记 assistant 消息；工作台“思考”chip 与参数抽屉共用 params.think |
| ImagePanel.tsx | 生成/编辑双模式、比例/步数/引导/种子、历史列表、Lightbox | 编辑模式必须先 upload 拿到 id；size 直接取 RATIOS[].size |
| ActionPanel.tsx | 通用 action 面板：画质预设（选中态）、动态表单、图片字段先上传、结果与历史 | 字段值和预设存 localStorage（键含 model/action），刷新后恢复；手动改字段会切回“自定义” |
| OmniPanel.tsx | MiniMind-O 专用：文字/图片/录音、音色下拉、流式回答、语音播放与下载 | 草稿与最近 20 条存 sessionStorage，刷新可恢复；语音不入作品库 |
| AudioPanels.tsx | AudioInput（:23）、TtsPanel（:78，SoVITS 音色卡与权重切换）、AsrPanel（:248） | 音色保存在 model.defaults.voices，通过 PUT /api/models/{id} 整份提交；sovits 判定看 binding.protocol（:81） |
| EmbeddingPanel.tsx | 多行输入、相似度热力图 | 后端返回已算好的矩阵，前端不再计算 |
| CustomPanel.tsx | 按 binding.fields 渲染表单（textarea/select/file/number/text） | file 字段先上传再传 id，后端 custom_call 转为路径或 base64 |
| WebuiPanel.tsx | iframe + 刷新/新窗口/启停 | 有 binding.command 视为附属服务（走 services 路由），否则算主服务 |

### 4.3 前端路由（App.tsx:17-27）

| 路径 | 组件 | 说明 |
| --- | --- | --- |
| / | Overview | 总览 |
| /models | Models | 模型库 |
| /models/:id | ModelDetail | 详情 |
| /studio、/studio/:id | Studio | 工作台，?panel= 指定面板 |
| /gallery | Gallery | 作品库 |
| /logs | Logs | 运行日志 |
| /settings | Settings | 设置 |
| * | Overview | 兜底 |

注意：使用 BrowserRouter，深链接依赖后端 /{full:path} 兜底；后端启动时若 dist 不存在则不会注册该路由（见 §8-13）。

### 4.4 前端状态流

~~~text
StoreProvider
  ├─ 首次：GET /api/models + /api/catalog + /api/settings
  ├─ WS /api/events：status → 就地更新；model → 全量刷新；log → 分发给订阅者
  ├─ 每 8s 且页面可见：GET /api/models（刷新内存/运行时长）
  ├─ start/stop：乐观更新 + 用返回快照兜底
  ├─ ChatJob：发送写 sessionStorage；挂载/切页回来后轮询 /api/chat/jobs/{id}
  └─ 主题：localStorage.md-theme + documentElement.dataset.theme
~~~

### 4.5 能力 → 面板 → 组件（改这里要三处对齐）

| 面板 id | 覆盖能力 | 组件 | 渲染入口 |
| --- | --- | --- | --- |
| chat | chat / vision / audio_input / speech_output | ChatPanel，或 OmniPanel（protocol=omni） | Studio.tsx:108 |
| image | image_generation / image_edit | ImagePanel，或 ActionPanel（protocol=comfyui） | Studio.tsx:111 |
| video | video_generation | ActionPanel | Studio.tsx:114 |
| tts | tts / voice_clone | TtsPanel | Studio.tsx:117 |
| asr | asr | AsrPanel | Studio.tsx:118 |
| embedding | embedding | EmbeddingPanel | Studio.tsx:119 |
| webui | webui | WebuiPanel | Studio.tsx:120 |
| custom | custom | CustomPanel | Studio.tsx:121 |

### 4.6 样式系统（src/styles.css，462 行）

- CSS 变量集中在 :root（:2）与 [data-theme=dark]（:38）：--bg / --bg-elev / --bg-soft / --bg-sunken / --sidebar / --border / --border-strong / --text / --text-2 / --text-3 / --accent / --accent-soft / --success / --warning / --danger 及阴影、圆角、--sidebar-w。
- 分段注释即目录：layout(84) / primitives(130) / empty·banners(234) / avatar(244) / stats(252) / model cards(261) / tables·lists(291) / capabilities(299) / modal(313) / toasts(334) / logs(340) / studio(354) / chat(373) / image(419) / audio(440) / heatmap(450)。
- 注意：主题切换只靠变量；新增样式**不要写死颜色**，否则深色主题失效。常用类：page / card / btn / chip / field / section-head / grid-2 / grid-3 / stack / row / spacer。

---

## 5. 跨端契约（改一处必须同步另一处）

### 5.1 新增一种能力类型（最容易漏的地方）

必须同步：

1. backend/modeldock/catalog.py 的 CAPABILITIES（标签、group、panel、protocol），必要时 PROTOCOLS。
2. backend/modeldock/schemas.py：无需改，校验器会补 protocol。
3. backend/modeldock/detect.py：可选，加识别规则/关键词（KEYWORDS / NAME_RULES / CONFIG_KEY_RULES）。
4. backend/modeldock/probe.py 的 ROUTE_CAPS：若该能力有 HTTP 端点。
5. backend/modeldock/invoke.py + app.py：新路由与处理函数。
6. frontend/src/lib/capabilities.ts 的 PANELS（caps 数组，必要时新面板）与 TASKS（若要在总览出现）。
7. frontend/src/components/ui.tsx 的 CAP_ICONS（+ 新面板时 PANEL_ICONS）。
8. frontend/src/pages/Studio.tsx 渲染分支。
9. frontend/src/pages/Models.tsx 的 FILTERS（若要参与筛选）。
10. backend/tests/test_detect.py 与 test_api.py 增补用例。

### 5.2 新增一种调用协议

catalog.PROTOCOLS（展示名）→ invoke.py 对应函数分支（并在 find_cap 允许的类型内）→ app.py 路由 → frontend/src/lib/types.ts 的 Binding 字段 → 面板里的 binding.protocol 判断（如 AudioPanels.tsx:81、CustomPanel.tsx:11）→ README.md。

### 5.3 服务 key 约定

「模型 id」与「模型 id:能力类型」出现在：process.py（注册/日志文件名）、app.py（sub_specs / delete_model）、store.tsx:115（split 冒号取 [1]）、Logs.tsx:12、WebuiPanel.tsx:19、ModelDetail.tsx:94。**不要改成别的分隔符**。

### 5.4 SSE 事件约定（ChatJob → ChatPanel）

> `/chat` 先创建 ChatJob，再用 SSE 把任务事件回推给当前页面。SSE 断开（切日志页、刷新、关标签）只移除 viewer，不取消生成；刷新后前端用 GET /api/chat/jobs/{id} 的快照恢复 content/reasoning/status。

| 事件 | 载荷 | 前端行为 |
| --- | --- | --- |
| delta | text | 追加正文 |
| reasoning | text | 追加思考过程 |
| audio | url、output | 挂载 audio autoPlay |
| notice | message | Toast 提示（如语音参数回退） |
| error | message | 气泡内错误条 |
| done | elapsed、usage | 写入耗时/速率 meta |

### 5.5 占位符

{python}、{port}、{host}、{servers}、{name} 由 process.values()（process.py:209）返回；expand()（:79）只替换已知键，未知花括号原样保留（保护 JSON 命令）。前四个是文档承诺的公开占位符（README.md 有表格，ModelDetail.tsx:258 与 AddModelDialog.tsx:125 有提示文案），{name} 目前只在命令替换中可用。

### 5.6 URL 与文件契约

- 作品 URL：/api/files/outputs/<日期>/<文件名> ↔ outputs.save_output 的 url 字段 ↔ resolve_upload。
- 上传 URL：/api/files/uploads/<文件名> ↔ app.serve_file ↔ AudioPanels 的 pickVoice（对 v.ref 的兼容处理）。
- resolve_upload 同时接受：上传 id、/api/files/uploads/... URL、绝对路径。改动其中任一形式都会影响 TTS/ASR/图像编辑/自定义接口。

### 5.7 状态枚举

Status = stopped | starting | running | stopping | error，出现在：process.py 的 _set 调用、types.ts:1、ui.tsx 的 STATUS_TEXT（:52）、CSS 的 .dot.<status>。新增状态必须同时补这三处，否则徽标无文案或圆点无色。

---

### 5.8 action（子功能）契约

Capability.actions 是一个列表，每项：id、label、description、fields、defaults、workflow（可选，覆盖 binding.workflow）。

- 后端：comfyui.pick_action 按请求里的 action 选择；字段类型 number 会被强制转成数字；image 字段先落到 uploads 再上传到 ComfyUI。
- 前端：ActionPanel 按 actions 渲染标签页；没有 actions 的能力会退化成单个默认表单。
- 目前内置 action：video_generation 的 t2v / i2v / flf2v；image_generation 的 t2i / edit；image_edit 的 edit。
- presets：每个 action 可带 {"id","label","values","note"} 列表；点击后覆盖对应字段，后端只接收最终字段值。
- 去重：多个能力可能暴露同一个 action id（如 image_edit 的 edit），前端只显示一次。
- 字段类型约定：text、textarea、number、select、image。新增类型要同时改 ActionPanel 的渲染分支。

### 5.9 omni 契约（MiniMind-O）

- chat / vision / audio_input / speech_output 四项能力共用 binding.protocol = "omni"，同一个进程提供全部输入输出。
- 启动：launch.python 指向项目自己的 .venv，命令为 {python} "{servers}/omni_server.py" --repo ... --model ... --host {host} --port {port}；健康检查 /health。
- /chat 请求：text、image_b64、audio_b64（允许 data URL）、voice、history、temperature、max_tokens。
- /chat 事件：delta、asr、status、audio（base64 WAV，24kHz）、error、done；前端 OmniPanel 消费。
- /voices 返回 {voices, builtin, clone}；音色来自项目的 model/speaker/*.pt。
- 注意：不要同时运行 start-webui.bat；两者抢 8888。

### 5.10 文档附件契约

- ChatPanel 先 POST /api/extract 取文本，再把 “文件：<name>\n<text>” 拼进当前消息 content；后端 build_messages 不需要新字段。
- 支持 txt/md/csv/json/代码/日志 + PDF（pypdf）+ DOCX（python-docx）；单文件 20MB、20 万字符上限，超出截断。
- 文件文本会进入对话历史，后续轮次仍可见；长文档会占用上下文，必要时清空对话。

### 5.11 Ollama 原生契约

- chat / vision 的 binding.protocol = ollama_chat（不再走 OpenAI 兼容层）。
- 请求：POST /api/chat，body 含 model、messages、stream、options；options 里显式带 num_ctx（取 binding.num_ctx 或 defaults.num_ctx，当前 8192）、temperature、top_p、num_predict。
- 图片：messages[].images 使用裸 base64（去掉 data: 前缀）。
- 响应 NDJSON：message.thinking → reasoning，message.content → delta，done → usage（prompt_eval_count / eval_count）。
- 思考开关：body 顶层 think=true/false。qwen3.5 默认开启思考；ChatPanel 默认发送 false，开启后才发送 true。思考 token 也计入 num_predict，不能把“只有思考没有答案”当成上下文错误。
- 上下文超出时后端翻译成中文提示，不再把原始 JSON 塞进气泡。

### 5.12 聊天任务（切页/刷新）契约

- 创建：POST /api/models/{id}/chat，body 可带 request_id（前端 crypto.randomUUID）。后端创建 ChatJob，task 独立运行，响应头 X-Chat-Job 返回实际 id。
- SSE 只是 viewer：客户端断开时 Starlette 取消的是 stream 生成器，chatjobs._consume 任务不受影响，继续聚合 content/reasoning。
- 快照：GET /api/chat/jobs/{job_id} 返回 id/model_id/status/content/reasoning/error/elapsed/usage；status = running | done | error | cancelled。
- 取消：POST /api/chat/jobs/{job_id}/cancel；只用于用户按停止，不用于页面卸载。
- 前端：发送时把 {id,index,started} 写 sessionStorage 的 md-chat-job-${model.id}，并给 assistant 消息加 jobId；ChatPanel 挂载时若存在任务，每 700ms 轮询快照并更新该条消息。
- 卸载规则：ChatPanel 的 mounted ref 为 false 时，done/catch 不清 sessionStorage；否则切到日志页再回来会丢失任务 id。只有在当前页面正常完成或用户停止时才清。
- 生命周期：任务在内存里保留 30 分钟、最多 30 个；后端重启会丢任务（前端轮询 404 后清 key）。长对话的历史只存最近 20 条，恢复时优先按 jobId 找消息，找不到再按 index 兜底。
- 测试：tests/test_chatjobs.py（viewer 断开/取消/事后快照）与 test_api.py::test_chat_job_keeps_generating_after_the_viewer_disconnects。

### 5.13 停止与退出契约

- launch.keep_running_on_exit = true 的模型（ComfyUI）在 ModelDock 退出时不会被 stop_all 停止；模型详情 → 启动配置里可手动开关。
- 前端 store.stop 对 ComfyUI 弹窗确认；WebuiPanel 停止非附属服务时也走 store.stop。
- 设置里的“退出时关闭模型”仍然生效，只是会跳过上述模型。

## 6. 修改场景手册

| 我要做… | 必须改 | 一并检查 / 验证 |
| --- | --- | --- |
| 加一种能力 | §5.1 全部 10 项 | CAP_ICONS 与 PANEL_ICONS；PANELS 顺序决定默认面板 |
| 加一种协议 | catalog.PROTOCOLS、invoke.py、app.py、types.ts、面板判断 | 错误统一抛 InvokeError，结果走 save_output |
| 加一种模型识别 | detect.py（Draft + detect_* + 入口优先级 + ADAPTERS） | 置信度 ≥0.5 才默认启用；test_detect.py 的 5 个样本不能回归 |
| 加识别插件 | 只放 plugins/*.py（非下划线开头，含 detect） | /api/plugins/reload 或重启；Settings 页会显示错误堆栈摘要 |
| 加后端接口 | app.py 路由 + types.ts + api.ts 调用方 | 错误用 HTTPException 或 InvokeError；路径前缀 /api |
| 加前端页面 | pages/*.tsx + App.tsx 路由 + Sidebar.tsx 导航 | 深链接依赖 SPA 兜底 |
| 加前端面板 | capabilities.ts PANELS + ui.tsx PANEL_ICONS + Studio.tsx + 新组件 | 面板 id 要与后端 catalog.panel 对齐 |
| 改配置字段 | schemas.py（给默认值）+ types.ts + 表单页 | 旧 config.json 必须还能加载；考虑 version 升级 |
| 改主题/样式 | styles.css 变量区 + 组件类名 | 深色主题回归；不要硬编码颜色 |
| 改内置推理服务 | servers/*.py 的端点与 --port/--host | 必须保持 health_path 可访问；OpenAI 兼容字段别改名 |
| 改启动/停止 | process.py（start/stop/_tick）+ schemas.LaunchConfig | Windows 进程组、外部接管、健康检查三条路径都要过一遍 |
| 改生成物存储 | outputs.py + resolve_upload + 前端 URL 使用点 | 旧作品索引仍需可读 |
| 改识别阈值 | detect.ENABLE_THRESHOLD 与 probe 置信度 | 先跑 test_detect.py 与两个 probe 用例 |

---

## 7. 验证清单

### 7.1 命令

~~~bash
# 后端测试（首次运行如未装 pytest，先执行 pip install pytest）
cd backend
..\.venv\Scripts\python.exe -m pytest -q

# 前端类型检查 + 构建（tsc -b 严格模式，未使用的 import 也会报错）
cd frontend
npm run build

# 只做类型检查
npm run typecheck

# 热更新开发（代理 /api 到 127.0.0.1:7777）
npm run dev

# 重新构建并由后端托管
python run.py --rebuild
~~~

### 7.2 测试与代码的映射

| 测试 | 覆盖 |
| --- | --- |
| test_detect.py 的 5 个样本用例 | 五种真实目录形态的识别结果与启动命令 |
| test_manifest_overrides | modeldock.json 覆盖名称/端口/能力来源 |
| test_single_weak_hint_stays_disabled | 弱证据不自动启用（阈值回归） |
| test_plugin_detector | 插件返回值合并与 notes |
| test_api.py::test_full_lifecycle | 建模型 → 启动 → SSE 对话（含推理与语音）→ 生图 → TTS → ASR → 向量 → 作品库 → 重扫 → 停止 |
| test_api.py::test_chat_job_keeps_generating_after_the_viewer_disconnects | 路由层断开 SSE 后任务仍完成，快照可取回 |
| test_chatjobs.py | ChatJob 脱离 viewer 继续、取消、结束后快照；不依赖模型 |
| test_ollama_chat.py | 原生 /api/chat 流、图片裸 base64、num_ctx、think=false、上下文错误翻译 |
| test_gpt_sovits_flow | SoVITS 参考音频必需 + 错误文案 |
| test_failed_start_reports_error | 进程异常退出时错误信息含日志尾部 |
| test_detect_endpoint_and_fs / test_detect_batch | 识别接口与目录浏览 |
| test_probe_generic_server_is_conservative / test_probe_single_route_server | 多路由保守、单路由确认 |

### 7.3 手工冒烟（改前端或链路后）

1. 总览：资源卡、任务入口、最近作品。
2. 添加模型：单个识别 → 复核页 → 保存 → 跳详情；批量扫描 → 勾选 → 保存。
3. 详情：5 个页签、保存脏检查、重扫、能力 JSON 应用、附属服务启停、日志清空。
4. 工作台：切换模型、切换面板、URL 的 ?panel= 直达、未启动时的引导。
5. 对话：流式、思考开关（默认关；开启时折叠）、图片、录音、语音回答、停止、参数抽屉。发送后立刻点“运行日志”再回来；再发一条后刷新页面——两次答案都应恢复并写入 meta，不能只剩空气泡。
6. 图像：生成、编辑、历史、Lightbox、删除。
7. 语音：普通 TTS、SoVITS 音色保存与权重切换、ASR、音频删除。
8. 作品库/日志/设置：筛选、打开目录、插件重载、主题切换。

---

## 8. 已知脆弱点与注意事项

按「改这里最容易出事」排序：

1. **面板图标缺条目会崩溃**：Studio.tsx:82 直接渲染 PANEL_ICONS[p.id]，新增 PANELS 项时必须补图标（CAP_ICONS 有 Sparkles 兜底）。
2. **默认面板由数组顺序决定**：Studio.tsx:32 取 panels[0]，调整 PANELS 顺序会改变进入工作台的默认页。
3. **两份面板映射**：后端 catalog.CAPABILITIES[].panel 与前端 PANELS 各自维护；ModelDetail.tsx:195 用后端值跳转，不一致会落到默认面板。
4. **custom 能力以 label 作为合并键**（detect.py:54）：两个没写 label 的自定义能力会互相覆盖，保存前给它们不同 label。
5. **Capability 校验器只补缺失的 protocol**（schemas.py:39）：手工写了半截 binding 时不会被补全。
6. **运行中改启动配置不生效**：ProcessManager.register（process.py:193）在非 stopped/error 时保留旧 spec；改完要停止再启动。
7. **自动分配端口有条件**（app.py:208）：只有命令含 {port} 或命令为空才分配；否则端口留空，健康检查退化为只看进程。
8. **config.json 无迁移层**：新增必填字段会让旧数据整体加载失败；只加默认值。
9. **demo.py 会清空目标数据目录的模型列表**（demo.py:74）：不要把 MODELDOCK_DATA 指向真实 data。
10. **同目录不能跑两个后端**：Store 只有进程内锁。
11. **前端构建严格**：tsconfig 开了 noUnusedLocals / noUnusedParameters，遗留 import 会让 npm run build 失败（vite build 单独跑不会）。
12. **data/ 已被 git 忽略**：调试用的模型配置不会进版本库，复现问题要贴 config.json 片段。
13. **SPA 兜底在导入时决定**：后端启动时 frontend/dist 不存在则 /{full:path} 不会注册；先构建再启动，或重启后端。
14. **hf_chat_server.py / diffusers_server.py 导入即执行**：不要在后端或测试里 import，只能作为子进程启动。
15. **mock 端点集合是测试契约**：--kind image 无 /v1/models，--kind sovits 只有 /docs 可探；改 mock 会影响 test_api.py 的 4 个用例（full_lifecycle、gpt_sovits、两个 probe）。
16. **chat_stream 的 400 回退只重试一次**（invoke.py:263）：新增「可能不被服务支持」的参数时，要么复用该模式，要么前后端都别默认发送。
17. **safe_child 是唯一路径护栏**（outputs.py:108）：/api/files 与 SPA 兜底都依赖它，替换实现时不要退化成字符串拼接。
18. **Logs.tsx 初值只算一次**（Logs.tsx:15）：模型异步加载后可能没有选中项，属已知小瑕疵，改动时顺手修更稳。
19. **测试用全局 app 单例**：test_api.py:17 的 client fixture 手动清 store_ref 与 pm.services；新增模块级状态时必须同步清理。
20. **rescan 的运算符优先级**（app.py:271）：and 先于 or，实际语义是「运行中」或「远程适配器」；改写时显式加括号。
21. **outputs 索引上限 5000**（outputs.py:67）：超出后旧条目被丢弃但文件仍在磁盘，作品库会「看不见但占空间」。
22. **localStorage / sessionStorage 键名即数据兼容**：md-theme、md-chat-<id>、md-chat-params-<id>、md-chat-draft-*、md-chat-job-<id>；改键会让用户历史、草稿或任务恢复丢失。
23. **ComfyUI 工作流嵌在配置里**：换权重文件后必须重新识别（rescan）才会更新工作流里的文件名；直接改文件名会让任务提交失败。
24. **ComfyUI 端口占用与就绪**：start-comfyui.bat 末尾有 pause，服务崩溃时 bat 可能挂住；若端口已开但 /system_stats 不可用，ModelDock 会报“端口已被其他程序占用”，先确认 8189/8188。
25. **视频任务只在后端轮询**：执行期间前端只显示“生成中”，ComfyUI 的节点进度不在 ModelDock 日志里；中断走 POST /api/models/{id}/video/stop。
26. **共享 ComfyUI 与停止误伤**：ComfyUI 常被别的程序或 agent 共用。现在点停止会弹窗确认，keep_running_on_exit 为真的模型在退出 ModelDock 时也不会被停止；但 stop-comfyui.bat 仍会按路径杀掉该目录下所有进程，手动停止前要确认没有别人的任务。
27. **qwen 2K 需要 2K 启动脚本**：2048×2048 应使用 start-comfyui-2k.bat；普通模式在 2K 下可能崩。预设会提示，但不会自动切换启动命令。本机历史产物 2048×1152 已证明可用，2048×2048 未实测。
28. **GPT-SoVITS 显存与实测**：推理约需 2~3GB 显存，与 ComfyUI 视频模型同时运行会 CUDA OOM。显存释放后已实测成功：3.36 秒 32kHz WAV，参考音色克隆正常；TTS 面板在缺少参考音频时会禁用。
29. **刷新保留范围**：聊天与 Omni 的草稿、最近 20 条（含图片/音频）存 sessionStorage，同一标签页刷新可恢复；localStorage 只留纯文本历史，跨标签页或重启浏览器时附件不会恢复。ChatJob 的 id 也存 sessionStorage（md-chat-job-<id>），刷新后靠它轮询恢复正文。
30. **action 去重**：image_generation 与 image_edit 都可能带 edit action，ActionPanel 按 action.id 去重，避免出现重复的“图像编辑”页。
31. **MiniMind-O 必须用项目自己的解释器**：omni_server.py 依赖 torch / funasr / gradio 等，launch.python 指向 D:\AI\MiniMind-O\.venv\Scripts\python.exe；换机器要同步改。
32. **8888 端口语义已改变**：以前是 Gradio 页面，现在是 Omni 服务。不要再同时运行 start-webui.bat，否则启动会报端口占用。
33. **Omni 的图片/音频按 data URL 直传**：单张图片或几秒录音没问题；长音频会让请求体变大，后续可改成先上传再传 id。
34. **Ollama 视觉能力靠运行时探测**：只有 /api/show 报告 vision 时才出现图片按钮；模型未启动时看不到，启动后 on_running 探测或点“重新识别”才会写入。探测会把 chat 的协议改成 ollama_chat。
35. **图片必须是有效文件**：Ollama 对损坏的 data URL 返回 Failed to load image or audio file，这不是接口不支持；换真实图片即可。
36. **文件输入走文本提取**：浏览器先 POST /api/extract，再把文本拼进消息；PDF/DOCX 依赖 pypdf/python-docx，未知二进制会被拒绝。
37. **8GB 硬件上限**：1344×768/49 实测 GPU OOM；1152×640/49 重试时采样完成（8/8，约 24 秒/步），但在 VAE 解码/保存阶段 ComfyUI 再次崩溃。因此本机视频预设保持 1152×640/22（历史产物验证），qwen 用 2048×1152；1344×768/360 仅作官方参考。
38. **Ollama 上下文与思考**：OpenAI 兼容路径默认按 4096 截断；现在走原生 /api/chat 并显式 num_ctx=8192。不要把 qwen3.5 的 chat 协议改回 openai_chat，否则图片和长上下文会再次 400。think 必须显式控制：ChatPanel 默认 false；true 时 message.thinking 单独流，且思考 token 计入 num_predict。
39. **草稿与预设的持久化**：ChatPanel 文本存 localStorage，附件/最近对话存 sessionStorage；ActionPanel 的字段值和选中的预设存 localStorage（键含 model/action）。清浏览器站点数据会一起清掉。
40. **报错长度**：服务端错误已转成中文短句；气泡和 .banner 增加 overflow-wrap，避免长 JSON 撑破版面。
41. **ChatJob 是内存态**：后端重启即丢，GET /api/chat/jobs/{id} 返回 404；前端会清 key 并保留已存文本，不要把它当持久化队列。
42. **不要在 ChatPanel unmount 时清任务**：mounted ref 为 false 时只停止 setState，不要删 md-chat-job-<id>、不要 abort；切到“运行日志”再回来正是靠这个 key 恢复。用户按停止才调用 /cancel。
43. **思考链不等于上下文超限**：qwen3.5 默认 thinking，简单问题也可能产出上千 token 思考（实测 9.11/9.9 用 2070 字、24.9 秒）。日志里 truncated=0 且 n_tokens < num_ctx 就说明不是上下文爆了，先检查 think 开关和 max_tokens。

---

## 9. 索引维护

- 本索引与源码一起提交；改到 §5 列出的契约时，**同一次提交内**更新对应小节。
- 行数漂移不必立刻修；但 §3.2 / §4.2 的关键行号若偏移超过约 10 行，应按新代码校正。
- 重新统计行数（PowerShell，排除依赖与产物）：

~~~powershell
Get-ChildItem -Recurse -File -Include *.py,*.ts,*.tsx,*.css,*.json,*.md -Path backend,frontend\src,plugins |
  Where-Object { $_.FullName -notmatch 'node_modules|__pycache__|dist' } |
  ForEach-Object { "{0,6}  {1}" -f @(Get-Content $_.FullName).Count, $_.FullName }
~~~

- 新增文件时：在 §2 表格加一行，在 §3.2 / §4.2 加一段（职责 / 关键符号 / 依赖 / 注意），并在依赖图中接线。
- 符号表：Python 用 ast 取顶层 FunctionDef/ClassDef/Assign；前端用 rg 匹配顶层 export。改完代码先跑一次，避免行号整段漂移。

## 10. 进行中的改造（活文档）

> 每次改动后更新本节；完成一项就把 [ ] 改成 [x]，并把设计结论写进 §5 或对应模块说明。

### 10.1 用户诉求（原始）

1. 本地模型能真正「一键启动 / 关闭」，并在本项目里直接使用，而不是只能去原生网页。
2. qwen3.5 启动后工作台没有可用窗口；MiniMind-O、qwen-image-2.1 只能在 ComfyUI / Gradio 原生页使用。
3. 内置识别把 MiniMax-H3 当成普通项目，没认出它是视频生成模型。
4. 需要识别「主功能里的小功能」：如 MiniMax-H3 的首帧/尾帧生视频、qwen-image-2.1 的图片元素分离。

### 10.2 根因（改造前，已核实，勿重复排查）

> 下表是项目接入这 5 个模型之前的原始问题；当前 data/config.json 已修复，保留它用于避免重复踩坑。

| 现象 | 根因 | 位置 |
| --- | --- | --- |
| qwen3.5 有服务没面板 | data/config.json 里 capabilities 为空；detect 不认识 Ollama 目录，端口也是 null | detect.py、data/config.json |
| MiniMax-H3 没有面板、启动即跑测试 | capabilities 为空；启动命令被识别成 run-test.sh（它只启动服务并提交一次测试工作流） | data/config.json:21、detect.py |
| qwen-image-2.1 有服务没面板 | 能力置信度 0.4 被关闭；协议是 openai_images，而 ComfyUI 不提供 OpenAI 接口 | data/config.json:167-182 |
| MiniMind-O 对话连不上 | 没有 chat 能力；launch.port 为空，base_url 退化成 127.0.0.1:80；实际是 Gradio 8888，没有 OpenAI 接口 | data/config.json:38-145 |
| 认不出视频模型 | catalog 没有 video_generation，detect 没有 ComfyUI/GGUF unet 规则，也没有 video 面板 | catalog.py、capabilities.ts |

### 10.3 设计决策

- 新增协议 comfyui：通过 ComfyUI HTTP API（/upload/image、/prompt、/history、/view）提交工作流、轮询结果、取回文件。
- 新增能力 video_generation（面板 video），并让 image 面板也走 comfyui 协议。
- 新增 Capability.actions（子功能列表）：每个 action 有自己的 label、fields 和工作流覆盖，工作台据此渲染标签页与表单。这同时解决「小功能」识别问题。
- 识别层新增：Ollama 目录 → chat（原生 /api/chat，端口 11434，num_ctx 来自 Modelfile）；ComfyUI 目录 → 按 models 里的权重名推断 video / image 能力与 action。
- MiniMind-O 改为自带服务：servers/omni_server.py 复用项目 demo 的加载/推理函数，暴露 /health、/voices、/chat；前端用专用 OmniPanel，不嵌入 Gradio。
- 对话生成与页面解耦：新增 ChatJobManager（chatjobs.py），SSE 只做 viewer，切日志页/刷新不取消生成；前端用 sessionStorage + jobId + 快照恢复。
- 思考可关：Ollama 请求显式带 think；ChatPanel 默认 false，开启时 reasoning 折叠，不自动展开。

### 10.4 状态

- [x] 索引改造（AI 速读、部署快照、符号总表、自检命令）
- [x] 后端 comfyui 协议客户端（comfyui.py）
- [x] schema：Capability.actions、video_generation
- [x] detect：Ollama / ComfyUI 识别 + 内置工作流模板（t2v/i2v/flf2v、t2i/edit）
- [x] invoke + app：/video、/video/stop 与 image 的 comfyui 分支
- [x] 前端：ActionPanel、video 面板、作品库视频、Lightbox 视频
- [x] 测试：识别、工作流、GPT-SoVITS、Ollama 原生、文档提取、stop_all 跳过、断线任务恢复，共 36 passed
- [x] qwen-image 编辑/元素分离 action（autogrow image_1 + latent 输出）
- [x] MiniMind-O 接入：omni_server.py + OmniPanel，端到端实测通过（文字流式 + 语音）
- [x] 画质预设：官方最高 + 本地实测最高（视频 1152×640/22、qwen 2048×1152；官方 1344×768/360、2048×2048）
- [x] GPT-SoVITS 实机：3.36 秒 32kHz WAV，参考音色克隆正常
- [x] qwen3.5 原生通道：chat/vision 走 ollama_chat，显式 num_ctx=8192；图片回复“紫色”、文件回复“布洛妮娅”
- [x] 停止确认 + 退出保留：ComfyUI 不再随 ModelDock 退出而停止
- [x] 草稿与预设持久化：ChatPanel 输入存 localStorage；ActionPanel 值与选中预设刷新后恢复
- [x] 参数说明与报错换行：温度/输出长度解释，长错误不再撑破气泡
- [x] 对话任务与页面解耦：ChatJob 后台生成；切到“运行日志”再回来、刷新页面都能恢复答案
- [x] 思考开关：qwen3.5 默认 think=false，直接回答；开启时 reasoning 折叠在“思考过程”，不铺满气泡
- [x] “布洛妮娅”来源确认：D:\ai-tool\voice-file 的 GPT-SoVITS 参考音频字幕 .list，用作文件读取测试，不是人设
- [x] 索引全面校对（2026-10-19）：文件行数、路由行号、符号表、协议/能力数量、契约编号与历史根因标注；36 tests + build
- [ ] 实机验证：等 8189 空闲时跑一次完整 t2v / flf2v（当前本地最高 1152×640/22）

### 10.5 实测记录（2026-10-19）

- 向 8189 提交 T2V 工作流：HTTP 200、node_errors 为空、返回 prompt_id；构建出的 17 节点图中，t2v 模式下 LoadImage 节点被正确删除。
- 轮询约 3 分钟后 /history 仍无记录，随后 8189 进程（PID 37744）消失、端口不可达。用户确认：另一个 agent 当时正在使用 MiniMax-H3 并主动停止了服务，与内存无关。
- data/config.json 已按新识别重写；改造前的备份在 data/config.backup-20261019-130956.json。
- MiniMind-O：omni_server 加载约 3.5 秒；经 ModelDock 的启停与 /omni/chat 端到端验证通过（delta 多段 → status → audio → done），语音为 24kHz WAV。
- 2026-10-19 晚：新增视频/qwen 画质预设、停止确认、退出保留、草稿持久化与参数说明；当时后端 26 个测试通过，前端构建通过（后续增至 36）。
- GPT-SoVITS（D:\ai-tool\...）：runtime、api_v2、v2Pro 预训练权重、webui 均存在。显存释放后通过 ModelDock 启停并实测 /tts：6 秒参考音频 → 3.36 秒 32kHz WAV，peak 0.279 / rms 0.049（非静音），权重扫描 4 个 GPT + 10 个 SoVITS。
- 视频硬件上限（两次实测）：1344×768/49 在采样前 GPU OOM；1152×640/49 重试成功完成 8/8 步采样（约 24 秒/步），随后在 VAE 解码 / 保存阶段 ComfyUI 再次崩溃（Windows access violation）。历史产物证明 1152×640/22 稳定，因此“本地最高”预设仍取 22 帧。
- qwen3.5：切到原生 /api/chat 后，chat/vision 的 protocol 均为 ollama_chat，options.num_ctx=8192。真实图片回复“紫色”，文件文本回复“布洛妮娅”，不再出现 4096 上下文错误；thinking 事件单独走 reasoning。
- 草稿/预设：ChatPanel 文本改存 localStorage；ActionPanel 的字段值与选中预设存 localStorage，刷新后恢复并显示“已选：xxx”。
- 后端测试增至 36 个，前端构建通过。
- 2026-10-19 晚（第二轮）：ChatJob 上线。headless Edge 实测两条链路：①发送长回答→立刻点“运行日志”→返回工作台，答案恢复；②发送后 1.2 秒刷新页面，答案仍恢复并正常收尾（job key 自动清空）。后端 36 个测试通过，前端构建通过。
- qwen think 探针（原生 /api/chat）：think=false 时只有 content；think=true 时 thinking 单独流。9.11/9.9 问题思考 2070 字、24.9 秒、976 tokens，日志 truncated=0——说明“输出思考链”是模型 reasoning 模式，不是上下文超限。
- “布洛妮娅”确认来自 D:\ai-tool\voice-file\bronay1.wav - 副本.list（与 鸭鸭1.wav.list 内容相同）：GPT-SoVITS 参考音频的逐句字幕，用作 qwen 文件读取测试；没有给人设或系统提示词。

---

## 附录 A. 本地部署快照（2026-10-19）

> 这是运行环境的真实状态，不是代码；换机器或换目录后需要重新核对。模型路径可能很大，没有逐个校验权重完整性。
> 2026-10-19 改造后：各模型的 adapter、端口、能力已按实际形态重写；8189 可能被其他 agent 占用，关闭 ModelDock 不会停止它。

| 目录 | 实际形态 | 启动方式 | 端口 | 备注 |
| --- | --- | --- | --- | --- |
| D:\AI\qwen3.5 | Ollama 0.34.0（qwen3.5:9b，Modelfile num_ctx 8192）+ 可选 Open WebUI 3000 | start-ollama.bat | 11434 | adapter=ollama；chat/vision 启用，protocol=ollama_chat；思考默认关 |
| D:\AI\MiniMax-H3 | ComfyUI 便携版 0.37.0 + MiniMax H3 FL2V GGUF | start-comfyui.bat（stop-comfyui.bat） | 8189 | video_generation 启用；t2v/i2v/flf2v；keep_running_on_exit=true；本机最高 1152×640/22 |
| D:\AI\qwen-image-2.1 | ComfyUI 便携版 + Qwen-Image-2.1 GGUF | start-comfyui.bat 或 start-comfyui-2k.bat | 8188 | image_generation/image_edit 启用；本机 2048×1152，2K 需专用脚本 |
| D:\AI\MiniMind-O | 全模态模型 minimind-3o（113M）+ Mimi/SenseVoice/SigLIP | ModelDock 的 omni_server.py（项目自带 .venv） | 8888 | chat/vision/audio_input/speech_output；Gradio 仅作备用 |
| D:\ai-tool\GPT-SoVITS-v2pro-20250604 | GPT-SoVITS v2Pro 整合包（runtime + v2Pro 预训练权重） | ModelDock 启动 api_v2.py | 9880（WebUI 9874） | tts/voice_clone 启用；启动需要至少 2~3GB 空闲显存 |

### A.1 MiniMax-H3 的模型与工作流

- 权重（ComfyUI/ComfyUI/models）：
  - unet/minimax_h3_fl2va_pruned-Q4_K.gguf（10.9 GB）、Q2_K.gguf（6.4 GB）
  - text_encoders/qwen3vl_32b_minimax_h3-Q2_K_M.gguf（12.5 GB）
  - vae/minimax_h3_video_vae_fp16.safetensors（5.0 GB）、minimax_h3_audio_vae_fp32.safetensors（577 MB）
  - loras/minimax_h3_fl2v_turbo_8step...、..._4step_768p...
- 工作流模板：D:\AI\MiniMax-H3\tools\test-prompt.json（16 节点，含音频 VAE 与 CreateVideo(24fps)）。
- 关键节点 MiniMaxH3ImageToVideo 的输入（来自运行中的 /object_info）：
  - 必填：clip、vae、prompt、width（默认 1344）、height（默认 768）、length（默认 124，范围 5~3600）
  - 可选：first_frame、last_frame ← 这就是首尾帧生视频的接入点
- 提交方式：POST /prompt，外层必须是 {"prompt": <工作流图>}；结果在 /history/{id} 的 outputs.videos/gifs/images。

### A.2 qwen-image-2.1 的模型与工作流

- 权重：diffusion_models/qwen-image-2.1-Q5_K_M.gguf（4.9 GB）、text_encoders/qwen3vl_8b_w4a8.safetensors（6.0 GB）、vae/qwen_image_2.1_vae_bf16.safetensors（644 MB）、upscale_models/4x-UltraSharp.pth。
- 已验证工作流：D:\AI\qwen-image-2.1\verify_wf.json（文生图）、tools/test-2k-prompt.json（2K，EmptyLatentImage）。
- 关键节点 TextEncodeQwenImage21：clip、prompt、negative_prompt、resolution、images（动态图像列表）；图像编辑/元素分离要靠 images 输入。
- 注意：ComfyUI 没有 OpenAI 兼容层，openai_images 协议一定失败。

### A.3 ComfyUI API 速查（实现 comfyui 协议时用）

| 用途 | 请求 |
| --- | --- |
| 健康检查 | GET /system_stats |
| 上传图片 | POST /upload/image（multipart: image、overwrite=true）→ 返回 name/subfolder/type |
| 提交工作流 | POST /prompt，body {"prompt": {...}, "client_id": "..."} → prompt_id |
| 查询结果 | GET /history/{prompt_id} → {prompt_id: {status:{completed,status_str}, outputs:{node:{images/videos/gifs/audio:[{filename,subfolder,type}]}}}} |
| 下载文件 | GET /view?filename=&subfolder=&type= |
| 中断 | POST /interrupt |
| 节点目录 | GET /object_info |

---

## 附录 B. 符号总表（手工校对）

> 只列顶层定义 / 导出符号，格式为 名称:行号。行号以当前文件为准；改到对应文件时顺手校正，不要凭记忆。Python 可用 ast 提取，前端可用 rg '^export (function|const|interface|type)' 辅助。

### backend/

| 文件 | 符号（行号） |
| --- | --- |
| backend/demo.py | ROOT:17、write:21、make_folders:31、MOCK_KIND:57、DESCRIPTIONS:59、main:64 |
| backend/modeldock/app.py | get_store:31、python_default:35、pm:40、chat_jobs:41、reload_plugins:45、main_spec:50、sub_specs:57、sync_services:70、model_view:76、free_port:87、merge_runtime:95、on_running:109、lifespan:130、_invoke_error:152、must:156、health:164、catalog:169、plugins_reload:174、system_stats:180、get_settings:187、put_settings:193、list_models:199、get_model:204、needs_port:208、create_model:213、update_model:225、delete_model:237、detect_path:249、detect_batch:254、rescan:271、probe_url:290、start_model:295、stop_model:304、restart_model:311、service_action:318、get_logs:332、clear_logs:338、services:346、browse:352、open_folder:381、omni_chat:401、omni_voices:412、chat:417、chat_job:427、cancel_chat_job:435、image:443、video:448、video_stop:453、tts:458、asr:463、embed:468、custom:473、sovits_weights:482、sovits_set:487、extract_document:493、upload:504、list_outputs:510、delete_output:515、serve_file:520、events:532 |
| backend/modeldock/chatjobs.py | MAX_JOBS:18、JOB_TTL:19、sse:22、_decode:26、ChatJob:39、ChatJobManager:84 |
| backend/modeldock/catalog.py | CAPABILITIES:13、PROTOCOLS:42、ADAPTERS:56、default_binding:72、catalog_payload:77 |
| backend/modeldock/comfyui.py | PLACEHOLDER:38、OUTPUT_KINDS:39、REQUEST_TIMEOUT:45、ComfyError:48、base_of:54、pick_action:59、_substitute:71、_drop_node:84、build_graph:99、coerce_values:114、action_fields:129、graph_for:133、_upload:143、_fetch:160、_error_from:172、run:189、interrupt:289 |
| backend/modeldock/detect.py | ENABLE_THRESHOLD:31、SOURCE_RANK:32、SKIP_DIRS:33、SCRIPT_EXT:37、MAX_ENTRIES:38、MAX_DEPTH:39、cap:43、_cap_key:54、merge_caps:58、read_json:87、read_text:95、which:105、has_module:109、Scan:117、scan:142、local_python:165、q:179、KEYWORDS:184、OMNI_KEYWORDS:197、NAME_RULES:198、COMPONENT_DIR_RULES:207、CONFIG_KEY_RULES:212、readme_hints:219、name_hints:236、component_hints:246、config_key_hints:256、PORT_PATTERNS:276、sniff_port:284、ServerScript:295、sniff_server_scripts:302、find_scripts:348、script_command:354、COMFY_MODEL_DIRS:364、find_comfy_dir:367、comfy_models:374、_matching:386、_one:395、_best_unet:402、_num_field:416、VIDEO_HINTS:420、_video_presets:426、_qwen_image_presets:439、_qwen_edit_presets:452、_minimax_workflow:460、_qwen_workflow:506、_qwen_edit_workflow:531、detect_comfyui:557、detect_ollama:677、Draft:729、detect_gpt_sovits:741、detect_diffusers:775、detect_gguf:801、analyze_hf_config:836、detect_hf:867、detect_minimind:918、detect_script_project:966、apply_manifest:1014、prettify:1042、detect:1047 |
| backend/modeldock/documents.py | MAX_CHARS:15、TEXT_SUFFIXES:16、DocumentError:24、_decode_text:28、_pdf_text:37、_docx_text:46、extract:61 |
| backend/modeldock/invoke.py | TIMEOUT:27、InvokeError:30、client:36、find_cap:40、base_of:48、served_name:54、headers_for:58、data_url_to_bytes:63、pcm16_to_wav:70、sniff_audio:80、raise_for:92、build_messages:102、sse:125、_strip_data_url:129、build_ollama_messages:135、_ollama_error_message:149、ollama_chat_stream:169、chat_stream:223、fetch_image_bytes:325、image_generate:338、omni_chat_stream:390、omni_voices:417、video_generate:432、video_interrupt:445、SOVITS_LANG:456、tts:460、transcribe:518、embed:547、sovits_weights:575、sovits_set_weights:591、_render:611、_pluck:624、custom_call:635 |
| backend/modeldock/outputs.py | EXT_BY_MIME:17、outputs_dir:23、uploads_dir:27、_index_path:31、list_outputs:35、_write_index:45、save_output:49、delete_output:71、save_upload:85、resolve_upload:93、safe_child:108 |
| backend/modeldock/paths.py | PACKAGE_DIR:8、BACKEND_DIR:9、PROJECT_DIR:10、SERVERS_DIR:11、FRONTEND_DIST:12、PLUGINS_DIR:13、data_dir:16、sub_dir:26 |
| backend/modeldock/plugins.py | load_plugins:21 |
| backend/modeldock/probe.py | ROUTE_CAPS:18、VISION_NAME:26、AUDIO_NAME:27、_ollama_show:30、probe:64 |
| backend/modeldock/process.py | ANSI:28、IS_WIN:29、ServiceSpec:33、Service:50、expand:79、port_open:87、kill_tree:96、pid_on_port:118、decode:128、ProcessManager:135 |
| backend/modeldock/schemas.py | new_id:12、Capability:19、LaunchConfig:47、ModelConfig:64、Settings:89、DetectRequest:98、DetectResult:102、ProbeResult:116 |
| backend/modeldock/store.py | Store:16 |
| backend/modeldock/system.py | gpus:17、stats:37 |
| backend/servers/diffusers_server.py | run:50、health:74、generations:79、edits:86 |
| backend/servers/hf_chat_server.py | TRUST:39、NAME:40、load_image:78、prepare:86、health:112、models:117、chat:122 |
| backend/servers/mock_server.py | KIND:36、tone:41、png:55 |
| backend/servers/omni_server.py | REPO:45、SCRIPTS:46、sse:124、wav_base64:128、decode_audio:140、decode_image:150、health:158、voices:170、chat:179 |
| backend/tests/conftest.py | ROOT:9、free_port:13、data_dir:20、write:25、models_root:36 |
| backend/tests/test_api.py | client:17、wait_status:26、test_full_lifecycle:38、test_gpt_sovits_flow:101、test_failed_start_reports_error:120、test_detect_endpoint_and_fs:135、test_detect_batch:142、test_probe_generic_server_is_conservative:149、test_probe_single_route_server:166、test_chat_job_keeps_generating_after_the_viewer_disconnects:182 |
| backend/tests/test_chatjobs.py | test_job_continues_when_viewer_disconnects:10、test_cancel_stops_a_running_job:40、test_finished_job_can_be_read_after_the_fact:66 |
| backend/tests/test_comfyui.py | test_build_graph_substitutes_typed_values:4、test_build_graph_drops_empty_optional_nodes:15、test_build_graph_removes_nested_references:29 |
| backend/tests/test_detect.py | caps:7、test_minimax_is_text_llm:11、test_minimind_o_is_omni:18、test_qwen35_has_vision:30、test_qwen_image_is_diffusers:37、test_gpt_sovits:44、test_manifest_overrides:54、test_single_weak_hint_stays_disabled:67、test_merge_caps_preserves_actions:78、test_plugin_detector:85、test_url_and_missing:93、test_comfyui_video_detection:98、test_comfyui_image_detection:127、test_minimind_omni_detection:152、test_ollama_detection:169 |
| backend/tests/test_documents.py | test_extract_plain_text:9、test_extract_docx:15、test_reject_binary:25 |
| backend/tests/test_ollama_chat.py | Handler:11、test_ollama_native_chat_sends_images_and_num_ctx:35、test_context_error_is_explained:64 |
| backend/tests/test_probe_ollama.py | Handler:10、test_probe_reads_ollama_capabilities:39 |
| backend/tests/test_process.py | test_stop_all_skips_keep_running_models:6 |
| backend/tests/test_sovits.py | test_gpt_sovits_tts_posts_reference_audio:12 |

### frontend/src/lib/

| 文件 | 符号（行号） |
| --- | --- |
| frontend/src/lib/api.ts | ApiError:1、api:38、upload:45、extractDocument:53、streamSSE:60 |
| frontend/src/lib/audio.ts | encodeWav:4、toWav:30、blobToDataUrl:47、useRecorder:56 |
| frontend/src/lib/capabilities.ts | PANELS:4、enabledCaps:15、has:19、panelsFor:23、TASKS:29、confidenceLabel:37 |
| frontend/src/lib/format.ts | bytes:1、duration:13、timeAgo:22、clock:30、greeting:34、modelColor:45、initials:52 |
| frontend/src/lib/store.tsx | StoreProvider:37、useStore:194、useLogs:200 |
| frontend/src/lib/types.ts | Status:1、Binding:3、CustomField:15、ActionField:24、ActionPreset:36、ActionSpec:44、Capability:53、LaunchConfig:65、Runtime:79、Model:91、DetectResult:110、CapabilityInfo:125、Catalog:133、OutputItem:140、LogLine:154、Settings:159、SystemStats:171 |

### frontend/src/components/

| 文件 | 符号（行号） |
| --- | --- |
| frontend/src/components/AddModelDialog.tsx | AddModelDialog:19 |
| frontend/src/components/CapabilityEditor.tsx | CapabilityEditor:9 |
| frontend/src/components/FileBrowser.tsx | FileBrowser:13 |
| frontend/src/components/LogViewer.tsx | LogViewer:12 |
| frontend/src/components/Markdown.tsx | renderMarkdown:16、Markdown:45、splitThink:51 |
| frontend/src/components/Sidebar.tsx | Sidebar:5 |
| frontend/src/components/ui.tsx | CAP_ICONS:10、PANEL_ICONS:26、CapIcon:37、CapChip:42、StatusBadge:60、PowerButton:70、ModelAvatar:93、Modal:102、Toasts:127、Switch:141、Empty:150、Field:161、Slider:171 |

### frontend/src/pages/

| 文件 | 符号（行号） |
| --- | --- |
| frontend/src/pages/Gallery.tsx | Gallery:10 |
| frontend/src/pages/Logs.tsx | Logs:7 |
| frontend/src/pages/ModelDetail.tsx | ModelDetail:27 |
| frontend/src/pages/Models.tsx | Models:18 |
| frontend/src/pages/Overview.tsx | ModelCard:14、Overview:55 |
| frontend/src/pages/Settings.tsx | Settings:31 |
| frontend/src/pages/Studio.tsx | Studio:17 |

### frontend/src/panels/

| 文件 | 符号（行号） |
| --- | --- |
| frontend/src/panels/ActionPanel.tsx | ActionPanel:59 |
| frontend/src/panels/AudioPanels.tsx | TtsPanel:78、AsrPanel:248 |
| frontend/src/panels/ChatPanel.tsx | ChatPanel:137（辅助：loadActiveJob:122、newJobId:50、jobMeta:54、applyJob:64） |
| frontend/src/panels/CustomPanel.tsx | CustomPanel:9 |
| frontend/src/panels/EmbeddingPanel.tsx | EmbeddingPanel:7 |
| frontend/src/panels/ImagePanel.tsx | Lightbox:18、ImagePanel:50 |
| frontend/src/panels/OmniPanel.tsx | OmniPanel:52 |
| frontend/src/panels/WebuiPanel.tsx | WebuiPanel:8 |

### 其他

| 文件 | 符号（行号） |
| --- | --- |
| frontend/src/App.tsx | App:12 |
| plugins/_example_plugin.py | NAME:9、DESCRIPTION:10、detect:13 |
| run.py | ROOT:21、ensure_frontend:25、main:40 |

---

## 附录 C. 自检命令（Windows PowerShell）

~~~powershell
# 端口与进程
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 8188,8189,8888,11434,3000,7777 } |
  Select-Object LocalPort,OwningProcess

# ComfyUI 是否就绪
Invoke-RestMethod http://127.0.0.1:8189/system_stats | ConvertTo-Json -Depth 3

# ComfyUI 里有没有某个节点及其输入
$oi = Invoke-RestMethod http://127.0.0.1:8189/object_info
$oi.MiniMaxH3ImageToVideo.input.optional

# Ollama 模型列表 / 原生接口（对话走 /api/chat）
Invoke-RestMethod http://127.0.0.1:11434/api/tags | ConvertTo-Json -Depth 4

# ChatJob 是否还在生成（把 <id> 换成 sessionStorage 里的任务 id）
Invoke-RestMethod http://127.0.0.1:7777/api/chat/jobs/<id> | ConvertTo-Json -Depth 5

# MiniMind-O omni 服务（ModelDock 启动后）
Invoke-RestMethod http://127.0.0.1:8888/health | ConvertTo-Json -Depth 4
Invoke-RestMethod http://127.0.0.1:8888/voices | ConvertTo-Json -Depth 4

# 后端测试 / 前端构建
cd backend; ..\.venv\Scripts\python.exe -m pytest -q
cd ..\frontend; npm run build
~~~
