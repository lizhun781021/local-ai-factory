#!/bin/bash
# ============================================================
# healthcheck.sh - AI 工厂健康巡检
# 输出各服务端口监听 + HTTP 响应检查表，失败项提示
# 用法: ./healthcheck.sh            # 全量巡检
#       ./healthcheck.sh --json     # JSON 输出(可接定时任务/监控)
# ============================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

# 名称|端口|健康检查URL|说明
CHECKS=(
  "webui|8501|http://localhost:8501/_stcore/health|AI工厂WebUI"
  "llm|8082|http://localhost:8082/v1/models|LLM(Mlx)"
  "vision|8081|http://localhost:8081/health|视觉识别"
  "comfy|8188|http://localhost:8188/system_stats|ComfyUI"
  "router|8606|http://localhost:8606/health|智能路由"
  "proxy|8088|http://localhost:8088/v1/models|OpenAI代理"
  "ragflow|8086|http://localhost:8086/api/v1/datasets|RAGFlow知识库"
  "xing|8089|http://localhost:8089/health|Xing4.0"
  "maxkb|8085|-|MaxKB(已下线)"
)

_http_ms() { # $1=url → 输出毫秒或FAIL
  local url="$1" t0 t1
  t0=$(python3 -c 'import time;print(int(time.time()*1000))')
  code=$(curl -s -o /dev/null -m 4 -w '%{http_code}' "$url" 2>/dev/null)
  t1=$(python3 -c 'import time;print(int(time.time()*1000))')
  if [ "$code" = "000" ] || [ -z "$code" ]; then
    echo "FAIL"
  else
    echo "$((t1-t0))ms"
  fi
}

pass=0; fail=0
echo "== AI 工厂健康巡检 @ $TS =="
printf "  %-10s %-10s %-10s %s\n" "服务" "端口" "响应" "说明"
for c in "${CHECKS[@]}"; do
  IFS='|' read -r name port url desc <<< "$c"
  if [ "$port" = "-" ]; then continue; fi
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    if [ -n "$url" ]; then
      r=$( _http_ms "$url")
      if [ "$r" = "FAIL" ]; then
        printf "  %-10s %-10s %-10s %s\n" "$name" "$port" "❌无响应" "$desc"
        fail=$((fail+1))
      else
        printf "  %-10s %-10s %-10s %s\n" "$name" "$port" "✅$r" "$desc"
        pass=$((pass+1))
      fi
    else
      printf "  %-10s %-10s %-10s %s\n" "$name" "$port" "✅监听" "$desc"
      pass=$((pass+1))
    fi
  else
    printf "  %-10s %-10s %-10s %s\n" "$name" "$port" "❌未监听" "$desc"
    fail=$((fail+1))
  fi
done
echo "== 结果: $pass 正常 / $fail 异常 =="
[ "$fail" -gt 0 ] && exit 1 || exit 0