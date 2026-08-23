#!/bin/bash
# MLX 文本模型 API 服务启动脚本
# 使用方法: ./mlx-server.sh [start|stop|status|test]

MODEL="/Users/lizhun/Desktop/星小辰工作空间/models/mlx-lm/Qwen3.8-27B-4bit"
PORT=8082
PID_FILE="/tmp/mlx-server.pid"
LOG_FILE="/tmp/mlx-server.log"

start() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "⚠️  MLX Server 已在运行 (PID: $(cat $PID_FILE))"
        return 1
    fi

    echo "🚀 启动 MLX Server..."
    echo "   模型: $MODEL"
    echo "   端口: $PORT"
    echo "   日志: $LOG_FILE"

    nohup mlx_lm.server \
        --model "$MODEL" \
        --host 0.0.0.0 \
        --port $PORT \
        --trust-remote-code \
        > "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    sleep 2

    if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "✅ MLX Server 启动成功 (PID: $(cat $PID_FILE))"
        echo "   API 地址: http://localhost:$PORT/v1/chat/completions"
    else
        echo "❌ 启动失败，查看日志: $LOG_FILE"
        return 1
    fi
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "🛑 停止 MLX Server (PID: $PID)..."
            kill "$PID"
            rm -f "$PID_FILE"
            echo "✅ 已停止"
        else
            echo "⚠️  进程不存在，清理 PID 文件"
            rm -f "$PID_FILE"
        fi
    else
        echo "⚠️  MLX Server 未运行"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "✅ MLX Server 运行中 (PID: $(cat $PID_FILE))"
        echo "   API: http://localhost:$PORT/v1/chat/completions"
    else
        echo "❌ MLX Server 未运行"
    fi
}

test() {
    echo "🧪 测试 MLX Server..."
    curl -s http://localhost:$PORT/v1/chat/completions \
        -H "Content-Type: application/json" \
        -d '{
            "model": "'"$MODEL"'",
            "messages": [{"role": "user", "content": "你好，简单介绍一下你自己"}],
            "max_tokens": 100
        }' | python3 -m json.tool
}

case "$1" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    test)   test ;;
    *)
        echo "用法: $0 {start|stop|status|test}"
        exit 1
        ;;
esac
