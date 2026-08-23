---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '55807414-5df7-47b3-a428-7981ba74ea06'
  PropagateID: '55807414-5df7-47b3-a428-7981ba74ea06'
  ReservedCode1: '065b4b18-051d-47a6-88e4-b3a2baf0c01b'
  ReservedCode2: '065b4b18-051d-47a6-88e4-b3a2baf0c01b'
---

# 🏭 本地 AI 工厂

> **v2.1.0** · 作者：李准的星小辰 · 2026-08-24

基于 MacBook Pro M5 Max / 137GB 搭建的**全本地多模态 AI 环境**。所有模型在本地运行，数据不出设备。提供 Streamlit WebUI，覆盖文本、视觉、语音、生成全链路。

## 📁 项目结构

```
local-ai-factory/
├── webui.py                  # 主程序（Streamlit WebUI，13个功能页面）
├── rag_sync.py               # RAGFlow 知识库增量同步脚本
├── router_config.yaml        # 智能路由配置
├── router-server.py          # OpenAI 兼容代理服务器
├── router.py                 # 智能路由核心逻辑
├── smart_router.py           # 策略引擎
├── ai-idle-watchdog.py       # GPU 空闲看门狗（自动卸载模型）
├── lifecycle.py              # 模型生命周期管理
├── generate.py               # 图片生成 CLI（ComfyUI API）
├── generate_video.py         # 视频生成 CLI（ComfyUI API）
├── mlx-server.sh             # MLX LLM 服务脚本
├── mlx-chat.py               # 文本对话 CLI
├── mlx-benchmark.py          # 性能测试工具
├── evaluator.py              # 模型评测框架
├── eval_dashboard.py         # 评测看板
├── start_all.sh / stop_all.sh  # 服务启停脚本
├── status.sh                 # 服务状态检查
├── ragflow-docker/           # RAGFlow Docker 部署
├── AI工厂项目介绍长图.png     # 项目介绍长图
├── README.md / CHANGELOG.md  # 文档
└── VERSION                   # 版本号
```

## 🚀 快速开始

### 1. 启动 WebUI

```bash
python3 -m streamlit run webui.py --server.port 8501
```

浏览器打开 http://localhost:8501 ，通过侧边栏导航各功能页面。

### 2. 一键启停所有服务

```bash
./start_all.sh    # 启动 LLM + ComfyUI + Ollama
./stop_all.sh     # 停止所有服务
./status.sh       # 查看运行状态
```

### 3. 启动 LLM 常驻服务

```bash
./mlx-server.sh start   # Qwen3.8-27B-4bit @ 端口 8082
./mlx-server.sh test    # 测试 API
```

## 📊 服务地址

| 服务 | 端口 | 说明 |
|------|------|------|
| AI 工厂 WebUI | 8501 | 主界面，Streamlit，13 个功能页面 |
| LLM 常驻服务 | 8082 | Qwen3.8-27B-4bit（launchd 托管） |
| OpenAI 兼容代理 | 8088 | 多模型统一入口，智能路由 |
| ComfyUI | 8188 | 图片/视频生成 |
| RAGFlow | 9380 / 8086 | 知识库问答（API / 代理） |
| Ollama | 11434 | gemma4:12b + bge-large-zh |

## 🧩 功能模块（13 个页面）

| 模块 | 功能 | 技术栈 |
|------|------|--------|
| 📊 系统监控 | CPU/内存/磁盘/GPU 实时监控，服务状态，模型进程 | Streamlit + psutil + Plotly |
| 🧠 文本对话 | 多模型对话，支持上下文，Token 统计 | mlx-lm / Ollama |
| 🔬 模型对比 | 同一 prompt 并行调用多个模型，横向对比 | MLX 多端口并发 |
| 👁️ 图片理解 | 图片上传 + AI 描述/OCR/问答 | Qwen3.8-27B-4bit（多模态） |
| 🎬 视频理解 | 视频抽帧 + AI 分析 | Qwen3.8-27B-4bit |
| 🎨 图片生成 | 文生图，1024×1024 | ComfyUI + SDXL / SANA |
| 🎬 视频生成 | 文生视频，5 秒 24fps | ComfyUI + MiniMax H3 4-bit |
| 🎤 语音识别 | 音频转文字，多人说话分离 | SenseVoiceSmall / Seaco-Paraformer + cam++ |
| 🔊 语音合成 | 文字转语音，9 种音色，声音克隆 | Qwen3-TTS-0.6B + edge-tts |
| 📚 智能问答 | 知识库问答 + 全文搜索 | RAGFlow + FTS5 本地索引 |
| 📈 Token 统计 | 各模型使用量/费用统计 | 内置 Token 计数器 |
| 📋 日志查看 | 操作日志 + 模型服务日志 | /tmp/ai-factory-activity.log |
| 📖 AI工厂说明 | 项目文档 | Markdown 内嵌 |

