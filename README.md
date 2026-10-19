# ModelDock · 本地模型管理器

把散落在硬盘里的本地大模型集中管理：**填写路径 → 自动识别能力 → 一键启动 / 关闭 → 在统一的工作台里使用全部功能**。

- **一键启停**：不再手动运行启动脚本和停止脚本。ModelDock 管理整个进程树，实时显示加载日志，自动检测"就绪"，退出时可自动释放显存；你手动启动的服务也会被识别并接管。
- **自动识别能力**：分析模型文件夹（`config.json`、diffusers 管线、GGUF、GPT-SoVITS 目录结构、服务脚本源码、README、组件目录），推断它能做什么、该怎么启动；模型启动后再探测其 API，补全/确认能力。每项能力都附带"识别依据"和置信度，可随时手动开关。
- **统一工作台**：按能力自动生成操作界面——对话（含看图、语音输入、语音回答、思考过程）、文生图 / 图像编辑、语音合成 / 声音克隆（含 GPT-SoVITS 参考音频、常用音色、权重切换）、语音识别、文本向量、模型自带网页界面、自定义 HTTP 接口。
- **可扩展**：`modeldock.json` 清单、`plugins/*.py` 插件、自定义 HTTP 模板三种方式接入新模型。
- **多页面**：总览、模型库、模型详情、工作台、作品库、运行日志、设置；浅色 / 深色主题。

## 快速开始

需要 Python 3.10+；首次运行还需要 Node.js 18+ 来构建界面（只构建一次）。

```bash
# Windows：双击 start.bat
# macOS / Linux：
./start.sh
```

启动后浏览器会打开 `http://127.0.0.1:7777`。想用独立窗口：`pip install pywebview` 后运行 `start.bat --window`。

> ModelDock 本身很轻，只负责管理与调用；每个模型继续使用它自己的 Python 环境。在「设置 → 默认 Python 解释器」或模型的「启动配置 → Python 解释器」中指定装好 torch 等依赖的环境即可。

### 先体验（无需显卡）

```bash
python backend/demo.py                       # 生成 5 个与真实目录结构一致的示例模型
MODELDOCK_DATA=demo-data python run.py       # Windows: set MODELDOCK_DATA=demo-data && python run.py
```

示例模型由内置的模拟服务驱动，可以完整体验启动、对话、生图、声音克隆等流程。

## 添加你的模型

在「模型库 → 添加模型」中填写文件夹路径（或点"浏览"），也可以选择**批量扫描**整个模型目录。以你现有的模型为例，ModelDock 的识别结果是：

| 模型 | 识别为 | 自动添加的能力 | 启动方式 |
| --- | --- | --- | --- |
| MiniMax-H3 | ComfyUI 视频模型 | 视频生成（文生视频 / 图生视频 / 首尾帧过渡） | 项目自带的 `start-comfyui.bat` |
| MiniMind-O | MiniMind 全模态项目 | 文本对话、图像理解、语音输入、语音输出 | ModelDock 的 `omni_server.py`（项目自带 Python） |
| qwen3.5 | Transformers 语言模型 | 文本对话、图像理解 | 同上 |
| qwen-image-2.1 | ComfyUI 图像模型 | 文生图、图像编辑 / 元素分离 | 项目自带的 `start-comfyui.bat` |
| GPT-SoVITS-v2pro-20250604 | GPT-SoVITS 语音 | 语音合成、声音克隆、原生训练界面 | 整合包自带 `runtime/python` + `api_v2.py` |

识别结果在添加前可以修改；之后也能在模型详情页随时调整。实际识别取决于你硬盘上的真实文件，如果某项判断不对，手动开关后会被"锁定"，重新识别时不会被覆盖。

### 启动命令占位符

| 占位符 | 含义 |
| --- | --- |
| `{python}` | 模型的 Python 解释器（模型设置 → 全局设置 → ModelDock 自身） |
| `{port}` / `{host}` | 服务端口 / 监听地址（端口留空时自动分配） |
| `{servers}` | 内置推理服务目录（`hf_chat_server.py`、`diffusers_server.py`） |

