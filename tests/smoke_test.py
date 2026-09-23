#!/usr/bin/env python3
"""
AI 工厂页面冒烟测试（Playwright）
逐页打开检查页面是否正常渲染、有无报错（Traceback/APIException/错误提示）。
用法: python3 tests/smoke_test.py [--headless] [--base http://localhost:8501] [--password xxx]
"""
import sys, time, argparse
from playwright.sync_api import sync_playwright

PAGES = [
    "📊 系统监控", "🧠 文本对话", "🔬 模型对比", "👁️ 图片理解",
    "🎨 图片生成", "🎥 视频理解", "🎬 视频生成", "🎤 语音识别",
    "🔊 语音合成", "📚 智能问答", "📈 Token 统计", "📋 日志查看", "📖 AI工厂说明",
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", default=True, help="无头模式")
    ap.add_argument("--base", default="http://localhost:8501")
    ap.add_argument("--password", default="lz781021")
    args = ap.parse_args()

    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        page.goto(args.base, wait_until="domcontentloaded")
        time.sleep(2)
        # 登录（若需要）
        if page.locator("input[type=password]").count() > 0:
            page.fill("input[type=password]", args.password)
            page.get_by_role("button", name="登 录").click()
            time.sleep(2)

        for name in PAGES:
            try:
                page.get_by_text(name, exact=True).first.click(timeout=8000)
                time.sleep(2.5)
                body = page.inner_text("body")
                if "Traceback" in body or "StreamlitAPIException" in body or "InternalException" in body:
                    raise RuntimeError("页面出现 Traceback/APIException")
                # 抓 Streamlit 错误提示组件
                alert_errs = page.locator("[data-testid=stAlertException]").all()
                if alert_errs:
                    raise RuntimeError(f"页面异常提示: {alert_errs[0].inner_text()[:80]}")
                print(f"  ✅ {name}")
            except Exception as e:
                failed.append(name)
                print(f"  ❌ {name}: {str(e)[:120]}")

        browser.close()

    print("\n== 冒烟测试结果 ==")
    if failed:
        print(f"失败 {len(failed)} 页: {failed}")
        sys.exit(1)
    print("全部通过 ✅")
    sys.exit(0)

if __name__ == "__main__":
    main()