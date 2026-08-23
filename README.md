---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'cb8e88cc-520e-4b02-876e-50553dd33a74'
  PropagateID: 'cb8e88cc-520e-4b02-876e-50553dd33a74'
  ReservedCode1: '63be37f9-6d3f-4409-b2d9-c8f27e935c48'
  ReservedCode2: '63be37f9-6d3f-4409-b2d9-c8f27e935c48'
---

# 🚀 本地 AI 工厂

基于 MacBook Pro M5 Max / 137GB 的本地多模态 AI 环境，所有模型本地运行，无需联网。

> 作者：李准的星小辰

## 📁 目录结构

```
local-ai-factory/
├── generate.py                    # 图片生成 CLI（ComfyUI API）
├── generate_video.py              # 视频生成 CLI（ComfyUI API）
├── mlx-server.sh                  # MLX LLM 服务启动脚本
├── mlx-chat.py                    # 文本对话 CLI
├── mlx-benchmark.py               # 性能测试工具
├── start_all.sh                   # 一键启动所有服务
├── stop_all.sh                    # 一键停止所有服务
├── status.sh                      # 检查服务状态
├── flux_schnell_workflow.json     # Flux Schnell 工作流
├── flux_dev_workflow.json         # Flux Dev 工作流
├── output/
│   ├── image/                     # 生成的图片
│   ├── video/                     # 生成的视频
│   └── audio/                     # 生成的音频/文字
└── README.md                      # 本文档
```

## 🚀 快速开始

### 1. 启动所有服务

```bash
./start_all.sh
```

### 2. 生成图片（FLUX via ComfyUI）

```bash
# 快速生成（4步）
python3 generate.py schnell "a cute cat wearing a space suit"

# 高质量生成（25步）
python3 generate.py dev "a beautiful sunset over mountains, photorealistic, 8k"
```

### 3. 生成视频（CogVideoX via ComfyUI）

```bash
python3 generate_video.py "A cat walking on the moon, cinematic"
```

### 4. 文本对话（mlx-lm）

```bash
# 交互聊天
mlx_lm chat --model ~/.omlx/models/Qwen3.6-35B-A3B-bf16

# 单次生成
mlx_lm generate --model ~/.omlx/models/Qwen3.6-35B-A3B-bf16 --prompt "你好"

# 通过 API 服务
./mlx-server.sh start
python3 mlx-chat.py
```

### 5. 语音识别（Whisper）

```bash
whisper audio.mp3 --model base --language zh --output_format txt
```

### 6. 语音合成（edge-tts）

```bash
edge-tts --voice zh-CN-XiaoxiaoNeural --text "你好世界" --write-media output.mp3
```

## 🔧 服务管理

```bash
./status.sh          # 查看状态
./start_all.sh       # 启动所有服务
./stop_all.sh        # 停止所有服务
./mlx-server.sh test # 测试 LLM API
```

## 📊 服务地址

| 服务 | 端口 | 地址 | 说明 |
|------|------|------|------|
| 路由平台 | 8606 | http://localhost:8606 | 统一 API 入口，OpenAI 兼容 |
| WebUI | 8501 | http://localhost:8501 | 管理面板（对话/图片/视频/语音） |
| 评测看板 | 8607 | http://localhost:8607 | 模型评测排行榜 |
| LLM 常驻 | 8082 | http://localhost:8082 | Qwen3.8-27B-4bit (launchd 托管) |
| LLM 按需 | 8084 | http://localhost:8084 | Qwen3.6-35B-A3B-bf16 |
| LLM 按需 | 8085 | http://localhost:8085 | Qwen3.8-27B-4bit (Lifecycle 管理) |
| LLM 按需 | 8086 | http://localhost:8086 | TeleChat3-36B-Thinking-4bit |
| VLM | 8081 | http://localhost:8081 | Qwen2.5-VL-32B 图片理解 |
| ComfyUI | 8189 | http://localhost:8189 | 图片/视频生成 |
| Ollama | 11434 | http://localhost:11434 | gemma4:12b + bge-large-zh |
| 远程 LLM | — | http://106.0.4.142:51211 | Qwen3.6-27B（武林提供） |

## 🎯 模型列表

### LLM 文本模型（mlx-lm）