也可以直接填写 `.bat` / `.sh` 启动脚本路径；如有专门的停止脚本，填在"停止命令"里。

### 视频、ComfyUI 与子功能

- **ComfyUI 项目**：识别 `start-comfyui.bat`、端口和 `models/` 里的权重名，自动生成内置工作流，通过 ComfyUI 的 `/prompt` 接口执行，结果自动进入作品库。
- **MiniMax-H3**：视频面板提供文生视频、图生视频、首尾帧生视频；首尾帧会先上传到 ComfyUI 的 input 目录。
- **qwen-image-2.1**：图像面板提供文生图与图像编辑 / 元素分离；编辑时参考图作为 `image_1` 送入文本编码器。
- **Ollama**：包含 `Modelfile` 或 `start-ollama.bat` 的目录会被识别为 OpenAI 兼容服务（默认 11434），工作台直接对话。
- **MiniMind-O**：不再嵌入 Gradio。ModelDock 自带 `omni_server.py`，复用项目 demo 的加载与推理代码，提供专用页面：文字 / 图片 / 录音输入，流式文字 + 语音回复，可直接选内置音色。
- **子功能（actions）**：一个能力可以带多个子功能，各自有字段与工作流。工作台会按 `actions` 生成标签页和表单；工作流保存在能力的 `binding.workflow` 里，可在「模型详情 → 高级 → 能力 JSON」中调整，换权重后点一次「重新识别」重建。

## 扩展

### 1. `modeldock.json` 清单（最准确）

在模型文件夹里放一个清单，ModelDock 会优先采用：

```json
{
  "name": "MiniMind-O",
  "launch": { "command": "{python} scripts/serve_openai_api.py --port {port}", "health_path": "/v1/models" },
  "capabilities": ["chat", "vision", "audio_input", "speech_output"],
  "defaults": { "model": "minimind-o" }
}
```

可用能力：`chat` `vision` `audio_input` `speech_output` `asr` `tts` `voice_clone` `image_generation` `image_edit` `video_generation` `embedding` `webui` `custom`。

### 2. 插件

在 `plugins/` 下放一个 Python 文件，实现 `detect(root, scan)`，即可教 ModelDock 识别新的模型类型。参考 [`plugins/_example_plugin.py`](plugins/_example_plugin.py)（下划线开头的文件不会加载）。

### 3. 自定义 HTTP 接口

任何 HTTP 接口都可以在「模型详情 → 高级 → 能力 JSON」中声明为 `custom_http` 能力，工作台会根据 `fields` 自动生成表单：

```json
{
  "type": "custom", "label": "超分辨率", "enabled": true,
  "binding": {
    "protocol": "custom_http", "method": "POST", "path": "/upscale",
    "body": { "image": "{{image}}", "scale": "{{scale}}" },
    "fields": [ { "name": "image", "type": "file", "as": "base64" }, { "name": "scale", "type": "number", "default": 2 } ],
    "extract": { "image_b64": "data.image" }
  }
}
```

## 项目结构

```
run.py                 启动器（自动构建前端、打开浏览器 / 窗口）
backend/modeldock/     FastAPI 后端
  detect.py            文件夹分析与能力推断
  probe.py             运行时 API 探测
  comfyui.py           ComfyUI 工作流客户端（提交 / 轮询 / 取回生成物）
  process.py           进程管理、就绪检测、日志
  invoke.py            统一调用代理（OpenAI 兼容 / GPT-SoVITS / ComfyUI / 自定义 HTTP）
backend/servers/       内置推理服务与模拟服务（omni_server.py：MiniMind-O 文字/图片/语音）
frontend/              React + TypeScript 界面
plugins/               识别插件
data/                  配置、日志、上传、作品（运行时生成）
```

## 开发

```bash
pip install -r requirements.txt pytest
cd backend && python -m pytest          # 后端测试
cd frontend && npm install && npm run dev  # 界面热更新（代理到 127.0.0.1:7777）
```
