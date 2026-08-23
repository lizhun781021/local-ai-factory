---
name: local-ai-factory
description: 本地 AI 工厂管理技能。管理基于 MacBook Pro M5 Max 137GB 的全本地多模态 AI 环境（Streamlit WebUI + MLX + ComfyUI + RAGFlow），包括服务启停、WebUI 操作、知识库同步、模型路由、日志查看等。当用户提到"AI工厂"、"本地AI"、"启动/停止AI"、"AI工厂服务"、"AI工厂不能用了"、"8501"、"8082"、"8088"、"8188"、"ComfyUI"、"RAGFlow同步"、"本地模型"、"Streamlit WebUI"时使用本技能。关键词：AI工厂, local-ai-factory, Streamlit, MLX, ComfyUI, RAGFlow, 本地模型, 智能路由, 知识库同步, 文本对话, 图片生成, 视频生成, 语音识别, 语音合成, Qwen3.8, Qwen3.6, SenseVoice, MiniMax H3, SDXL, SANA。
name_cn: 本地 AI 工厂
description_cn: 管理基于 MacBook Pro M5 Max 的全本地多模态 AI 环境，包括 Streamlit WebUI（13个功能页面）、智能路由、知识库同步、模型启停等。
create_source: super-agent-skill-creator
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'f1a4cc08-6d85-41c2-9123-66687f7cef07'
  PropagateID: 'f1a4cc08-6d85-41c2-9123-66687f7cef07'
  ReservedCode1: '196cc8ae-3375-4f47-8819-de453545afd3'
  ReservedCode2: '196cc8ae-3375-4f47-8819-de453545afd3'
---

# 本地 AI 工厂

## 项目位置

```
~/Desktop/星小辰工作空间/local-ai-factory/
```

## 服务架构

| 服务 | 端口 | 说明 | 启动方式 |
|------|------|------|----------|
| WebUI | 8501 | Streamlit 主界面，13 个功能页面 | `streamlit run webui.py --server.port 8501` |
| LLM 常驻 | 8082 | Qwen3.8-27B-4bit（launchd 托管） | `./mlx-server.sh start` |
| OpenAI 代理 | 8088 | 多模型统一入口，智能路由 | `python3 router-server.py` |
| ComfyUI | 8188 | 图片/视频生成 | ComfyUI 启动 |
| RAGFlow | 9380/8086 | 知识库问答（API/代理） | Docker Compose |
| Ollama | 11434 | gemma4:12b + bge-large-zh | `ollama serve` |

## 快速操作

### 启停服务

```bash
cd ~/Desktop/星小辰工作空间/local-ai-factory
./start_all.sh     # 启动 LLM + ComfyUI + Ollama
./stop_all.sh      # 停止所有服务
./status.sh        # 查看运行状态
```

### 重启 WebUI

```bash
lsof -ti:8501 | xargs kill -9 2>/dev/null; sleep 2
cd ~/Desktop/星小辰工作空间/local-ai-factory
nohup python3 -m streamlit run webui.py --server.port 8501 --server.headless true --server.address 0.0.0.0 > /tmp/ai-factory.log 2>&1 &
```

### 知识库同步

```bash
cd ~/Desktop/星小辰工作空间/local-ai-factory
python3 rag_sync.py                    # 手动增量同步
cat rag_sync.log                       # 查看同步日志
```

定时任务已配置：每天 9:30 自动执行 `rag_sync.py`。

### 查看 WebUI 日志

```bash
tail -100 /tmp/ai-factory.log          # WebUI 运行日志
cat /tmp/ai-factory-activity.log        # 操作日志
```

## WebUI 功能页面（13 个）

| 页面 | 功能 |
|------|------|
| 📊 系统监控 | CPU/内存/磁盘/GPU 实时监控，服务健康检查 |
| 🧠 文本对话 | 多模型对话，支持上下文，Token 统计 |
| 🔬 模型对比 | 同一 prompt 并行调用多模型，横向对比 |
| 👁️ 图片理解 | 图片上传 + AI 描述/OCR/问答 |
| 🎬 视频理解 | 视频抽帧 + AI 分析 |
| 🎨 图片生成 | 文生图 1024×1024 (SDXL/SANA) |
| 🎬 视频生成 | 文生视频 864×480 5秒 (MiniMax H3) |
| 🎤 语音识别 | 音频转文字，说话人分离 (SenseVoice/Seaco) |
| 🔊 语音合成 | 文字转语音，9 音色，声音克隆 (Qwen3-TTS) |
| 📚 智能问答 | 自然语言提问 → RAGFlow 检索 + LLM 推理 → 结构化回答（含溯源） |
| 📈 Token 统计 | 各模型使用量/费用统计 |
| 📋 日志查看 | 操作日志 + 模型服务日志 |
| 📖 AI 工厂说明 | 项目文档 |

