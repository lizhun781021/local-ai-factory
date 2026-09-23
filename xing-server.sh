#!/bin/bash
# Xing4.0 星辰语义大模型服务管理脚本 (transformers + PyTorch MPS)
# 使用方法: ./xing-server.sh [start|stop|status|test]
# 作者：李准的星小辰

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$SCRIPT_DIR/xing-server.py"
PORT=8089
PID_FILE="/tmp/xing-server.pid"
LOG_FILE="/tmp/xing-server.log"

start() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "⚠️  Xing4.0 服务已在运行 (PID: $(cat $PID_FILE))"
        return 1
    fi

    echo "🚀 启动 Xing4.0 服务..."
    echo "   模型: ~/Downloads/xing40 (bf16, 约60GB)"
    echo "   端口: $PORT"
    echo "   日志: $LOG_FILE"
    echo "   注意: 加载约需 30~60s，占用 MPS 内存约 55GB"

    nohup python3 "$SERVER" > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    # 等待服务就绪（最多 180s）
    for i in $(seq 1 60); do
        sleep 3
        if curl -s -m 2 "http://localhost:$PORT/health" 2>/dev/null | grep -q '"loaded":true'; then
            echo "✅ Xing4.0 服务启动成功 (PID: $(cat $PID_FILE))"
            echo "   API: http://localhost:$PORT/v1/chat/completions"
            return 0
        fi
        printf "   加载中 %02ds...\r" $((i*3))
    done
    echo ""
    echo "❌ 服务启动超时，查看日志: $LOG_FILE"
    return 1
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "🛑 停止 Xing4.0 服务 (PID: $PID)..."
            kill "$PID"
            rm -f "$PID_FILE"
            echo "✅ 已停止"
        else
            echo "⚠️  进程不存在，清理 PID 文件"
            rm -f "$PID_FILE"
        fi
    else
        echo "⚠️  Xing4.0 服务未运行"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "✅ Xing4.0 服务运行中 (PID: $(cat $PID_FILE))"
        curl -s -m 2 "http://localhost:$PORT/health" 2>/dev/null && echo ""
        echo "   API: http://localhost:$PORT/v1/chat/completions"
    else
        echo "❌ Xing4.0 服务未运行"
    fi
}

test() {
    echo "🔍 测试 Xing4.0 对话..."
    curl -s -m 300 "http://localhost:$PORT/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d '{"model":"xing4.0","messages":[{"role":"user","content":"你好，请用一句话介绍你自己"}],"max_tokens":50}' | python3 -m json.tool
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 2; start ;;
    status) status ;;
    test) test ;;
    *) echo "用法: $0 [start|stop|restart|status|test]" ;;
esac