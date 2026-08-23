#!/usr/bin/env python3
"""
MLX 聊天客户端 - 与本地 Qwen2.5 72B 对话
用法: python3 mlx-chat.py
"""

import requests
import json
import sys

API_URL = "http://localhost:8082/v1/chat/completions"
MODEL = "Qwen3.8-27B-4bit"

# 系统提示词（可根据需要修改）
SYSTEM_PROMPT = """你是一个有用的 AI 助手，基于 Qwen2.5 72B 模型运行在本地 MacBook Pro 上。
你会用中文回答问题，语言简洁明了。"""


def chat(messages):
    """发送对话请求"""
    try:
        resp = requests.post(
            API_URL,
            json={
                "model": MODEL,
                "messages": messages,
                "max_tokens": 2048,
                "temperature": 0.7,
                "stream": False,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        else:
            return f"错误: {resp.status_code} - {resp.text}"
    except requests.exceptions.ConnectionError:
        return "错误: 无法连接到 MLX Server，请先启动服务: ./mlx-server.sh start"
    except Exception as e:
        return f"错误: {e}"


def main():
    print("=" * 50)
    print("🤖 MLX 本地 AI 聊天")
    print(f"   模型: {MODEL}")
    print(f"   API: {API_URL}")
    print("=" * 50)
    print("输入消息开始对话，输入 'quit' 退出")
    print()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ["quit", "exit", "q"]:
            print("👋 再见！")
            break

        messages.append({"role": "user", "content": user_input})

        print("AI: ", end="", flush=True)
        response = chat(messages)
        print(response)

        messages.append({"role": "assistant", "content": response})
        print()


if __name__ == "__main__":
    main()