## 🤖 模型清单

### 本地模型

#### 文本大模型 (LLM)

| 模型 | 大小 | 速度 | 用途 |
|------|------|------|------|
| Qwen3.8-27B-4bit | 15 GB | ~31.5 tps | 日常主力（常驻 8082），多模态兼视觉理解 |
| Qwen3.6-35B-A3B-bf16 | 65 GB | ~30.8 tps | MoE 通用对话/推理，激活 3B |
| gemma4:12b (Ollama) | 7.6 GB | ~56.1 tps | 最快响应，短消息/翻译 |

#### 图像 / 视频生成

| 模型 | 大小 | 指标 | 用途 |
|------|------|------|------|
| SDXL Base 1.0 | ~7 GB | 1024×1024 / 20步 | 文生图 |
| SANA 1.5 1.6B | ~3 GB | 1024×1024 / FP32 | 文生图 |
| MiniMax H3 4-bit | ~5 GB | 864×480 / 5秒24fps | 文生视频（带同步音频） |

#### 语音 (ASR / TTS)

| 模型 | 类型 | 指标 | 用途 |
|------|------|------|------|
| Qwen3-TTS-0.6B | TTS | 9音色 / 声音克隆 | 语音合成 |
| SenseVoiceSmall | ASR | 0.78s CPU | 极速语音识别 |
| Seaco-Paraformer | ASR | 说话人分离 | 长音频识别 + 多人分离 |

#### 知识库与嵌入

| 模型 | 指标 | 用途 |
|------|------|------|
| RAGFlow v0.27.0 | 459 文档 | 知识库问答（bge-large-zh 向量化） |
| bge-large-zh (Ollama) | 1024 维 | 向量嵌入 |
| FTS5 本地索引 | 454 文件 / 53MB | 全文搜索 (SQLite FTS5) |

### 云端 / 远程模型（降级备选 & 专有能力）

| 模型 | 来源 | 用途 |
|------|------|------|
| Qwen3.6-27B (vLLM) | 武林提供 106.0.4.142:51211 | 远程 LLM 降级备选 |
| 星辰慧记 (云端 ASR) | OpenAPI | 长音频语音识别 + 会议纪要 |
| edge-tts (云端) | 微软免费 | 云端 TTS 备选 |

## 🔀 智能路由

统一入口 `http://localhost:8082`（常驻）/ `http://localhost:8088`（代理），OpenAI 兼容 API：

| 规则 | 首选模型 | 降级链 |
|------|----------|--------|
| 图片理解 | Qwen3.8-27B | → Qwen3.6-35B |
| 代码任务 | Qwen3.8-27B | → 远程 Qwen3.6 |
| 推理任务 | Qwen3.6-35B-MoE | → Qwen3.8 → 远程 |
| 短消息 (≤200 token) | gemma4:12b | → Qwen3.8 |
| 长文本 (≥4000 token) | Qwen3.8-27B | → Qwen3.6-35B |
| 默认兜底 | Qwen3.8-27B | → 远程 → Qwen3.6-35B |

## 🔒 数据安全

- 所有模型推理在本地完成，**数据不上传**
- 知识库文档（RAGFlow + FTS5）本地存储，每日 9:30 自动增量同步
- 操作日志记录在 `/tmp/ai-factory-activity.log`
- 远程 LLM (vLLM 106.0.4.142) 仅作为降级备选

## 📋 版本历史

| 版本 | 日期 | 要点 |
|------|------|------|
| v2.1.0 | 2026-08-24 | WebUI 增强（说明页+操作日志）、RAGFlow 同步、长图、FTS5 优化 |
| v2.0.0 | 2026-08-22 | 基准测试全面更新，SDXL/SANA/MiniMax H3/SenseVoice 等新模型 |
| v1.0.0 | 2026-06-01 | 首个正式版本 |

详见 [CHANGELOG.md](CHANGELOG.md)。