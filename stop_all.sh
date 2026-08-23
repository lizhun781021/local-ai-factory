#!/bin/bash
# 本地 AI 工厂一键停止脚本

echo "🛑 停止本地 AI 工厂..."
echo "=" * 50

# 1. 停止 MLX 文本模型
echo "🧠 停止文本模型..."
pkill -f "mlx_lm.server.*8082" 2>/dev/null && echo "   已停止" || echo "   未运行"

# 2. 停止 MLX 视觉模型
echo "👁️ 停止视觉模型..."
pkill -f "mlx_lm.server.*8081" 2>/dev/null && echo "   已停止" || echo "   未运行"

# 3. 停止 ComfyUI
echo "🎨 停止 ComfyUI..."
pkill -f "python.*main.py.*ComfyUI" 2>/dev/null && echo "   已停止" || echo "   未运行"

echo ""
echo "=" * 50
echo "✅ 停止完成！"
echo "   MaxKB 未停止（需要手动: pkill -f agentmemory）"
