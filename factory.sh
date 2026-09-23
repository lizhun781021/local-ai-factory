#!/bin/bash
# ============================================================
# factory.sh - 本地 AI 工厂统一服务编排
# 管理: webui / llm / vision / comfy / router / eval / proxy / watchdog / tunnel / xing
# 用法: ./factory.sh [start|stop|restart|status] [all|<service>]
#       示例: ./factory.sh status all
#             ./factory.sh restart webui
#             ./factory.sh start xing
# ============================================================
set -u

LAUNCH_DIR="$HOME/Library/LaunchAgents"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 服务定义: 名称|launchd标签|端口|说明
SERVICES=(
  "webui|com.local-ai-factory.webui|8501|AI工厂WebUI"
  "llm|com.local-ai-factory.llm|8082|LLM(Mlx Qwen3.8-27B)"
  "vision|com.local-ai-factory.vision|8081|视觉识别"
  "comfy|com.local-ai-factory.comfyui|8188|ComfyUI(图/视频生成)"
  "router|com.local-ai-factory.router|8606|智能路由"
  "proxy|com.lizhun.openai-proxy|8088|OpenAI兼容代理"
  "watchdog|com.local-ai-factory.watchdog|-|空闲看门狗"
  "tunnel|com.lizhun.ai-factory-tunnel|8500|公网隧道"
  "eval|com.local-ai-factory.eval-dashboard|-|评估面板"
)

_find() {
  for s in "${SERVICES[@]}"; do
    [ "${s%%|*}" = "$1" ] && echo "$s" && return 0
  done
  return 1
}

_status_one() { # 服务定义串
  local name label port desc state portinfo
  IFS='|' read -r name label port desc <<< "$1"
  if launchctl list 2>/dev/null | grep -q "$label"; then
    state="🟢 运行中"
  else
    state="❌ 未运行"
  fi
  portinfo=""
  if [ "$port" != "-" ]; then
    if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      portinfo="端口${port}✓"
    else
      portinfo="端口${port}✗"
    fi
  fi
  printf "  %-10s %-24s %-10s %s\n" "$name" "$desc" "$state" "$portinfo"
}

_do_one() { # $1=act $2=name
  local act="$1" name="$2" def sname label port desc
  def="$(_find "$name")" || { echo "❌ 未知服务: $name"; return 1; }
  IFS='|' read -r sname label port desc <<< "$def"
  case "$act" in
    start)
      if [ "$name" = "xing" ]; then "$SCRIPT_DIR/xing-server.sh" start; return $?; fi
      launchctl load "$LAUNCH_DIR/$label.plist" 2>/dev/null
      echo "✅ $desc ($name) 已启动";;
    stop)
      if [ "$name" = "xing" ]; then "$SCRIPT_DIR/xing-server.sh" stop; return $?; fi
      launchctl unload "$LAUNCH_DIR/$label.plist" 2>/dev/null
      echo "🛑 $desc ($name) 已停止";;
    restart)
      if [ "$name" = "xing" ]; then "$SCRIPT_DIR/xing-server.sh" restart; return $?; fi
      launchctl unload "$LAUNCH_DIR/$label.plist" 2>/dev/null; sleep 1
      launchctl load "$LAUNCH_DIR/$label.plist" 2>/dev/null
      echo "🔄 $desc ($name) 已重启";;
    status) _status_one "$def";;
  esac
}

ACT="${1:-status}"
NAME="${2:-all}"

case "$ACT" in
  start|stop|restart|status)
    if [ "$NAME" = "all" ]; then
      echo "== AI 工厂服务 $ACT =="
      for s in "${SERVICES[@]}"; do
        IFS='|' read -r sname _l _p _d <<< "$s"
        _do_one "$ACT" "$sname"
      done
    else
      _do_one "$ACT" "$NAME"
    fi
    ;;
  list)
    echo "可用服务: webui llm vision comfy router eval proxy watchdog tunnel xing"
    echo "用法: $0 [start|stop|restart|status] [all|服务名]"
    ;;
  *)
    echo "用法: $0 [start|stop|restart|status] [all|服务名]"
    exit 1;;
esac