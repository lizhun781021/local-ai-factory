---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'd98df0fa-9940-4eb8-abe1-1e317d1d705e'
  PropagateID: 'd98df0fa-9940-4eb8-abe1-1e317d1d705e'
  ReservedCode1: 'e8608218-f394-4d3e-9d44-a8bc02cc7200'
  ReservedCode2: 'e8608218-f394-4d3e-9d44-a8bc02cc7200'
---

# 📋 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

版本标签格式：`v{版本号}`

---

## [2.1.0] - 2026-08-24

### 🎯 WebUI 增强 + 长图更新 + 知识库同步

#### 新增
- **「📖 AI工厂说明」页面**：WebUI 新增项目说明菜单，集中展示功能模块、模型清单、智能路由规则、服务架构、数据安全、项目目录树
- **RAGFlow 知识库同步脚本** (`rag_sync.py`)：增量同步工作目录文档到 RAGFlow，支持文件比对（新增/更新/删除），每日 9:30 定时执行（launchd 托管）
- **侧边栏知识库同步状态**：WebUI 侧边栏和系统监控页显示上次同步时间和结果
- **AI 工厂项目介绍长图** (`AI工厂项目介绍长图.png`)：1280px 宽全页长图，含技术能力 9 卡片、业务能力 6 卡片、模型清单表格、服务架构图、数据安全
- **操作日志系统**：所有页面操作自动记录到 `/tmp/ai-factory-activity.log`，覆盖文本对话、图片理解、图片/视频生成、语音识别/合成、知识库搜索等

#### 更新
- **长图模型清单补充云端模型**：新增「云端/远程模型」分类，包含 vLLM 远程 Qwen3.6-27B（武林提供）、星辰慧记云端 ASR、edge-tts 云端 TTS
- **智能问答页面**：原「知识库查询」更名为「智能问答」，模型下拉框改为从 8088 代理动态获取可用模型
- **FTS5 搜索优化**：3 字滑窗 trigram 分词 + LIKE 联合查询，解决长句搜索不到的问题；搜索结果卡片式布局 + 关键词高亮 + 打开文件/文件夹按钮
- **RAGFlow 相关文件迁入项目目录**：rag_sync.py、rag_sync.log、ragflow-docker/ 统一归入 local-ai-factory/
- **Streamlit fragment 优化**：FTS5 搜索区块用 @st.fragment 包裹，避免全页 rerun 导致搜索卡顿

#### 修复
- **Markdown 渲染问题**：「AI工厂说明」页 st.markdown 内容因缩进被当代码块渲染，用 textwrap.dedent() 修复
- **服务端口冲突**：KeepAlive=true 导致旧进程占端口、新进程反复报错，杀旧进程后恢复
- **智能问答数据来源打不开**：增加本地 FTS5 路径匹配，匹配到则显示「打开文件」按钮

---

## [2.0.0] - 2026-08-22

### 🎯 依据本地模型基准测试全面更新

基于 model-benchmark 技能对本地 7 类模型系统测试后的全面更新。

#### 新增模型
- **LLM 常驻服务**：Qwen3.8-27B-4bit 在端口 8082 常驻 (launchd 托管)，启动即用
- **SDXL Base 1.0**：文生图模型，1024×1024，20步生成（替代旧版 FLUX）
- **SANA 1.5 1.6B**：文生图模型，1024×1024，FP32 精度，GemmaLoader 本地化已修复
- **MiniMax H3**：视频生成模型，864×480 5秒24fps，Turbo 4 Fast + SolAttn（替代旧版 CogVideoX）
- **CosyVoice3-0.5B**：零样本音色克隆 TTS，RTF=1.123（替代旧版 edge-tts）
- **SenseVoiceSmall + Fun-ASR-Nano**：双引擎 ASR，CPU 0.78s / MPS 3.71s（替代旧版 Whisper）
- **bge-large-zh**：向量嵌入模型，1024 维，via Ollama

