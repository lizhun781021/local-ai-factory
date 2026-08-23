#!/bin/bash
# 本地 AI 工厂一键启动脚本

echo "🚀 启动本地 AI 工厂..."
echo "=" * 50

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. 启动 MLX 文本模型
echo "🧠 启动文本模型 (Qwen2.5 72B)..."
"$SCRIPT_DIR/mlx-server.sh" start

# 2. 启动 MLX 视觉模型
echo "👁️ 启动视觉模型 (Qwen2.5-VL 32B)..."
VISION_MODEL="/Users/lizhun/Desktop/星小辰工作空间/models/hf/models--mlx-community--Qwen2.5-VL-32B-Instruct-4bit/snapshots"
VISION_SNAP=$(ls -d "$VISION_MODEL"/*/ 2>/dev/null | head -1)
if ! lsof -i :8081 > /dev/null 2>&1; then
    nohup mlx_lm.server --model "$VISION_SNAP" --host 0.0.0.0 --port 8081 > /tmp/mlx-vision.log 2>&1 &
    echo "   视觉模型已启动 (PID: $!)"
else
    echo "   视觉模型已在运行"
fi

# 3. 启动 ComfyUI
echo "🎨 启动 ComfyUI..."
if ! lsof -i :8188 > /dev/null 2>&1; then
    cd ~/ComfyUI && nohup python main.py --listen > /tmp/comfyui.log 2>&1 &
    echo "   ComfyUI 已启动 (PID: $!)"
else
    echo "   ComfyUI 已在运行"
fi

# 4. 检查 MaxKB
echo "📦 检查 MaxKB..."
if lsof -i :8080 > /dev/null 2>&1; then
    echo "   MaxKB 已在运行"
else
    echo "   ⚠️ MaxKB 未运行，请手动启动: agentmemory"
fi

echo ""
echo "=" * 50
echo "✅ 启动完成！"
echo ""
echo "服务地址："
echo "  🧠 文本模型: http://localhost:8082"
echo "  👁️ 视觉模型: http://localhost:8081"
echo "  🎨 ComfyUI: http://localhost:8188"
echo "  📦 MaxKB: http://localhost:8080"
echo ""
echo "CLI 工具："
echo "  cd $SCRIPT_DIR"
echo "  python3 generate.py schnell '提示词'"
echo "  python3 generate.py dev '提示词'"
echo "  python3 generate_video.py '提示词'"
