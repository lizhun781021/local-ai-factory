#!/bin/bash
# 本地 AI 工厂服务管理脚本
# 用法: bash factory_manager.sh [start|stop|restart|status|sync|log]

FACTORY_DIR=~/Desktop/星小辰工作空间/local-ai-factory
LOG_FILE=/tmp/ai-factory.log
PID_FILE=/tmp/ai-factory.pid

start_webui() {
    if lsof -ti:8501 > /dev/null 2>&1; then
        echo "✅ WebUI (8501) 已在运行"
        return 0
    fi
    cd "$FACTORY_DIR"
    nohup python3 -m streamlit run webui.py \
        --server.port 8501 \
        --server.headless true \
        --server.address 0.0.0.0 \
        > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 3
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8501 | grep -q 200; then
        echo "✅ WebUI (8501) 启动成功"
    else
        echo "❌ WebUI (8501) 启动失败，检查 $LOG_FILE"
    fi
}

stop_webui() {
    local pid=$(lsof -ti:8501 2>/dev/null)
    if [ -n "$pid" ]; then
        kill -9 $pid 2>/dev/null
        echo "✅ WebUI (8501) 已停止"
    else
        echo "⚪ WebUI (8501) 未运行"
    fi
}

status_all() {
    echo "========== 本地 AI 工厂服务状态 =========="
    # WebUI
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8501 | grep -q 200; then
        echo "✅ WebUI          (8501) 运行中"
    else
        echo "❌ WebUI          (8501) 未运行"
    fi
    # LLM 常驻
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8082/v1/models | grep -q 200; then
        echo "✅ LLM 常驻       (8082) 运行中"
    else
        echo "❌ LLM 常驻       (8082) 未运行"
    fi
    # OpenAI 代理
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8088/v1/models | grep -q 200; then
        echo "✅ OpenAI 代理    (8088) 运行中"
    else
        echo "❌ OpenAI 代理    (8088) 未运行"
    fi
    # ComfyUI
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8188 | grep -q 200; then
        echo "✅ ComfyUI        (8188) 运行中"
    else
        echo "❌ ComfyUI        (8188) 未运行"
    fi
    # RAGFlow
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9380 | grep -qE "200|301|302"; then
        echo "✅ RAGFlow        (9380) 运行中"
    else
        echo "❌ RAGFlow        (9380) 未运行"
    fi
    # Ollama
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:11434/api/tags | grep -q 200; then
        echo "✅ Ollama         (11434) 运行中"
    else
        echo "❌ Ollama         (11434) 未运行"
    fi
    echo "=========================================="
}

sync_kb() {
    cd "$FACTORY_DIR"
    echo "🔄 开始同步知识库..."
    python3 rag_sync.py
    echo "📋 同步结果:"
    tail -5 rag_sync.log
}

show_log() {
    echo "========== 最近 50 行日志 =========="
    tail -50 "$LOG_FILE" 2>/dev/null || echo "日志文件不存在: $LOG_FILE"
}

case "$1" in
    start)
        echo "🚀 启动本地 AI 工厂..."
        start_webui
        ;;
    stop)
        echo "🛑 停止本地 AI 工厂..."
        stop_webui
        ;;
    restart)
        echo "🔄 重启本地 AI 工厂..."
        stop_webui
        sleep 2
        start_webui
        ;;
    status)
        status_all
        ;;
    sync)
        sync_kb
        ;;
    log)
        show_log
        ;;
    *)
        echo "用法: bash factory_manager.sh [start|stop|restart|status|sync|log]"
        exit 1
        ;;
esac
