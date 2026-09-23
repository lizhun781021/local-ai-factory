---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '54f4eb21-7504-4ced-874f-a37a4fb13317'
  PropagateID: '54f4eb21-7504-4ced-874f-a37a4fb13317'
  ReservedCode1: 'f7af5812-a621-4674-a2e1-c2dc6bdfc152'
  ReservedCode2: 'f7af5812-a621-4674-a2e1-c2dc6bdfc152'
---

# 📋 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

版本标签格式：`v{版本号}`

---

## [2.2.2] - 2026-09-23

### 🚀 新增：Xing4.0 星辰语义大模型本地推理服务

#### 新增
- `xing-server.py`：Xing4.0（transformers + PyTorch MPS，bf16 约 60GB）OpenAI 兼容推理服务，端口 8089，支持 `/v1/models`、`/v1/chat/completions`、`/health`
- `xing-server.sh`：服务管理脚本（start/stop/restart/status/test），带就绪等待与健康检查
- `router_config.yaml` 新增 `xing4.0-local` 模型路由（本地端口 8089，上下文 262144，质量最高）

#### 其他
- `.gitignore` 补充 `*.bak`、`screenshots/` 忽略规则

---

## [2.2.1] - 2026-09-23

### 🐛 全量耗时页面修复：切换菜单不再丢失进度/结果

#### 背景
Streamlit 每次切换菜单会整页 rerun（以全新命名空间重跑脚本），此前所有耗时任务都在按钮点击块内**同步执行**、结果只存局部变量，切换菜单即中断任务并丢失结果展示。

#### 修复（统一方案：后台线程 + `st.cache_resource` 共享状态）
- **🔬 模型对比**：测试改为后台线程执行，进度（`[n/总数]`）与结果跨 rerun 保留，切页返回自动续显
- **👁️ 图片理解**：VLM 流式分析改为后台线程，生成中的文本实时同步，完成结果/思考过程/md 报告切页不丢
- **🎥 视频理解**：同上
- **🎨 图片生成**：ComfyUI 同步生成改为后台线程，进度条（步数/ETA）跨页续显，完成后图片+下载按钮保留
- **🎬 视频生成**（文生视频 + 图生视频）：5-10 分钟长任务改为后台线程，两个 tab 独立状态，进度与结果均跨页保留
- **🎤 语音识别**：ASR 子进程改为后台线程，识别日志实时同步，完成后完整文本/分段/说话人展示与下载保留
- **🔊 语音合成**（edge-tts / Qwen3-TTS 预置 / 声音克隆）：三处均改后台线程；Qwen3-TTS 模型缓存从 `st.session_state` 迁移到 `st.cache_resource`（后台线程无法访问 session_state，且模型须在主线程懒加载、线程持引用使用）

#### 关键技术说明（已写入代码注释）
- 后台线程**不能访问** `st.session_state`（缺脚本线程上下文），统一改用 `st.cache_resource` 缓存 dict 对象，主线程与后台线程共享引用
- 模块级全局变量在 rerun 时会被全新命名空间重置，同样不能用于跨 rerun 状态保持
- 受影响但无需修改的页面：智能问答（已用 session_state 缓存）、文本对话（历史本就在 session_state）、监控/统计/日志页（无耗时操作）

---

## [2.2.0] - 2026-09-16

### 🚀 公网访问 + 页面内登录 + 移动端自适应

#### 新增
- 公网访问：手机浏览器直接访问 AI 工厂（http://219.151.184.206:8500），算力仍在本地 Mac（SSH 反向隧道 + nginx 反代 + WebSocket 升级）
- 页面内密码登录：webui.py 内置登录门（`AUTH_PASSWORD` 变量，默认 lz781021，可用环境变量 `AI_FACTORY_PASSWORD` 覆盖），未登录仅显示登录页；nginx 已移除 Basic Auth，不再依赖浏览器弹框
- 移动端自适应：≤768px 屏幕多列布局自动单列堆叠、标题字号缩放、按钮全宽、侧边栏抽屉化（290px）且长文本自动换行，无横向滚动
- 运维备忘：`AI工厂公网访问备忘.html`（访问地址、架构链路、修改密码、隧道故障排查）

#### 更新
- WebUI 启动参数：增加 `--server.enableCORS false --server.enableXsrfProtection false`（公网反代必需，否则公网 WebSocket 被 403 拦截）
- 技能文档 v2.2.0：local-ai-factory 技能新增「公网访问（手机/远程）」章节与故障排查
- rag_sync.py：同名不同内容文件按 MD5 分组，非主版本用【父目录】前缀命名，避免知识库因同名丢内容

#### 修复
- 公网页面白屏：Streamlit 默认 CORS/XSRF 校验 Origin，导致公网 WebSocket 请求 403，页面停留加载状态
- 隧道僵死：SSH 反向转发 CLOSE_WAIT 堆积导致公网转发超时，固化清理流程（重启 autossh + 云端释放端口）

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