## 智能路由规则

统一入口 `http://localhost:8082`（常驻）/ `http://localhost:8088`（代理），OpenAI 兼容 API：

| 规则 | 首选模型 | 降级链 |
|------|----------|--------|
| 图片理解 | Qwen3.8-27B | → Qwen3.6-35B |
| 代码任务 | Qwen3.8-27B | → 远程 Qwen3.6 |
| 推理任务 | Qwen3.6-35B-MoE | → Qwen3.8 → 远程 |
| 短消息 (≤200 token) | gemma4:12b | → Qwen3.8 |
| 长文本 (≥4000 token) | Qwen3.8-27B | → Qwen3.6-35B |
| 默认兜底 | Qwen3.8-27B | → 远程 → Qwen3.6-35B |

## 本地模型清单

### 文本大模型 (LLM)
- Qwen3.8-27B-4bit: 15GB, 31.5 tps, 日常主力（常驻 8082），多模态兼视觉理解
- Qwen3.6-35B-A3B-bf16: 65GB, 30.8 tps, MoE 通用对话/推理
- gemma4:12b (Ollama): 7.6GB, 56.1 tps, 最快响应，短消息/翻译

### 图像/视频生成
- SDXL Base 1.0: 1024×1024, 20 步, 文生图
- SANA 1.5 1.6B: 1024×1024, FP32, 文生图
- MiniMax H3 4-bit: 864×480, 5秒 24fps, 文生视频（带同步音频）

### 语音
- Qwen3-TTS-0.6B: 9 音色 + 声音克隆
- SenseVoiceSmall: 0.78s CPU, 极速语音识别
- Seaco-Paraformer: 长音频识别 + 说话人分离

### 知识库与嵌入
- RAGFlow v0.27.0: 459 文档, bge-large-zh 向量化
- bge-large-zh (Ollama): 1024 维向量嵌入
- FTS5 本地索引: 454 文件, 53MB SQLite

### 云端/远程模型（降级备选）
- Qwen3.6-27B (vLLM): 武林提供 106.0.4.142:51211, 远程 LLM 降级
- 星辰慧记: 云端 ASR, 长音频识别 + 会议纪要
- edge-tts: 微软免费云端 TTS 备选

## 常见故障排查

### WebUI 打不开（8501）
1. 检查端口：`lsof -i:8501`
2. 杀旧进程：`lsof -ti:8501 | xargs kill -9`
3. 重启：见上方"重启 WebUI"

### LLM 服务无响应（8082）
1. 检查 launchd：`launchctl list | grep mlx`
2. 重启：`cd ~/Desktop/星小辰工作空间/local-ai-factory && ./mlx-server.sh start`
3. 测试：`./mlx-server.sh test`

### ComfyUI 无响应（8188）
1. 检查进程：`ps aux | grep -i comfyui | grep -v grep`
2. 检查端口：`lsof -i:8188`
3. 重启 ComfyUI

### 知识库搜索无结果
- RAGFlow 索引进度：检查 `rag_sync.log`
- FTS5 索引：检查 SQLite 文件是否存在
- 长句搜索：FTS5 使用 trigram 分词，≥6 字无空格长句需智能切分（webui.py 已实现）

### 端口冲突（KeepAlive 导致旧进程占端口）
- 症状：日志反复刷 "Port xxxx is not available"
- 原因：launchd KeepAlive 改 true 后旧进程占端口，新进程反复报错
- 修复：`lsof -ti:端口号 | xargs kill -9`，保留一个进程

## 操作日志

所有 WebUI 操作自动记录到 `/tmp/ai-factory-activity.log`，覆盖：
- 文本对话（模型、输入、tokens、耗时）
- 图片理解/生成/视频生成
- 语音识别/合成
- RAGFlow/FTS5 搜索（含关键词、结果数）

## 脚本

### scripts/factory_manager.sh

服务管理一键脚本，支持 start/stop/restart/status 操作：

```bash
bash scripts/factory_manager.sh start    # 启动所有服务
bash scripts/factory_manager.sh stop     # 停止所有服务
bash scripts/factory_manager.sh restart  # 重启所有服务
bash scripts/factory_manager.sh status   # 查看所有服务状态
bash scripts/factory_manager.sh sync     # 手动同步知识库
bash scripts/factory_manager.sh log      # 查看最近日志
```