| 模型 | 大小 | 速度 | 用途 | 状态 |
|------|------|------|------|------|
| Qwen3.6-35B-A3B-bf16 | 65 GB | ~30.8 tps | 通用对话/推理/数学，MoE总35B激活3B | ✅ 启用 |
| Qwen3.8-27B-4bit | 15 GB | ~31.5 tps | 轻量主力，默认首选，128K上下文 | ✅ 启用(常驻+按需) |
| TeleChat3-36B-Thinking-4bit | 19 GB | — | 电信星辰自研，思维链推理，中文好 | ✅ 启用 |
| gemma4:12b (Ollama) | 7.6 GB | ~56.1 tps | 最快响应，短消息闲聊/翻译 | ✅ 启用 |
| Qwen3.6-27B (远程) | — | — | 武林提供，降级不占本地内存 | ✅ 启用 |
| Qwen2.5-72B-Instruct-4bit | 38 GB | ~4 tps | 最高质量但速度慢 | ❌ 已删除 |
| gemma-4-26b-a4b-it-bf16 | 48 GB | — | MoE 模型 | ❌ 已删除 |
| gemma-4-31b-it-bf16 | 58 GB | — | Dense 模型 | ❌ 已删除 |
| gemma-4-12B-8bit (MLX) | — | — | MLX 下中文不可用 | ❌ 禁用 |

### 视觉语言模型

| 模型 | 大小 | 速度 | 用途 |
|------|------|------|------|
| Qwen2.5-VL-32B-Instruct-4bit | 18 GB | 22.91s/图 | 图片理解/OCR |

### 图像生成（ComfyUI）

| 模型 | 大小 | 分辨率 | 用途 |
|------|------|--------|------|
| SDXL Base 1.0 | ~7 GB | 1024×1024 | 文生图，20步，CheckpointLoaderSimple |
| SANA 1.5 1.6B | ~3 GB | 1024×1024 | 文生图，20步 FP32，GemmaLoader本地化已修复 |
| FLUX.1-schnell | 22 GB | — | 快速文生图（4步），旧版 |
| FLUX.1-dev | 38 GB | — | 高质量文生图（25步），旧版 |

### 视频生成（ComfyUI）

| 模型 | 大小 | 分辨率 | 用途 |
|------|------|--------|------|
| MiniMax H3 | ~5 GB | 864×480 | 文生视频，5秒24fps，Turbo 4 Fast+SolAttn |
| CogVideoX-5b | 15 GB | — | 文生视频，旧版 |

### 语音

| 工具/模型 | 类型 | 指标 | 用途 |
|-----------|------|------|------|
| CosyVoice3-0.5B | 本地 TTS | RTF=1.123 | 零样本音色克隆，MPS需强制FP32 |
| SenseVoiceSmall | 本地 ASR | 0.78s (CPU) | 极速语音识别 |
| Fun-ASR-Nano | 本地 ASR | 3.71s (MPS) | GPU加速语音识别 |
| Whisper (base) | 本地 ASR | — | 语音转文字，旧版 |
| edge-tts | 云端 TTS | — | 文字转语音，旧版 |

### 向量嵌入

| 模型 | 维度 | 用途 |
|------|------|------|
| bge-large-zh | 1024 | 语义相似度，相关文本>0.86/不相关<0.67 |

## 🔀 智能路由

统一入口 `http://localhost:8606`，OpenAI 兼容 API，自动按意图路由：

| 规则 | 匹配方式 | 首选模型 | 降级链 |
|------|----------|----------|--------|
| 显式指定 | 请求中指定模型名 | 请求模型 | — |
| 图片理解 | 消息含图片 | Qwen2.5-VL-32B | Qwen3.8 → Qwen3.6-35B |
| 代码任务 | 关键词匹配 | Qwen3.8-27B | 远程Qwen3.6 → Qwen3.6-35B |
| 推理任务 | 关键词匹配 | Qwen3.6-35B-MoE | TeleChat3 → Qwen3.8 → 远程 |
| 短消息 | ≤200 token | gemma4:12b | Qwen3.8 → 远程 |
| 长文本 | ≥4000 token | Qwen3.8-27B | Qwen3.6-35B → TeleChat3 |
| 默认 | 兜底 | Qwen3.8-27B | 远程 → Qwen3.6-35B → gemma4 |