#### 模型基准测试数据
- Qwen3.6-35B-A3B: 平均 30.8 tps（中文推理17.3/数学43.9/代码20.6/行业41.3）
- Qwen3.8-27B-4bit: 平均 31.5 tps（含 VLM 视觉理解 22.91s/图）
- gemma4:12b: 平均 56.1 tps（Ollama 模式，中文可用）
- CosyVoice3 RTF=1.123（接近实时）
- bge-large-zh: 相关文本相似度>0.86，不相关<0.67

#### 移除/禁用
- Qwen2.5-72B-Instruct-4bit：模型已删除，端口让给 Qwen3.8 常驻
- gemma-4-26b-a4b-it-bf16 (MoE)：模型已删除
- gemma-4-31b-it-bf16 (Dense)：模型已删除
- gemma-4-12B-8bit (MLX)：MLX 下中文不可用，仅 Ollama 模式保留
- FLUX.1-schnell / FLUX.1-dev：被 SDXL + SANA 替代
- CogVideoX-5b：被 MiniMax H3 替代
- Whisper (base)：被 FunASR 替代
- edge-tts：被 CosyVoice3 替代

#### 配置更新
- `router_config.yaml`：新增 7 个 ComfyUI/语音/向量模型条目，新增 qwen3.8-27b-resident 常驻条目
- `README.md`：全面更新模型列表、服务地址表、智能路由规则表，添加基准测试数据
- launchd plist：LLM 常驻服务已指向 Qwen3.8-27B-4bit（端口 8082）

#### 关键修复记录
- SANA 1.5 GemmaLoader 本地化：符号链接 + local_files_only=True + 设备检测 + FP32 强制
- SANA EmptySanaLatentImage device 修复：getattr 兜底 + try/except
- CosyVoice3 MPS dtype mismatch：推理前转 FP32
- gemma 全系列 MLX 下中文不可用，仅 Ollama 模式可用

---

## [1.0.0] - 2026-06-01

### 🎉 首个正式版本

#### 新增
- **LLM 模型支持**：Qwen3.6-35B、Qwen2.5-72B、TeleChat3-36B、gemma-4-26B/31B
- **视觉模型**：Qwen2.5-VL-32B 图片理解
- **图片生成**：FLUX.1-schnell（快速4步）、FLUX.1-dev（高质量25步）
- **视频生成**：CogVideoX-5b（16帧/480p）
- **语音识别**：openai-whisper（本地）
- **语音合成**：edge-tts（云端免费）
- **CLI 工具**：generate.py（图片）、generate_video.py（视频）、mlx-chat.py（对话）
- **服务管理**：start_all.sh、stop_all.sh、status.sh、mlx-server.sh
- **开机自启**：launchd 配置（LLM + ComfyUI）
- **输出目录**：output/image/、output/video/、output/audio/
- **文档**：README.md、128GB-MacBook-多模态环境搭建方案.md

#### 技术栈
- Apple MLX（mlx-lm）作为 LLM 推理框架
- diffusers + ComfyUI 作为图像/视频生成框架
- OpenAI 兼容 API（mlx-lm server）

#### 模型清单
| 模型 | 大小 | 用途 |
|------|------|------|
| Qwen3.6-35B-A3B-bf16 | 65 GB | LLM 对话 |
| Qwen2.5-72B-Instruct-4bit | 38 GB | LLM 对话 |
| TeleChat3-36B-Thinking-4bit | 19 GB | 思维链推理 |
| gemma-4-26b-a4b-it-bf16 | 48 GB | LLM 对话 |
| gemma-4-31b-it-bf16 | 58 GB | LLM 对话 |
| Qwen2.5-VL-32B-Instruct-4bit | 18 GB | 图片理解 |
| FLUX.1-schnell | 22 GB | 快速图片生成 |
| FLUX.1-dev | 38 GB | 高质量图片生成 |
| CogVideoX-5b | 15 GB | 视频生成 |

---

## [未发布]

### 计划
- [ ] 统一 CLI 入口（ai-factory 命令）
- [ ] Web UI 管理面板
- [ ] 模型热切换
- [ ] 批量生成任务队列