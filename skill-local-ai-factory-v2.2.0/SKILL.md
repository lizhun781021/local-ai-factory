---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '928c6b9e-0e20-47d1-adab-d7339ae4fcef'
  PropagateID: '928c6b9e-0e20-47d1-adab-d7339ae4fcef'
  ReservedCode1: '388a3677-1149-4442-8bcd-44782a9fd10d'
  ReservedCode2: '388a3677-1149-4442-8bcd-44782a9fd10d'
---

### 查看 WebUI 日志

```bash
tail -100 /tmp/ai-factory.log          # WebUI 运行日志
cat /tmp/ai-factory-activity.log        # 操作日志
```

## 公网访问（手机/远程）

AI 工厂已支持公网访问，手机浏览器打开即用（**页面内密码登录**，无浏览器弹框）：

```bash
地址：http://219.151.184.206:8500
密码：lz781021（页面内输入；webui.py 的 AUTH_PASSWORD 变量，可用环境变量 AI_FACTORY_PASSWORD 覆盖）
```

架构链路：手机 → 天翼云 219.151.184.206:8500（nginx 反代）→ SSH 反向隧道(127.0.0.1:8501) → 本地 8501

- 本地隧道由 launchd 托管：`com.lizhun.ai-factory-tunnel.plist`（autossh，KeepAlive 自启，断线自动重连）
- 天翼云 nginx：`/etc/nginx/conf.d/ai-factory.conf`（监听 8500，WebSocket 反代，已移除 Basic Auth）
- 安全组：天翼云控制台已放行 TCP 8500（另已有 22、1011）
- 完整运维手册：项目目录 `AI工厂公网访问备忘.html`

### 公网故障排查

- **隧道僵死**（云端 8501 CLOSE_WAIT 堆积、转发超时）：重启本地隧道 `kill $(pgrep -f "autossh.*8501")`（launchd 自动拉起）；仍不通则云端杀掉对应 sshd 释放 8501 端口再重启
- **手机/浏览器打开白屏**：确认 webui 启动参数含 `--server.enableCORS false --server.enableXsrfProtection false`（Streamlit 默认校验 Origin，公网 WebSocket 会 403）
- **改访问密码**：改 webui.py 的 `AUTH_PASSWORD` 或设环境变量后重启 webui

## WebUI 功能页面（13 个）