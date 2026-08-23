#!/bin/bash
# 检查本地 AI 工厂状态

echo "📊 本地 AI 工厂状态"
echo "=" * 50

# 检查各服务
check_service() {
    local name=$1
    local port=$2
    local url=$3

    if lsof -i :$port > /dev/null 2>&1; then
        echo "✅ $name (端口 $port) - 运行中"
    else
        echo "❌ $name (端口 $port) - 未运行"
    fi
}

check_service "文本模型 (Qwen2.5 72B)" 8082 "http://localhost:8082"
check_service "视觉模型 (Qwen2.5-VL 32B)" 8081 "http://localhost:8081"
check_service "ComfyUI" 8188 "http://localhost:8188"
check_service "MaxKB" 8080 "http://localhost:8080"

echo ""
echo "=" * 50
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "📁 输出目录"
echo "  图片: $SCRIPT_DIR/output/image/"
echo "  视频: $SCRIPT_DIR/output/video/"
echo "  音频: $SCRIPT_DIR/output/audio/"
img_count=$(ls "$SCRIPT_DIR/output/image/"*.{png,jpg} 2>/dev/null | wc -l | tr -d ' ')
vid_count=$(ls "$SCRIPT_DIR/output/video/"*.{mp4,webp} 2>/dev/null | wc -l | tr -d ' ')
aud_count=$(ls "$SCRIPT_DIR/output/audio/"*.{mp3,wav,txt} 2>/dev/null | wc -l | tr -d ' ')
echo "  图片: ${img_count} 个 | 视频: ${vid_count} 个 | 音频: ${aud_count} 个"

echo ""
echo "🔧 快捷命令"
echo "  启动全部: ./start_all.sh"
echo "  停止全部: ./stop_all.sh"
echo "  生成图片: python3 generate.py schnell '提示词'"
echo "  生成视频: python3 generate_video.py '提示词'"
