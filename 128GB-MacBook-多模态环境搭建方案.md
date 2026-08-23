# 128GB MacBook Pro 本地多模态 AI 环境搭建方案（MLX 版）

> 📅 更新时间：2026-06-01
> 💻 目标设备：MacBook Pro 128GB 统一内存（Apple Silicon）
> 🎯 目标：搭建完整的本地多模态 AI 环境（全部使用 Apple MLX 优化）

---

## 📋 目录

1. [整体架构](#整体架构)
2. [硬件资源规划](#硬件资源规划)
3. [软件工具选型](#软件工具选型)
4. [模型选型详解](#模型选型详解)
5. [安装部署步骤](#安装部署步骤)
6. [使用场景示例](#使用场景示例)
7. [常见问题](#常见问题)

---

## 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│              本地 AI 工厂（MLX 全家桶）                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  🧠 文本大脑          👁️ 视觉眼睛         🎨 绘画大师           │
│  Qwen3.6 35B         Qwen2.5-VL 32B      FLUX.1-schnell        │
│  Qwen2.5 72B         (图片/视频理解)      FLUX.1-dev            │
│  TeleChat3 36B                          (图片生成)              │
│  gemma-4 26B/31B                                                │
│  (对话/推理/代码)     MLX                 ComfyUI              │
│      MLX                                                        │
│                                                                 │
│  🎬 视频工厂          🎤 语音助手                                │
│  CogVideoX 5B        Whisper (STT)                             │
│  (视频生成)           edge-tts (TTS)                            │
│   ComfyUI             Python                                    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  🔧 统一管理：mlx-lm server + ComfyUI Web                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 硬件资源规划

### 模型存储占用

| 分类 | 模型 | 磁盘占用 |
|------|------|---------|
| LLM | Qwen3.6-35B-A3B-bf16 | 65 GB |
| LLM | Qwen2.5-72B-Instruct-4bit | 38 GB |
| LLM | TeleChat3-36B-Thinking-4bit | 19 GB |
| LLM | gemma-4-26b-a4b-it-bf16 | 48 GB |
| LLM | gemma-4-31b-it-bf16 | 58 GB |
| VL | Qwen2.5-VL-32B-Instruct-4bit | 18 GB |
| 图像 | FLUX.1-schnell | 22 GB |
| 图像 | FLUX.1-dev | 38 GB |
| 视频 | CogVideoX-5b | 15 GB |
| 语音 | Whisper (base) | ~1 GB |
| **总计** | | **~322 GB** |

### 运行时内存分配

| 任务 | 模型 | 运行框架 | 内存占用 |
|------|------|---------|---------|
| 🧠 文本对话 | Qwen3.6 35B | mlx-lm | ~69 GB |
| 🧠 文本对话 | Qwen2.5 72B | mlx-lm | ~41 GB |
| 👁️ 图片理解 | Qwen2.5-VL 32B | mlx-lm | ~22 GB |
| 🎨 图片生成 | FLUX.1-schnell | ComfyUI/diffusers | ~15 GB |
| 🎬 视频生成 | CogVideoX 5B | ComfyUI/diffusers | ~12 GB |
| 🎤 语音识别 | Whisper | openai-whisper | ~3 GB |

> ⚠️ 不要同时运行所有模型，按需加载。

---

## 软件工具选型

### 核心工具

| 功能 | 工具 | 说明 |
|------|------|------|
| **LLM 推理** | **mlx-lm** (Apple 官方) | MLX 原生 LLM 库，性能最佳 |
| **视觉模型** | **mlx-vlm** | MLX 视觉语言模型 |
| **图片生成** | **diffusers** / **ComfyUI** | FLUX、SD 等扩散模型 |
| **视频生成** | **diffusers** / **ComfyUI** | CogVideoX |
| **语音识别** | **openai-whisper** | 本地语音转文字 |
| **语音合成** | **edge-tts** | 微软云端 TTS（免费） |
| **API 服务** | **mlx-lm server** | OpenAI 兼容 API |

### 工具安装

```bash
pip install mlx mlx-lm mlx-metal       # LLM 核心
pip install diffusers torch torchvision # 图像/视频生成
pip install openai-whisper              # 语音识别
pip install edge-tts                    # 语音合成
pip install imageio imageio-ffmpeg      # 视频导出
```

---

## 模型选型详解

### LLM 文本模型（mlx-lm）

| 模型 | 参数 | 大小 | 速度 | 特点 |
|------|------|------|------|------|
| Qwen3.6-35B-A3B-bf16 | 35B | 65 GB | **73.7 tok/s** | 最新 Qwen，速度快 |
| Qwen2.5-72B-Instruct-4bit | 72B | 38 GB | 4.1 tok/s | 中文最强，代码优秀 |
| TeleChat3-36B-Thinking-4bit | 36B | 19 GB | — | 思维链推理 |
| gemma-4-26b-a4b-it-bf16 | 26B | 48 GB | — | Google 出品 |
| gemma-4-31b-it-bf16 | 31B | 58 GB | — | Google 出品 |

### 视觉语言模型

| 模型 | 大小 | 用途 |
|------|------|------|
| Qwen2.5-VL-32B-Instruct-4bit | 18 GB | 图片理解、OCR、视频理解 |

### 图像生成（ComfyUI / diffusers）

| 模型 | 大小 | 步数 | 用途 |
|------|------|------|------|
| FLUX.1-schnell | 22 GB | 4 步 | 快速文生图 |
| FLUX.1-dev | 38 GB | 25 步 | 高质量文生图 |

### 视频生成

| 模型 | 大小 | 用途 |
|------|------|------|
| CogVideoX-5b | 15 GB | 文生视频（16帧/480p 稳定） |

### 语音

| 工具 | 类型 | 说明 |
|------|------|------|
| openai-whisper | 语音→文字 | 本地运行，支持 99 种语言 |
| edge-tts | 文字→语音 | 云端（微软），免费，中文效果好 |

---

## 安装部署步骤

### 第一步：基础环境

```bash
# Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python
brew install python@3.11

# Git
brew install git
```

### 第二步：安装 MLX

```bash
pip install mlx mlx-lm mlx-metal

# 验证
python3 -c "import mlx.core as mx; print(f'MLX 设备: {mx.default_device()}')"
```

### 第三步：安装 diffusers / ComfyUI

```bash
# diffusers（推荐，代码调用更灵活）
pip install diffusers torch torchvision transformers

# ComfyUI（可选，Web UI 更直观）
git clone https://github.com/comfyanonymous/ComfyUI ~/ComfyUI
cd ~/ComfyUI && pip install -r requirements.txt
```

### 第四步：安装语音工具

```bash
pip install openai-whisper   # 语音识别
pip install edge-tts         # 语音合成
```

### 第五步：下载模型

```bash
# LLM 模型（用 huggingface-cli，支持镜像站加速）
export HF_ENDPOINT=https://hf-mirror.com  # 国内加速

huggingface-cli download mlx-community/Qwen3.6-35B-A3B-bf16
huggingface-cli download mlx-community/Qwen2.5-72B-Instruct-4bit
huggingface-cli download mlx-community/Qwen2.5-VL-32B-Instruct-4bit

# 图像/视频模型通过 ComfyUI 下载，或手动放到对应目录
```

### 第六步：启动服务

```bash
cd ~/Desktop/my_programs/local-ai-factory

# 一键启动
./start_all.sh

# 或单独启动
./mlx-server.sh start              # LLM API (端口 8082)
mlx_lm server --model <视觉模型路径> --port 8081  # 视觉 API
cd ~/ComfyUI && python main.py --listen  # ComfyUI (端口 8188)
```

---

## 使用场景示例

### 文本对话

```bash
# CLI 交互
mlx_lm chat --model ~/.omlx/models/Qwen3.6-35B-A3B-bf16

# 单次生成
mlx_lm generate --model ~/.omlx/models/Qwen3.6-35B-A3B-bf16 --prompt "解释量子计算"

# API 调用
curl http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "你好"}]}'
```

### 图片生成

```bash
# 通过 CLI 工具（ComfyUI API）
python3 generate.py schnell "a cute cat in space"
python3 generate.py dev "a beautiful sunset, photorealistic"

# 通过 diffusers 直接调用
python3 -c "
from diffusers import FluxPipeline
import torch
pipe = FluxPipeline.from_pretrained('FLUX模型路径', torch_dtype=torch.float16)
pipe.to('mps')
image = pipe('a cute cat', num_inference_steps=4).images[0]
image.save('output.png')
"
```

### 视频生成

```bash
python3 generate_video.py "A cat walking on the moon"

# 或用 diffusers
python3 -c "
from diffusers import CogVideoXPipeline
from diffusers.utils import export_to_video
import torch
pipe = CogVideoXPipeline.from_pretrained('CogVideoX路径', torch_dtype=torch.float16)
pipe.to('mps')
video = pipe('a cat', num_frames=16, num_inference_steps=20).frames[0]
export_to_video(video, 'output.mp4', fps=8)
"
```

### 语音识别

```bash
whisper audio.mp3 --model base --language zh --output_format txt
```

### 语音合成

```bash
edge-tts --voice zh-CN-XiaoxiaoNeural --text "你好世界" --write-media output.mp3
```

---

## 常见问题

### Q1：mlx-lm 用 HF model ID 加载超时？

用本地路径代替：
```bash
# 找到 HF 缓存中的 snapshot 路径
ls ~/.cache/huggingface/hub/models--mlx-community--Qwen2.5-72B-Instruct-4bit/snapshots/*/

# 用该路径作为 model 参数
mlx_lm generate --model ~/.cache/huggingface/hub/models--mlx-community--Qwen2.5-72B-Instruct-4bit/snapshots/<hash>/
```

或直接用本地目录：
```bash
mlx_lm generate --model ~/.omlx/models/Qwen3.6-35B-A3B-bf16 --prompt "你好"
```

### Q2：TeleChat3 加载报错？

需要 `trust_remote_code`：
```python
from mlx_lm import load, generate
model, tokenizer = load('模型路径', tokenizer_config={'trust_remote_code': True})
```

### Q3：视频生成爆显存？

降低参数：
- `num_frames=16`（不要 49）
- `width=480, height=320`（不要 720p）
- `num_inference_steps=20`（不要 50）

### Q4：国内下载模型太慢？

```bash
export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download mlx-community/模型名
```

### Q5：mlx-lm vs omlx？

- **mlx-lm**：Apple 官方，轻量灵活，适合开发
- **omlx**：第三方，有 GUI 和多模型管理，已卸载

---

## ✅ 部署完成记录（2026-06-01）

### 已安装模型

| 模型 | 大小 | 存储位置 | 状态 |
|------|------|---------|------|
| Qwen3.6-35B-A3B-bf16 | 65 GB | `~/.omlx/models/` | ✅ |
| Qwen2.5-72B-Instruct-4bit | 38 GB | `~/.cache/huggingface/hub/` | ✅ |
| TeleChat3-36B-Thinking-4bit | 19 GB | `~/.omlx/models/` | ✅ |
| gemma-4-26b-a4b-it-bf16 | 48 GB | `~/.omlx/models/` | ✅ |
| gemma-4-31b-it-bf16 | 58 GB | `~/.omlx/models/` | ✅ |
| Qwen2.5-VL-32B-Instruct-4bit | 18 GB | `~/.cache/huggingface/hub/` | ✅ |
| FLUX.1-schnell | 22 GB | `~/ComfyUI/models/unet/` | ✅ |
| FLUX.1-dev | 38 GB | `~/ComfyUI/models/unet/` | ✅ |
| CogVideoX-5b | 15 GB | `~/ComfyUI/models/CogVideoX-5b/` | ✅ |
| Whisper (base) | ~1 GB | `~/.cache/whisper/` | ✅ |

### 服务配置

| 服务 | 端口 | 启动命令 |
|------|------|---------|
| LLM API | 8082 | `./mlx-server.sh start` |
| 视觉 API | 8081 | `mlx_lm server --model <视觉模型> --port 8081` |
| ComfyUI | 8188 | `cd ~/ComfyUI && python main.py --listen` |
| MaxKB | 8080 | `agentmemory` |

### CLI 工具

```bash
cd ~/Desktop/my_programs/local-ai-factory

# 状态检查
./status.sh

# 图片生成
python3 generate.py schnell "提示词"
python3 generate.py dev "提示词"

# 视频生成
python3 generate_video.py "提示词"

# 文本对话
mlx_lm chat --model ~/.omlx/models/Qwen3.6-35B-A3B-bf16
```

### 输出目录

```
local-ai-factory/output/
├── image/   ← 生成的图片
├── video/   ← 生成的视频
└── audio/   ← 生成的音频/文字
```

---

*适用设备：MacBook Pro 128GB（Apple Silicon）*
*方案特点：全部使用 Apple MLX 优化，性能最佳*
