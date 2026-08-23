# MLX 本地 AI 环境使用指南

## 🚀 快速开始

### 1. 启动 MLX Server

```bash
cd ~/Desktop/my_programs
./mlx-server.sh start
```

### 2. 测试 API

```bash
./mlx-server.sh test
```

### 3. 开始聊天

```bash
python3 mlx-chat.py
```

### 4. 性能测试

```bash
python3 mlx-benchmark.py
```

### 5. 停止服务

```bash
./mlx-server.sh stop
```

---

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| `mlx-server.sh` | MLX Server 启动/停止脚本 |
| `mlx-chat.py` | 交互式聊天客户端 |
| `mlx-benchmark.py` | 性能测试工具 |
| `mlx-README.md` | 本文档 |

---

## 🔧 API 使用

### OpenAI 兼容 API

```bash
curl http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Qwen2.5-72B-Instruct-4bit",
    "messages": [{"role": "user", "content": "你好"}],
    "max_tokens": 2048,
    "temperature": 0.7
  }'
```

### Python 调用

```python
import requests

resp = requests.post(
    "http://localhost:8082/v1/chat/completions",
    json={
        "model": "mlx-community/Qwen2.5-72B-Instruct-4bit",
        "messages": [{"role": "user", "content": "你好"}],
    }
)
print(resp.json()["choices"][0]["message"]["content"])
```

---

## 💡 使用技巧

### 1. 修改系统提示词

编辑 `mlx-chat.py` 中的 `SYSTEM_PROMPT` 变量。

### 2. 调整生成参数

- `temperature`：控制随机性（0.0-1.0）
- `max_tokens`：最大生成长度
- `top_p`：核采样参数

### 3. 长对话管理

长对话会消耗更多内存，建议定期清理历史。

---

## ⚠️ 注意事项

1. **内存占用**：模型运行时占用约 45GB 内存
2. **首次加载**：首次启动需要 30-60 秒加载模型
3. **端口冲突**：默认使用 8082 端口，避免与 MaxKB 冲突
4. **并发限制**：单实例不支持并发请求

---

## 🔗 相关资源

- [MLX 官方文档](https://github.com/ml-explore/mlx)
- [Qwen2.5 模型](https://huggingface.co/Qwen/Qwen2.5-72B-Instruct)
- [MLX 社区模型](https://huggingface.co/mlx-community)
