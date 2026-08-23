#!/usr/bin/env python3
"""
MLX 性能测试脚本
测试 Qwen2.5 72B 的响应速度
"""

import requests
import time
import statistics

API_URL = "http://localhost:8082/v1/chat/completions"
MODEL = "Qwen3.8-27B-4bit"

TEST_PROMPTS = [
    "你好",
    "解释量子计算的基本原理",
    "写一个 Python 快速排序算法",
    "用 100 字概括中国近代史",
]


def test_prompt(prompt, max_tokens=200):
    """测试单个提示词的响应时间"""
    start = time.time()

    try:
        resp = requests.post(
            API_URL,
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=120,
        )

        elapsed = time.time() - start

        if resp.status_code == 200:
            result = resp.json()
            content = result["choices"][0]["message"]["content"]
            tokens = result.get("usage", {}).get("completion_tokens", 0)
            return {
                "success": True,
                "time": elapsed,
                "tokens": tokens,
                "speed": tokens / elapsed if elapsed > 0 else 0,
                "response": content[:100] + "..." if len(content) > 100 else content,
            }
        else:
            return {"success": False, "time": elapsed, "error": resp.text}
    except Exception as e:
        return {"success": False, "time": 0, "error": str(e)}


def main():
    print("=" * 60)
    print("🚀 MLX 性能测试")
    print(f"   模型: {MODEL}")
    print(f"   API: {API_URL}")
    print("=" * 60)
    print()

    results = []

    for i, prompt in enumerate(TEST_PROMPTS, 1):
        print(f"[{i}/{len(TEST_PROMPTS)}] 测试: {prompt[:30]}...")
        result = test_prompt(prompt)
        results.append(result)

        if result["success"]:
            print(f"  ✅ 耗时: {result['time']:.2f}s | 速度: {result['speed']:.1f} tokens/s")
            print(f"  回复: {result['response']}")
        else:
            print(f"  ❌ 失败: {result['error']}")
        print()

    # 汇总统计
    print("=" * 60)
    print("📊 性能汇总")
    print("=" * 60)

    successful = [r for r in results if r["success"]]
    if successful:
        times = [r["time"] for r in successful]
        speeds = [r["speed"] for r in successful]

        print(f"成功率: {len(successful)}/{len(results)}")
        print(f"平均响应时间: {statistics.mean(times):.2f}s")
        print(f"平均生成速度: {statistics.mean(speeds):.1f} tokens/s")
        print(f"最快响应: {min(times):.2f}s")
        print(f"最慢响应: {max(times):.2f}s")
    else:
        print("所有测试失败")


if __name__ == "__main__":
    main()
