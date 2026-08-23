#!/usr/bin/env python3
"""
本地大模型空闲自动卸载守护进程 (ai-idle-watchdog)

监控 8081/8082 上的 mlx_lm.server:
- 解析每个服务最近一条 2xx 响应日志时间戳 → 作为"活跃时间"
- 服务启动后 5 分钟加载宽限期,避免在模型加载途中误杀
- 空闲超过 30 分钟 → SIGTERM 优雅卸载,8 秒后仍未退则 SIGKILL
- launchd 配置 RunAtLoad=true 下次开机仍自启;watchdog 不会改 plist,
  只杀进程,所以 vision 的 KeepAlive 必须为 false(否则被重启)

判定"活跃"的钥匙:只看响应码 2xx,过滤掉公网扫描器的 400 噪音
(因为 host=0.0.0.0,8081 一直被扫描 TLS 握手)。
"""

import os
import re
import sys
import time
import signal
import subprocess
import logging.handlers
from datetime import datetime

# ========== 配置 ==========
IDLE_THRESHOLD = 30 * 60      # 空闲 30 分钟卸载
GRACE_PERIOD = 5 * 60         # 启动后 5 分钟宽限(等模型装载)
CHECK_INTERVAL = 60           # 主循环 60 秒
LOG_FILE = "/tmp/ai-watchdog.log"
TAIL_BYTES = 512 * 1024       # 只扫日志末尾 512KB,提速且足够

SERVICES = [
    {
        "name": "72B-LLM",
        "port": 8082,
        "logs": ["/tmp/llm-server.log", "/tmp/llm-server.err"],
        "match": "mlx_lm.server.*Qwen3.8-27B",
        "label": "com.local-ai-factory.llm",
    },
    {
        "name": "VL-32B",
        "port": 8081,
        "logs": ["/tmp/mlx-vision.log"],
        "match": "mlx_lm.server.*Qwen2.5-VL-32B",
        "label": "com.local-ai-factory.vision",
    },
]

# 形如: 127.0.0.1 - - [19/Jul/2026 00:48:32] "GET /v1/models HTTP/1.1" 200 -
# 提取 时间戳 / 方法 / 路径 / 响应码,便于过滤健康检查
LOG_PATTERN = re.compile(
    r'\[(\d{2}/[A-Za-z]{3}/\d{4} \d{2}:\d{2}:\d{2})\] "(\w+)\s+(\S+)\s+HTTP[^"]*"\s+(\d{3})'
)

# 这类端点只算健康检查,不能算真实活跃
#   /v1/models 是 OpenAI 标准模型列表(被 dashboard 用来探测存活)
HEALTH_PATHS = {"/v1/models", "/health", "/healthz", "/ready", "/"}


# ========== 工具 ==========
_LOGGER = logging.getLogger("watchdog")
_HANDLER = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=512 * 1024, backupCount=1, encoding="utf-8"
)
_HANDLER.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
_LOGGER.addHandler(_HANDLER)
_LOGGER.setLevel(logging.INFO)


def log(msg):
    _LOGGER.info(msg)
    print(msg, flush=True)


def get_pid(match_pattern):
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", match_pattern],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        pids = [p for p in out.split("\n") if p]
        return int(pids[0]) if pids else None
    except subprocess.CalledProcessError:
        return None


def get_proc_started(pid):
    """进程启动时间,datetime 对象"""
    try:
        out = subprocess.check_output(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        # 例: "Sat Jul 19 18:08:31 2026"
        return datetime.strptime(out, "%a %b %d %H:%M:%S %Y")
    except Exception:
        return None


def get_proc_etime_seconds(pid):
    try:
        out = subprocess.check_output(
            ["ps", "-o", "etime=", "-p", str(pid)],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        # 形如 "1-03:45:21" 或 "12:34" 或 "12:34:56"
        days = 0
        if "-" in out:
            d, out = out.split("-", 1)
            days = int(d)
        parts = [int(x) for x in out.split(":")]
        if len(parts) == 2:
            h, m, s = 0, parts[0], parts[1]
        elif len(parts) == 3:
            h, m, s = parts
        else:
            return None
        return days * 86400 + h * 3600 + m * 60 + s
    except Exception:
        return None


def parse_last_active(log_paths):
    """扫每个日志末尾,返回最近一条 2xx 响应时间。无则 None"""
    latest = None
    for path in log_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - TAIL_BYTES))
                chunk = f.read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        for m in LOG_PATTERN.finditer(chunk):
            ts_str, _method, req_path, code = m.group(1), m.group(2), m.group(3), m.group(4)
            # 只认 2xx 响应
            if not code.startswith("2"):
                continue
            # 健康检查端点不算真实活跃(否则 dashboard 探活会一直续命)
            norm = req_path.split("?", 1)[0]
            if norm in HEALTH_PATHS:
                continue
            try:
                ts = datetime.strptime(ts_str, "%d/%b/%Y %H:%M:%S")
            except ValueError:
                continue
            if latest is None or ts > latest:
                latest = ts
    return latest


def unload_service(svc, pid, reason):
    log(f"[{svc['name']}] unload → release memory (pid={pid}, reason={reason})")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(8):
        time.sleep(1)
        if get_pid(svc["match"]) is None:
            log(f"[{svc['name']}] unloaded cleanly")
            return
    try:
        os.kill(pid, signal.SIGKILL)
        log(f"[{svc['name']}] force killed (didn't exit in 8s)")
    except ProcessLookupError:
        pass


def check_once():
    """扫一轮,返回卸载/保留摘要"""
    now = datetime.now()
    summary = []
    for svc in SERVICES:
        try:
            pid = get_pid(svc["match"])
            if pid is None:
                summary.append(f"[{svc['name']}] not running")
                continue

            etime = get_proc_etime_seconds(pid)
            if etime is not None and etime < GRACE_PERIOD:
                summary.append(
                    f"[{svc['name']}] grace {etime}s/{GRACE_PERIOD}s (loading, skipped)"
                )
                continue

            last_active = parse_last_active(svc["logs"])
            if last_active is None:
                started = get_proc_started(pid)
                if started is None:
                    summary.append(f"[{svc['name']}] can't determine active time")
                    continue
                last_active = started
                reason = "no_requests_since_start"
            else:
                reason = "since_last_2xx"

            idle_sec = int((now - last_active).total_seconds())
            if idle_sec > IDLE_THRESHOLD:
                unload_service(svc, pid, f"{reason} idle={idle_sec}s")
                summary.append(f"[{svc['name']}] UNLOADED (idle {idle_sec}s)")
            else:
                summary.append(f"[{svc['name']}] idle {idle_sec}s ({reason})")
        except Exception as e:
            summary.append(f"[{svc['name']}] ERROR: {e}")
    return summary


# ========== 主循环 ==========
def main():
    log(
        f"ai-idle-watchdog started | "
        f"idle={IDLE_THRESHOLD}s grace={GRACE_PERIOD}s interval={CHECK_INTERVAL}s"
    )
    while True:
        try:
            summary = check_once()
            # 每轮都打一行心跳,方便 ai tail 实时观察
            log(" | ".join(summary))
        except Exception as e:
            log(f"main loop error: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("watchdog stopped by user")
        sys.exit(0)
    except Exception as e:
        log(f"watchdog crashed: {e}")
        sys.exit(1)
