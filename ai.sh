#!/bin/bash
# 本地大模型按需管理命令
# 用法: ai {up|down|status|tail|help}
#
# 工作机制:
#   - 开机自启模型(launchd RunAtLoad=true)
#   - watchdog 每分钟扫一次日志
#   - 30 分钟无 2xx 请求 → 自动 SIGTERM 卸载,释放内存
#   - 下次需要 → 手动 ai up 拉起,或重启电脑自动起
#   - 平时切到 TeleAgent 本地模型用一阵,用完它自己卸

LLM_LABEL="com.local-ai-factory.llm"
VIS_LABEL="com.local-ai-factory.vision"
WDOG_LABEL="com.local-ai-factory.watchdog"
GUI_PREFIX="gui/$(id -u)"

# 颜色
G="\033[32m"; Y="\033[33m"; R="\033[31m"; GR="\033[90m"; N="\033[0m"

case "$1" in
    up)
        echo -e "🚀 拉起本地大模型..."
        # bootstrap 幂等(已注册则报错忽略) → kickstart 不带 -k(已跑则 no-op,未跑则启动)
        launchctl bootstrap "$GUI_PREFIX" "$HOME/Library/LaunchAgents/$LLM_LABEL.plist" 2>/dev/null
        if launchctl kickstart "$GUI_PREFIX/$LLM_LABEL" 2>/dev/null; then
            echo -e "  ${G}✅ 72B-LLM  端口 8082 已拉起${N}"
        else
            echo -e "  ${R}❌ 72B-LLM  拉起失败 (查看 launchctl print $GUI_PREFIX/$LLM_LABEL)${N}"
        fi
        launchctl bootstrap "$GUI_PREFIX" "$HOME/Library/LaunchAgents/$VIS_LABEL.plist" 2>/dev/null
        if launchctl kickstart "$GUI_PREFIX/$VIS_LABEL" 2>/dev/null; then
            echo -e "  ${G}✅ VL-32B   端口 8081 已拉起${N}"
        else
            echo -e "  ${R}❌ VL-32B   拉起失败${N}"
        fi
        echo ""
        echo -e "${Y}⏳ 首次加载需要 1~3 分钟(模型装载到内存)${N}"
        echo -e "   状态: ${GR}ai status${N}  |  探测: ${GR}curl -s http://localhost:8082/v1/models${N}"
        ;;
    down)
        echo -e "🛑 立即卸载本地大模型,释放内存..."
        if pkill -f "mlx_lm.server.*Qwen3.8-27B"; then
            echo -e "  ${G}✅ 72B-LLM 已卸载${N}"
        else
            echo -e "  ${GR}⚪ 72B-LLM 未运行${N}"
        fi
        if pkill -f "mlx_lm.server.*Qwen2.5-VL-32B"; then
            echo -e "  ${G}✅ VL-32B  已卸载${N}"
        else
            echo -e "  ${GR}⚪ VL-32B  未运行${N}"
        fi
        echo ""
        echo -e "💡 下次需要时: ${GR}ai up${N}"
        ;;
    status)
        echo -e "📊 本地大模型状态"
        echo -e "──────────────────────────────────────"
        for pair in "72B-LLM:8082" "VL-32B:8081"; do
            name="${pair%%:*}"
            port="${pair##*:}"
            pid=$(lsof -iTCP:$port -sTCP:LISTEN -P -n -t 2>/dev/null | head -1)
            if [ -n "$pid" ]; then
                rss_kb=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
                if [ -n "$rss_kb" ]; then
                    rss_gb=$(echo "scale=1; $rss_kb/1024/1024" | bc)
                    echo -e "  ${G}✅${N} $name   端口 $port   PID $pid   内存 ${rss_gb}GB"
                else
                    echo -e "  ${G}✅${N} $name   端口 $port   PID $pid"
                fi
            else
                echo -e "  ${GR}⚪${N} $name   端口 $port   未运行"
            fi
        done
        echo ""
        if pgrep -f "ai-idle-watchdog" >/dev/null 2>&1; then
            echo -e "  ${G}✅${N} 空闲守护运行中 (30min 无 2xx 请求自动卸载)"
        else
            echo -e "  ${R}⚠️${N}  空闲守护未运行"
        fi
        echo -e "──────────────────────────────────────"
        echo -e "命令: ${GR}ai up${N} | ${GR}ai down${N} | ${GR}ai status${N} | ${GR}ai tail${N} | ${GR}ai watchdog restart${N}"
        ;;
    tail)
        echo -e "📜 watchdog 日志 (最近 30 行):"
        echo -e "──────────────────────────────────────"
        if [ -f /tmp/ai-watchdog.log ]; then
            tail -30 /tmp/ai-watchdog.log
        else
            echo -e "  ${GR}暂无日志${N}"
        fi
        ;;
    watchdog)
        case "$2" in
            restart)
                echo -e "🔄 重启 watchdog..."
                launchctl kickstart -k "$GUI_PREFIX/$WDOG_LABEL" 2>/dev/null \
                    && echo -e "  ${G}✅ watchdog 已重启${N}" \
                    || echo -e "  ${R}❌ 重启失败${N}"
                ;;
            stop)
                launchctl bootout "$GUI_PREFIX/$WDOG_LABEL" 2>/dev/null \
                    && echo -e "  ${G}✅ watchdog 已停止${N}" \
                    || echo -e "  ${R}❌ 停止失败${N}"
                ;;
            *)
                echo -e "用法: ai watchdog {restart|stop}"
                ;;
        esac
        ;;
    help|*)
        cat <<'EOF'
本地大模型按需管理

用法: ai <命令>

命令:
  up               拉起 72B-LLM + VL-32B (首次加载 1~3 分钟)
  down             立即卸载,释放内存 (~38GB)
  status           查看运行状态与内存占用
  tail             查看 watchdog 日志
  watchdog restart 重启守护进程
  watchdog stop    停止守护进程

工作机制:
  - 开机自动启动模型(launchd RunAtLoad=true)
  - watchdog 每 60 秒扫描服务日志
  - 30 分钟无 HTTP 2xx 响应 → 自动卸载,释放内存
  - 过滤掉公网扫描器的 400 噪音(只认 2xx)
  - 服务启动有 5 分钟加载宽限期
  - 下次需要时手动 ai up,或重启电脑自启
EOF
        ;;
esac
