#!/usr/bin/env python3
"""
本地 AI 工厂 - 统一 Web UI 管理面板
基于 Streamlit + Plotly + psutil
"""
import streamlit as st
import psutil
import requests
import json
import time
import os
import sys
import subprocess
import sqlite3
import threading
from datetime import datetime, timedelta
from collections import deque
import plotly.graph_objects as go
import plotly.express as px

# ==================== 配置 ====================
LLM_API_URL = "http://localhost:8082"
LLM_PROXY_URL = "http://localhost:8088"  # OpenAI兼容代理（含NewApi/云端模型）
VISION_API_URL = "http://localhost:8081"
COMFYUI_URL = "http://localhost:8188"
# MAXKB_URL = "http://localhost:8085"  # 已下线
RAGFLOW_URL = "http://localhost:8086"
RAGFLOW_API_KEY = "ragflow-sFxr5UX1yM7ABLgOCjJOH11LOtJWC54DziBMVxavMh0"

# FTS5 本地全文搜索索引数据库
FTS_DB_PATH = os.path.join(os.path.expanduser("~/Desktop/工作/工作知识库"), "fts_index.db")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
# 结构化输出目录
OUTPUT_IMAGE_GEN = os.path.join(OUTPUT_DIR, "图像生成")      # ComfyUI/FLUX 图片生成
OUTPUT_VIDEO_GEN = os.path.join(OUTPUT_DIR, "视频生成")      # CogVideoX 视频生成
OUTPUT_AUDIO_TTS = os.path.join(OUTPUT_DIR, "语音合成")      # edge-tts / Qwen3-TTS 语音合成
OUTPUT_REPORTS = os.path.join(OUTPUT_DIR, "测试报告")        # 模型对比报告等
OUTPUT_IMAGE_RECOG = os.path.join(OUTPUT_DIR, "图像识别")    # 图片理解分析结果
OUTPUT_VIDEO_RECOG = os.path.join(OUTPUT_DIR, "视频识别")    # 视频理解分析结果
OUTPUT_SPEECH_RECOG = os.path.join(OUTPUT_DIR, "语音识别")   # 语音识别结果

# 启动时确保所有目录存在
for _d in [OUTPUT_IMAGE_GEN, OUTPUT_VIDEO_GEN, OUTPUT_AUDIO_TTS, OUTPUT_REPORTS, OUTPUT_IMAGE_RECOG, OUTPUT_VIDEO_RECOG, OUTPUT_SPEECH_RECOG]:
    os.makedirs(_d, exist_ok=True)

# ==================== 跨线程共享状态（模型对比 + 各耗时页面）====================
# 说明：Streamlit 后台线程无法访问 st.session_state（缺线程上下文），
#       且每次 rerun 会以全新命名空间执行脚本（模块级变量不保留）。
#       故用 st.cache_resource 缓存同一 dict 对象：
#       - 主线程每次 rerun 调用拿到同一对象；
#       - 后台线程持有该对象引用直接读写。
@st.cache_resource(show_spinner=False)
def _mc_store():
    return {
        "running": False, "status": "", "current": 0, "total": 0,
        "result_ready": False, "all_test_results": None, "all_perf_data": None,
    }

# 通用后台任务状态容器：各耗时页面用独立 key 存取任务状态
@st.cache_resource(show_spinner=False)
def _bt_store():
    return {}

# Token 统计（内存中持久化）
if "token_stats" not in st.session_state:
    st.session_state.token_stats = {
        "total_tokens": 0,
        "total_requests": 0,
        "history": deque(maxlen=100),
        "start_time": datetime.now()
    }

# ==================== 操作日志系统 ====================
ACTIVITY_LOG_FILE = "/tmp/ai-factory-activity.log"

def log_activity(action, detail="", status="ok", duration=None):
    """记录用户操作日志"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dur_str = f" ({duration:.1f}s)" if duration is not None else ""
    line = f"[{ts}] {action}{dur_str} | {status}"
    if detail:
        # detail 截取前500字符，避免日志过大
        detail_str = str(detail)[:500]
        line += f" | {detail_str}"
    try:
        with open(ACTIVITY_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# ==================== 文件路径自动修复 ====================
WORK_DIR = os.path.expanduser("~/Desktop/工作")
FTS_BUILD_SCRIPT = os.path.expanduser("~/Desktop/星小辰工作空间/work-knowledge-base/build_fts_index.py")
_fts_rebuild_lock = {"last_run": 0}  # 防止频繁重建（至少间隔60秒）

def resolve_file_path(doc_name):
    """
    查找文件的真实本地路径，支持自动修复：
    1. 先从 FTS 数据库查 file_path
    2. 路径失效时自动触发增量重建索引
    3. 重建后再次查询
    4. 仍然找不到时全盘搜索兜底
    返回: (path, source) 或 (None, None)
    """
    if not os.path.exists(FTS_DB_PATH):
        return None, None

    # Step 1: 从 FTS 数据库查
    def _query_fts(name):
        try:
            conn = sqlite3.connect(FTS_DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT file_path FROM files WHERE file_name = ? LIMIT 1", (name,))
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None
        except:
            return None

    db_path = _query_fts(doc_name)
    if db_path and os.path.exists(db_path):
        return db_path, "fts_db"

    # Step 2: 路径失效或不存在 → 触发增量重建（节流60秒）
    now = time.time()
    if now - _fts_rebuild_lock["last_run"] > 60:
        _fts_rebuild_lock["last_run"] = now
        try:
            subprocess.run(
                ["python3", FTS_BUILD_SCRIPT],
                capture_output=True, timeout=120,
                cwd=os.path.dirname(FTS_BUILD_SCRIPT)
            )
        except:
            pass
        # Step 3: 重建后重新查
        db_path = _query_fts(doc_name)
        if db_path and os.path.exists(db_path):
            return db_path, "fts_rebuilt"

    # Step 4: 全盘搜索兜底
    for dirpath, dirnames, filenames in os.walk(WORK_DIR):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != ".Trash"]
        if doc_name in filenames:
            found = os.path.join(dirpath, doc_name)
            # 顺便更新 FTS 数据库里的路径
            try:
                conn = sqlite3.connect(FTS_DB_PATH)
                cur = conn.cursor()
                cur.execute("UPDATE files SET file_path = ? WHERE file_name = ?", (found, doc_name))
                conn.commit()
                conn.close()
            except:
                pass
            return found, "filesystem_search"

    return None, None

# 系统监控数据
if "sys_history" not in st.session_state:
    st.session_state.sys_history = {
        "cpu": deque(maxlen=60),
        "memory": deque(maxlen=60),
        "timestamps": deque(maxlen=60)
    }


# ==================== 工具函数 ====================
def check_service(url, timeout=2):
    """检查服务状态"""
    try:
        resp = requests.get(url, timeout=timeout)
        return True
    except:
        return False


def check_port(port):
    """检查端口是否占用"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except:
        return False


@st.cache_data(ttl=15)
def get_service_status():
    """获取所有服务状态及模型名"""
    results = {}

    # 1. 文本与视觉识别模型（HTTP 服务，需要检查端口）
    try:
        resp = requests.get(f"{LLM_API_URL}/v1/models", timeout=2)
        data = resp.json()
        models = [m["id"] for m in data.get("data", [])]
        main_models = [m for m in models if "gemma" not in m.lower()]
        model_name = main_models[0] if main_models else (models[0] if models else "")
        if "/" in model_name:
            model_name = model_name.split("/")[-1]
        results["文本与视觉识别模型 (8082)"] = {"status": "online", "port": 8082, "model": model_name}
    except:
        results["文本与视觉识别模型 (8082)"] = {"status": "offline", "port": 8082, "model": ""}

    # 2. 图像与视频生成模型（HTTP 服务）
    try:
        resp = requests.get(f"{COMFYUI_URL}/system_stats", timeout=2)
        comfy_models = []
        mm_dir = os.path.expanduser("~/ComfyUI/models/minimax_h3/transformers")
        if os.path.isdir(mm_dir) and os.listdir(mm_dir):
            comfy_models.append("MiniMax-H3 (生视频)")
        sana_dir = os.path.expanduser("~/ComfyUI/models/sana")
        if os.path.isdir(sana_dir):
            sana_files = [f for f in os.listdir(sana_dir) if not f.startswith("models--") and f != ".DS_Store"]
            for f in sana_files:
                comfy_models.append("SANA (生图)")
        cp_dir = os.path.expanduser("~/ComfyUI/models/checkpoints")
        if os.path.isdir(cp_dir):
            cp_files = [f for f in os.listdir(cp_dir) if not f.startswith("put_") and f != ".DS_Store"]
            for f in cp_files:
                comfy_models.append("SDXL (生图)")
        model_name = " · ".join(comfy_models) if comfy_models else "图像与视频生成模型"
        results["图像与视频生成模型 (8188)"] = {"status": "online", "port": 8188, "model": model_name}
    except:
        results["图像与视频生成模型 (8188)"] = {"status": "offline", "port": 8188, "model": ""}

    # 3. 智能问答 RAGFlow（HTTP 服务，端口 8086）—— 排在最后，通过 priority 标记
    _kb_result = {}
    try:
        requests.get(RAGFLOW_URL, timeout=2)
        _kb_result["智能问答 (8086)"] = {"status": "online", "port": 8086, "model": "RAGFlow"}
    except:
        _kb_result["智能问答 (8086)"] = {"status": "offline", "port": 8086, "model": "RAGFlow"}

    # 4. 语音识别 ASR（本地 + 云端）
    asr_models = []
    # SenseVoiceSmall
    sv_dir = os.path.expanduser("~/.cache/modelscope/models/iic--SenseVoiceSmall/snapshots")
    if os.path.isdir(sv_dir):
        snaps = [d for d in os.listdir(sv_dir) if os.path.isdir(os.path.join(sv_dir, d))]
        if snaps:
            asr_models.append("SenseVoiceSmall")
    # Seaco-Paraformer
    seaco_dir = os.path.expanduser("~/.cache/modelscope/models/iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots")
    if os.path.isdir(seaco_dir):
        snaps = [d for d in os.listdir(seaco_dir) if os.path.isdir(os.path.join(seaco_dir, d))]
        if snaps:
            asr_models.append("Seaco-Paraformer")
    # 星辰慧记（云端）
    huiji_cfg = os.path.expanduser("~/.config/TeleAgent/skills/offline_asr/config/config.json.enc")
    if os.path.isfile(huiji_cfg):
        asr_models.append("星辰慧记(云端)")
    asr_model_name = " · ".join(asr_models) if asr_models else ""
    asr_status = "online" if asr_models else "offline"
    results["语音识别模型"] = {"status": asr_status, "port": "本地+云端", "model": asr_model_name}

    # 5. 语音合成 TTS（本地 + 云端）
    tts_models = []
    tts_cv_dir = os.path.expanduser("~/Desktop/星小辰工作空间/models/tts/Qwen3-TTS-12Hz-0.6B-CustomVoice")
    tts_base_dir = os.path.expanduser("~/Desktop/星小辰工作空间/models/tts/Qwen3-TTS-12Hz-0.6B-Base")
    if os.path.isdir(tts_cv_dir) and os.path.exists(os.path.join(tts_cv_dir, "model.safetensors")):
        tts_models.append("Qwen3-TTS-0.6B-CustomVoice")
    if os.path.isdir(tts_base_dir) and os.path.exists(os.path.join(tts_base_dir, "model.safetensors")):
        tts_models.append("Qwen3-TTS-0.6B-Base")
    # edge-tts（云端）
    try:
        import importlib
        importlib.import_module("edge_tts")
        tts_models.append("edge-tts(云端)")
    except Exception:
        pass
    tts_model_name = " · ".join(tts_models) if tts_models else ""
    tts_status = "online" if tts_models else "offline"
    results["语音合成模型"] = {"status": tts_status, "port": "本地+云端", "model": tts_model_name}

    # 知识库放最后
    results.update(_kb_result)

    return results


def get_last_sync_info():
    """读取 RAGFlow 知识库最近一次同步的时间与结果"""
    log_path = os.path.expanduser("~/Desktop/星小辰工作空间/local-ai-factory/rag_sync.log")
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 从末尾找最近一条 "同步完成" 行，该行可能没有时间戳，需要往前找
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            if "同步完成" in line and ("新增" in line or "更新" in line):
                result = line.strip()
                # 去掉可能的时间戳前缀
                if result.startswith("["):
                    ts_end = result.find("]")
                    result = result[ts_end+1:].strip() if ts_end > 0 else result
                # 往前找最近的时间戳
                ts = ""
                for j in range(i, max(i - 5, -1), -1):
                    prev = lines[j].strip()
                    if prev.startswith("[") and "]" in prev:
                        ts_end2 = prev.find("]")
                        ts = prev[1:ts_end2]
                        break
                return {"time": ts, "result": result}
        return {"time": "", "result": ""}
    except Exception:
        return {"time": "", "result": ""}


def get_system_info():
    """获取系统信息"""
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    # 获取 MPS/GPU 信息（Apple Silicon）
    gpu_info = None
    try:
        import torch
        if torch.backends.mps.is_available():
            # 通过进程估算 MPS 内存使用
            gpu_info = {"available": True, "device": "MPS"}
    except:
        gpu_info = {"available": False}

    return {
        "cpu_percent": cpu_percent,
        "memory": memory,
        "disk": disk,
        "gpu": gpu_info
    }


@st.cache_data(ttl=15)
def get_process_info():
    """获取模型相关进程信息"""
    model_processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
        try:
            cmdline = " ".join(proc.info['cmdline'] or [])
            if any(kw in cmdline for kw in ['mlx_lm', 'comfyui', 'main.py', 'llm-server', 'whisper']):
                model_processes.append({
                    "pid": proc.info['pid'],
                    "name": proc.info['name'],
                    "cmdline": cmdline[:80],
                    "memory_mb": proc.info['memory_info'].rss / 1024 / 1024 if proc.info['memory_info'] else 0
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return model_processes


def call_llm_api(messages, max_tokens=1024, model_path=None):
    """调用 LLM API 或 Ollama"""
    # 检查是否是 ollama 模型
    if model_path and model_path.startswith("ollama:"):
        model_name = model_path.replace("ollama:", "")
        prompt = messages[-1]["content"] if messages else ""
        try:
            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model_name,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": max_tokens}
                },
                timeout=300
            )
            if resp.status_code == 200:
                data = resp.json()
                usage = {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                    "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
                }
                return data.get("response", ""), usage
            else:
                return f"Ollama 错误: {resp.status_code}", {}
        except Exception as e:
            return f"Ollama 连接失败: {str(e)}", {}
    else:
        # 统一走 8082 mlx_lm server API，通过 model 参数指定模型
        try:
            # Qwen3 系列有思维链，需要更多 token 才能输出 content
            effective_max_tokens = max(max_tokens, 1024)
            payload = {
                "messages": messages,
                "max_tokens": effective_max_tokens,
            }
            if model_path:
                payload["model"] = model_path
            resp = requests.post(
                f"{LLM_API_URL}/v1/chat/completions",
                json=payload,
                timeout=300
            )
            if resp.status_code == 200:
                data = resp.json()
                msg = data["choices"][0]["message"]
                # Qwen3 有思维链：content 可能因 token 不足为 null，回退到 reasoning
                content = msg.get("content") or msg.get("reasoning") or "(模型未返回内容)"
                usage = data.get("usage", {})
                return content, usage
            else:
                return f"API 错误: {resp.status_code} - {resp.text[:200]}", {}
        except Exception as e:
            return f"API 连接失败: {str(e)}", {}


def generate_image_comfyui(prompt, model="sana", width=1024, height=1024, steps=None, seed=-1, cfg=4.0, progress_callback=None):
    """通过 ComfyUI API 生成图片（SANA / SDXL / Qwen-Image）
    progress_callback: 可选回调函数 func(step:int, total:int, status:str)
    """
    import requests, json, time, random, threading
    import websocket

    comfy_url = "http://localhost:8188"
    client_id = f"streamlit-{random.randint(10000,99999)}"

    if seed < 0:
        seed = random.randint(0, 2**32 - 1)

    os.makedirs(OUTPUT_IMAGE_GEN, exist_ok=True)

    if model == "sana":
        actual_steps = steps or 28
        # SANA 1.5 workflow: SanaCheckpointLoader(FP32) → ModelSamplingSD3(shift=6) → KSampler(euler)
        # 注意: MPS 设备上 BF16 会导致黑图，必须用 FP32
        actual_cfg = cfg if cfg != 4.0 else 5.0
        workflow = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": actual_steps, "cfg": actual_cfg,
                "sampler_name": "euler", "scheduler": "simple",
                "denoise": 1.0, "model": ["13", 0], "positive": ["14", 0],
                "negative": ["7", 0], "latent_image": ["5", 0]
            }},
            "4": {"class_type": "SanaCheckpointLoader", "inputs": {
                "ckpt_name": "Efficient-Large-Model/SANA1.5_1.6B_1024px",
                "model": "SanaMS1.5_1600M_P1_D20", "dtype": "FP32", "enable_cfg_passthrough": False
            }},
            "5": {"class_type": "EmptySanaLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "SanaTextEncode", "inputs": {"text": prompt, "GEMMA": ["11", 0]}},
            "7": {"class_type": "SanaTextEncode", "inputs": {"text": "", "GEMMA": ["11", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["9", 0]}},
            "9": {"class_type": "ExtraVAELoader", "inputs": {
                "vae_name": "mit-han-lab/dc-ae-f32c32-sana-1.1-diffusers",
                "vae_type": "dcae-f32c32-sana-1.1-diffusers", "dtype": "FP32"
            }},
            "11": {"class_type": "GemmaLoader", "inputs": {"model_name": "Efficient-Large-Model/gemma-2-2b-it", "device": "cpu", "dtype": "FP32"}},
            "12": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "sana"}},
            "13": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": 6.0}},
            "14": {"class_type": "SanaResolutionCond", "inputs": {"cond": ["6", 0], "width": width, "height": height}}
        }
    elif model == "sdxl":
        actual_steps = steps or 25
        # SDXL workflow: UNETLoader + DualCLIPLoader + VAELoader（散件格式）
        workflow = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": actual_steps, "cfg": cfg,
                "sampler_name": "dpmpp_2m", "scheduler": "karras",
                "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0],
                "negative": ["7", 0], "latent_image": ["5", 0]
            }},
            "4": {"class_type": "UNETLoader", "inputs": {
                "unet_name": "sdxl-base-1.0-unet.safetensors", "weight_dtype": "default"
            }},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["10", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["10", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["9", 0]}},
            "9": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl-vae-fp16-fix.safetensors"}},
            "10": {"class_type": "DualCLIPLoader", "inputs": {
                "clip_name1": "sdxl-text-encoder1.safetensors",
                "clip_name2": "sdxl-text-encoder2.safetensors",
                "type": "sdxl"
            }},
            "12": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "sdxl"}}
        }
    elif model == "qwen_image":
        actual_steps = steps or 8
        # Qwen-Image workflow: UNETLoader + CLIPLoader(qwen_image) + VAELoader + ModelSamplingAuraFlow
        workflow = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": actual_steps, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "simple",
                "denoise": 1.0, "model": ["13", 0], "positive": ["6", 0],
                "negative": ["7", 0], "latent_image": ["5", 0]
            }},
            "4": {"class_type": "UNETLoader", "inputs": {
                "unet_name": "qwen_image_fp8_e4m3fn.safetensors", "weight_dtype": "default"
            }},
            "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["11", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["9", 0]}},
            "9": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
            "11": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                "type": "qwen_image"
            }},
            "13": {"class_type": "ModelSamplingAuraFlow", "inputs": {
                "model": ["4", 0], "shift": 3.1
            }},
            "12": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "qwen"}}
        }
    else:
        return None, f"未知模型: {model}"

    # 确定总步数（用于进度计算）
    total_steps = actual_steps

    try:
        # 提交 workflow
        resp = requests.post(f"{comfy_url}/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=30)
        if resp.status_code != 200:
            return None, f"ComfyUI API 错误: {resp.text[:300]}"
        prompt_id = resp.json()["prompt_id"]

        # WebSocket 实时监听进度
        ws_url = f"ws://localhost:8188/ws?clientId={client_id}"
        latest_step = [0]
        ws_connected = [False]
        ws_error = [None]
        ws_progress = [0, total_steps]  # [当前步, 总步数]
        ws_status = ["loading_model"]   # 当前状态

        def on_ws():
            try:
                ws = websocket.create_connection(ws_url, timeout=10)
                ws_connected[0] = True
                while True:
                    try:
                        msg = json.loads(ws.recv())
                    except:
                        break
                    if msg.get("type") == "progress":
                        d = msg.get("data", {})
                        ws_progress[0] = d.get("value", 0)
                        ws_progress[1] = d.get("max", total_steps)
                        ws_status[0] = "sampling"
                        latest_step[0] = ws_progress[0]
                    elif msg.get("type") == "executing" and msg.get("data", {}).get("node") is None:
                        # 执行完成
                        ws_progress[0] = total_steps
                        ws_status[0] = "done"
                        break
                    elif msg.get("type") == "execution_error":
                        ws_error[0] = msg.get("data", {}).get("exception_message", "执行错误")
                        break
                ws.close()
            except Exception as e:
                ws_error[0] = str(e)

        ws_thread = threading.Thread(target=on_ws, daemon=True)
        ws_thread.start()

        if progress_callback:
            progress_callback(0, total_steps, "loading_model")

        # 根据模型动态设置超时：SANA 5分钟, SDXL 10分钟, Qwen-Image 15分钟
        timeout_minutes = {"sana": 5, "sdxl": 10, "qwen_image": 15}.get(model, 5)
        max_polls = timeout_minutes * 60  # 每1秒轮询一次

        # 轮询等待完成（主线程同步更新进度）
        for _ in range(max_polls):
            time.sleep(1)
            if ws_error[0]:
                return None, f"生成错误: {ws_error[0]}"
            # 主线程同步更新进度（Streamlit 子线程更新 UI 不可靠）
            if progress_callback:
                progress_callback(ws_progress[0], ws_progress[1], ws_status[0])
            try:
                hist = requests.get(f"{comfy_url}/history/{prompt_id}", timeout=10).json()
            except:
                continue
            if prompt_id in hist:
                outputs = hist[prompt_id].get("outputs", {})
                for node_id, node_out in outputs.items():
                    if "images" in node_out:
                        for img in node_out["images"]:
                            img_url = f"{comfy_url}/view?filename={img['filename']}&subfolder={img.get('subfolder','')}&type={img.get('type','output')}"
                            local_path = os.path.join(OUTPUT_IMAGE_GEN, f"{model}_{int(time.time())}.png")
                            img_resp = requests.get(img_url, timeout=30)
                            with open(local_path, "wb") as f:
                                f.write(img_resp.content)
                            return local_path, None
                return None, "ComfyUI 完成但无输出图片"
        return None, f"生成超时（{timeout_minutes}分钟）"
    except requests.exceptions.ConnectionError:
        return None, "ComfyUI 未运行（端口 8188），请先启动 ComfyUI"
    except Exception as e:
        return None, str(e)


def generate_image_diffusers(prompt, model="schnell", width=1024, height=1024, guidance_scale=None, seed=-1):
    """通过 diffusers 生成图片（本地直接调用）"""
    try:
        # FLUX.1-dev 需要 HF 认证，统一用 schnell
        # schnell 4步=快速，12步+guidance=高质量
        model_path = os.path.expanduser("~/ComfyUI/models/unet/FLUX.1-schnell")

        if model == "schnell":
            steps = 4
            guidance = guidance_scale if guidance_scale is not None else 0.0
        else:
            steps = 12
            guidance = guidance_scale if guidance_scale is not None else 3.5

        seed_arg = f"generator=torch.Generator('mps').manual_seed({seed})" if seed >= 0 else ""

        script = f"""
from diffusers import FluxPipeline
import torch, os

pipe = FluxPipeline.from_pretrained('{model_path}', torch_dtype=torch.float16)
pipe.to('mps')
image = pipe(
    '{prompt}',
    num_inference_steps={steps},
    guidance_scale={guidance},
    width={width},
    height={height}{seed_arg}
).images[0]
        output = f'{OUTPUT_IMAGE_GEN}/flux_' + str(int(__import__('time').time())) + '.png'
        os.makedirs(os.path.dirname(output), exist_ok=True)
image.save(output)
print(output)
"""
        result = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=300
        )

        if result.returncode == 0 and result.stdout.strip():
            output_path = result.stdout.strip()
            return output_path, None
        return None, result.stderr[-200:] if result.stderr else "生成失败"
    except Exception as e:
        return None, str(e)


def generate_video_diffusers(prompt):
    """通过 diffusers 生成视频"""
    try:
        script = f'''
import sys
sys.path.insert(0, '{PROJECT_DIR}')
from diffusers import CogVideoXPipeline
from diffusers.utils import export_to_video
import torch

pipe = CogVideoXPipeline.from_pretrained(
    os.path.expanduser('~/ComfyUI/models/CogVideoX-5b'),
    torch_dtype=torch.float16
)
pipe.to('mps')
video = pipe(
    prompt='{prompt}',
    num_frames=16,
    num_inference_steps=20,
    width=480,
    height=320,
).frames[0]
export_to_video(video, f'{OUTPUT_VIDEO_GEN}/output.mp4', fps=8)
print("DONE")
'''
        result = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=600
        )
        if "DONE" in result.stdout:
            return os.path.join(OUTPUT_VIDEO_GEN, "output.mp4"), None
        return None, result.stderr
    except Exception as e:
        return None, str(e)


def generate_video_comfyui(prompt, model_profile="attention16-mlp8-pruned",
                            generation_profile="Turbo 4 Fast", width=864, height=480,
                            duration=5.0, seed=0, progress_callback=None):
    """通过 ComfyUI API 用 MiniMax H3 生成视频（带同步音频）
    progress_callback: func(step:int, total:int, status:str)
    """
    import requests, json, time, random, threading
    import websocket

    comfy_url = "http://localhost:8188"
    client_id = f"streamlit-{random.randint(10000,99999)}"

    # MiniMax H3 要求宽高必须是 32 的倍数，做保护性对齐
    width = (width // 32) * 32
    height = (height // 32) * 32

    if seed == 0:
        seed = random.randint(1, 2**32 - 1)

    os.makedirs(OUTPUT_VIDEO_GEN, exist_ok=True)

    # 预设步数映射（用于进度计算）
    preset_steps = {"Turbo 4 Fast": 5, "Turbo 8 Balanced": 9, "Full 20 Quality": 21}
    total_steps = preset_steps.get(generation_profile, 5)

    # MiniMax H3 MLX workflow
    workflow = {
        "1": {
            "class_type": "MiniMaxH3MLXGenerate",
            "inputs": {
                "prompt": prompt,
                "model_profile": model_profile,
                "generation_profile": generation_profile,
                "memory_mode": "auto",
                "qwen_precision": "prequantized 8-bit",
                "attention": "sol_attn",
                "width": width,
                "height": height,
                "duration_seconds": duration,
                "seed": seed,
                "sol_tau": 1.3,
                "full20_fbc": True,
                "stream_io": "auto"
            }
        },
        "2": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["1", 0],
                "audio": ["1", 1],
                "fps": 24
            }
        },
        "3": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["2", 0],
                "filename_prefix": "minimax_h3",
                "format": "auto",
                "codec": "auto"
            }
        },
        "4": {
            "class_type": "SaveAudio",
            "inputs": {
                "audio": ["1", 1],
                "filename_prefix": "minimax_h3"
            }
        }
    }

    try:
        resp = requests.post(f"{comfy_url}/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=30)
        if resp.status_code != 200:
            return None, f"ComfyUI API 错误: {resp.text[:500]}"
        prompt_id = resp.json()["prompt_id"]

        # WebSocket 实时进度
        ws_url = f"ws://localhost:8188/ws?clientId={client_id}"
        ws_connected = [False]
        ws_error = [None]
        ws_progress = [0, total_steps]  # [当前步, 总步数]
        ws_status = ["loading_model"]  # 当前状态

        def on_ws():
            try:
                ws = websocket.create_connection(ws_url, timeout=10)
                ws_connected[0] = True
                while True:
                    try:
                        msg = json.loads(ws.recv())
                    except:
                        break
                    if msg.get("type") == "progress":
                        d = msg.get("data", {})
                        ws_progress[0] = d.get("value", 0)
                        ws_progress[1] = d.get("max", total_steps)
                        ws_status[0] = "sampling"
                    elif msg.get("type") == "executing" and msg.get("data", {}).get("node") is None:
                        ws_progress[0] = total_steps
                        ws_status[0] = "done"
                        break
                    elif msg.get("type") == "execution_error":
                        ws_error[0] = msg.get("data", {}).get("exception_message", "执行错误")
                        break
                ws.close()
            except Exception as e:
                ws_error[0] = str(e)

        ws_thread = threading.Thread(target=on_ws, daemon=True)
        ws_thread.start()

        if progress_callback:
            progress_callback(0, total_steps, "loading_model")

        # 根据生成模式动态设置超时：Turbo 4 Fast 20分钟, Turbo 8 Balanced 30分钟, Full 20 Quality 45分钟
        timeout_minutes = {"Turbo 4 Fast": 20, "Turbo 8 Balanced": 30, "Full 20 Quality": 45}.get(generation_profile, 20)
        max_polls = timeout_minutes * 60 // 2  # 每2秒轮询一次

        # 轮询等待完成
        for _ in range(max_polls):
            time.sleep(2)
            if ws_error[0]:
                return None, f"生成错误: {ws_error[0]}"
            # 主线程同步更新进度（Streamlit 子线程更新 UI 不可靠）
            if progress_callback:
                progress_callback(ws_progress[0], ws_progress[1], ws_status[0])
            try:
                hist = requests.get(f"{comfy_url}/history/{prompt_id}", timeout=10).json()
            except:
                continue
            if prompt_id in hist:
                outputs = hist[prompt_id].get("outputs", {})
                video_path = None
                audio_path_saved = None
                for node_id, node_out in outputs.items():
                    # SaveVideo 输出：ComfyUI 0.22 用 "images" key + animated=True 表示视频
                    if "images" in node_out:
                        img_info = node_out["images"]
                        if isinstance(img_info, list):
                            for vid in img_info:
                                fname = vid["filename"]
                                # 只下载视频文件（.mp4/.webm/.gif），跳过普通图片
                                if fname.endswith(('.mp4', '.webm', '.gif')):
                                    vid_url = f"{comfy_url}/view?filename={fname}&subfolder={vid.get('subfolder','')}&type={vid.get('type','output')}"
                                    video_path = os.path.join(OUTPUT_VIDEO_GEN, f"minimax_h3_{int(time.time())}.mp4")
                                    vid_resp = requests.get(vid_url, timeout=120, stream=True)
                                    with open(video_path, "wb") as f:
                                        for chunk in vid_resp.iter_content(chunk_size=8192):
                                            f.write(chunk)
                        elif isinstance(img_info, dict) and img_info.get("filename", "").endswith(('.mp4', '.webm', '.gif')):
                            vid_url = f"{comfy_url}/view?filename={img_info['filename']}&subfolder={img_info.get('subfolder','')}&type={img_info.get('type','output')}"
                            video_path = os.path.join(OUTPUT_VIDEO_GEN, f"minimax_h3_{int(time.time())}.mp4")
                            vid_resp = requests.get(vid_url, timeout=120, stream=True)
                            with open(video_path, "wb") as f:
                                for chunk in vid_resp.iter_content(chunk_size=8192):
                                    f.write(chunk)
                    # 兼容旧版 "videos" / "gifs" key
                    elif "videos" in node_out:
                        for vid in node_out["videos"]:
                            vid_url = f"{comfy_url}/view?filename={vid['filename']}&subfolder={vid.get('subfolder','')}&type={vid.get('type','output')}"
                            video_path = os.path.join(OUTPUT_VIDEO_GEN, f"minimax_h3_{int(time.time())}.mp4")
                            vid_resp = requests.get(vid_url, timeout=120, stream=True)
                            with open(video_path, "wb") as f:
                                for chunk in vid_resp.iter_content(chunk_size=8192):
                                    f.write(chunk)
                    # SaveAudio 输出（可能是 .flac 或 .wav）
                    if "audio" in node_out:
                        for aud in node_out["audio"]:
                            aud_ext = os.path.splitext(aud["filename"])[1] or ".wav"
                            aud_url = f"{comfy_url}/view?filename={aud['filename']}&subfolder={aud.get('subfolder','')}&type={aud.get('type','output')}"
                            audio_path_saved = os.path.join(OUTPUT_VIDEO_GEN, f"minimax_h3_{int(time.time())}_audio{aud_ext}")
                            aud_resp = requests.get(aud_url, timeout=60)
                            with open(audio_path_saved, "wb") as f:
                                f.write(aud_resp.content)
                if video_path:
                    return video_path, None
                return None, "ComfyUI 完成但无视频输出"
        return None, f"生成超时（{timeout_minutes}分钟）"
    except requests.exceptions.ConnectionError:
        return None, "无法连接 ComfyUI（端口 8188），请确认服务已启动"
    except Exception as e:
        return None, str(e)


def generate_image_to_video_local(image_path, prompt, duration=5.0,
                                  width=864, height=480, steps=5, seed=0,
                                  profile="attention16-mlp8-pruned",
                                  progress_callback=None):
    """通过 MiniMax H3 MLX 本地图生视频（直接调用 pipeline，绕过 ComfyUI 节点）
    progress_callback: func(step:int, total:int, status:str)
    """
    import random as _rand
    import subprocess as _sp
    import json as _json

    os.makedirs(OUTPUT_VIDEO_GEN, exist_ok=True)

    # MiniMax H3 要求宽高必须是 32 的倍数，做保护性对齐
    width = (width // 32) * 32
    height = (height // 32) * 32

    script_path = os.path.join(os.path.dirname(__file__), ".temp", "i2v_generate.py")
    if not os.path.exists(script_path):
        return None, "图生视频脚本不存在"

    if seed == 0:
        seed = _rand.randint(1, 2**32 - 1)

    output_file = os.path.join(OUTPUT_VIDEO_GEN, f"minimax_i2v_{int(time.time())}.mp4")

    cmd = [
        sys.executable, script_path,
        "--image", image_path,
        "--prompt", prompt,
        "--output", output_file,
        "--width", str(width),
        "--height", str(height),
        "--duration", str(duration),
        "--steps", str(steps),
        "--seed", str(seed),
        "--profile", profile,
    ]

    try:
        if progress_callback:
            progress_callback(0, 100, "loading_model")

        proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True, bufsize=1)
        total_steps = steps
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            # 解析进度
            if "step " in line and "/" in line and progress_callback:
                try:
                    parts = line.split("step ")[1].split("/")
                    cur = int(parts[0])
                    tot = int(parts[0].split()[0]) if " " in parts[0] else int(parts[0])
                    tot = total_steps
                    progress_callback(cur, tot, "sampling")
                except:
                    pass
            elif line.startswith("{"):
                # JSON 输出（最终结果或错误）
                try:
                    result = _json.loads(line)
                    if "error" in result:
                        return None, result["error"]
                    if "video" in result and os.path.exists(result["video"]):
                        if progress_callback:
                            progress_callback(100, 100, "done")
                        return result["video"], None
                except:
                    pass

        proc.wait(timeout=600)
        if proc.returncode != 0:
            return None, f"图生视频进程退出码 {proc.returncode}"

        if os.path.exists(output_file):
            if progress_callback:
                progress_callback(100, 100, "done")
            return output_file, None

        return None, "图生视频完成但未找到输出文件"

    except _sp.TimeoutExpired:
        return None, "图生视频超时（10分钟）"
    except Exception as e:
        return None, str(e)


def recognize_speech(audio_path):
    """语音识别"""
    try:
        result = subprocess.run(
            ["whisper", audio_path, "--model", "base", "--language", "zh", "--output_format", "txt"],
            capture_output=True, text=True, timeout=120
        )
        txt_path = audio_path.rsplit(".", 1)[0] + ".txt"
        if os.path.exists(txt_path):
            with open(txt_path) as f:
                return f.read(), None
        return None, result.stderr
    except Exception as e:
        return None, str(e)


def synthesize_speech(text, voice="zh-CN-XiaoxiaoNeural", rate=0, volume=0, pitch=0):
    """语音合成（edge-tts，使用 Python API 避免依赖 PATH 中的命令行工具）"""
    try:
        import edge_tts
        output_path = os.path.join(OUTPUT_AUDIO_TTS, "tts_output.mp3")
        os.makedirs(OUTPUT_AUDIO_TTS, exist_ok=True)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 构建 edge-tts 参数（Python API 直接调用）
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=f"{rate:+d}%",
            volume=f"{volume:+d}%",
            pitch=f"{pitch:+d}Hz",
        )

        import asyncio
        asyncio.run(communicate.save(output_path))
        if os.path.exists(output_path):
            return output_path, None
        return None, "edge-tts 合成失败：未生成输出文件"
    except Exception as e:
        return None, str(e)


def update_sys_history():
    """更新系统监控历史数据"""
    info = get_system_info()
    now = datetime.now()
    st.session_state.sys_history["cpu"].append(info["cpu_percent"])
    st.session_state.sys_history["memory"].append(info["memory"].percent)
    st.session_state.sys_history["timestamps"].append(now.strftime("%H:%M:%S"))


# ==================== 页面布局 ====================
st.set_page_config(
    page_title="本地 AI 工厂",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 读取版本号
try:
    with open(os.path.join(PROJECT_DIR, "VERSION"), "r") as _vf:
        APP_VERSION = _vf.read().strip()
except:
    APP_VERSION = "unknown"

# ==================== 公网访问登录门 ====================
# 页面内密码登录（公网入口认证），可通过环境变量 AI_FACTORY_PASSWORD 修改
AUTH_PASSWORD = os.environ.get("AI_FACTORY_PASSWORD", "lz781021")

if not st.session_state.get("authed", False):
    st.markdown("""
    <style>
    .auth-wrap { max-width: 380px; margin: 6vh auto 0 auto; padding: 40px 36px;
                 background: #ffffff; border-radius: 16px; border: 1px solid #ececf1;
                 box-shadow: 0 8px 32px rgba(0,0,0,0.06); text-align: center; }
    .auth-icon { font-size: 48px; margin-bottom: 12px; }
    .auth-title { font-size: 22px; font-weight: 700; color: #1a1a2e; margin-bottom: 6px; }
    .auth-sub { font-size: 13px; color: #8e8ea0; margin-bottom: 28px; }
    </style>
    """, unsafe_allow_html=True)
    st.markdown(
        '<div class="auth-wrap"><div class="auth-icon">🏭</div>'
        '<div class="auth-title">本地 AI 工厂</div>'
        '<div class="auth-sub">请输入访问密码后使用</div></div>',
        unsafe_allow_html=True
    )
    with st.form("auth_form"):
        _pw = st.text_input("访问密码", type="password", placeholder="请输入访问密码")
        _submitted = st.form_submit_button("登 录", use_container_width=True)
    if _submitted:
        if _pw == AUTH_PASSWORD:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("密码错误，请重试")
    st.stop()

# ==================== 全局设计系统 ====================
st.markdown("""
<style>
    /* ---- 字体 ---- */
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
    html, body, [class*="css"] {
        font-family: 'Space Grotesk', -apple-system, 'PingFang SC', 'Helvetica Neue', sans-serif;
    }

    /* ---- 设计 Token ---- */
    :root {
        --accent: #7c3aed;
        --accent-2: #ec4899;
        --accent-3: #f59e0b;
        --accent-grad: linear-gradient(135deg, #7c3aed 0%, #ec4899 50%, #f59e0b 100%);
        --accent-grad-soft: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%);
        --accent-tint: #f3e8ff;
        --accent-tint-2: #ede9fe;
        --accent-tint-3: #fdf4ff;
        --ink: #1a1a2e;
        --slate: #4a4a68;
        --ash: #8e8ea0;
        --ash-light: #b8b8c8;
        --paper: #ffffff;
        --canvas: #fafafa;
        --canvas-2: #f5f5f7;
        --line: #ececf1;
        --line-light: #f1f1f6;
        --success: #059669;
        --success-tint: #d1fae5;
        --danger: #ef4444;
        --danger-tint: #fee2e2;
        --warn: #d97706;
        --warn-tint: #fef3c7;
        --radius-sm: 6px;
        --radius: 12px;
        --radius-lg: 16px;
        --shadow-xs: 0 1px 3px rgba(0,0,0,0.03), 0 1px 2px rgba(0,0,0,0.02);
        --shadow-sm: 0 4px 24px rgba(0,0,0,0.05);
        --shadow-hover: 0 8px 32px rgba(124,58,237,0.08);
        --transition: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }

    /* ---- 主区背景 ---- */
    .stApp {
        background: var(--canvas);
    }
    .main .block-container {
        padding-top: 1.5rem;
        max-width: 1320px;
    }

    /* ---------- 侧边栏：极浅紫灰底 + 右边框 ---------- */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #faf9ff 0%, #ffffff 100%);
        border-right: 1px solid var(--line);
    }
    /* 侧边栏文字颜色 */
    section[data-testid="stSidebar"] h1 {
        color: var(--ink) !important;
        font-family: 'Space Grotesk', sans-serif !important;
        font-weight: 700;
        letter-spacing: -0.02em;
        font-size: 1.15rem !important;
    }
    section[data-testid="stSidebar"] h3 {
        color: var(--ash) !important;
        font-size: 0.68rem !important;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        font-weight: 600;
        margin-bottom: 0.3rem;
    }
    section[data-testid="stSidebar"] .stCaption {
        color: var(--slate) !important;
    }
    section[data-testid="stSidebar"] hr {
        border: none;
        height: 1px;
        background: var(--line);
        margin: 0.5rem 0;
    }
    /* 导航项：去掉默认圆点，用左竖线标记选中 */
    section[data-testid="stSidebar"] .stRadio > div {
        gap: 0;
    }
    section[data-testid="stSidebar"] .stRadio label {
        padding: 6px 0 6px 12px;
        border-left: 3px solid transparent;
        transition: var(--transition);
        font-size: 0.87rem;
        color: var(--slate);
        border-radius: 0;
    }
    section[data-testid="stSidebar"] .stRadio label:hover {
        background: var(--canvas-2);
        color: var(--ink);
    }
    section[data-testid="stSidebar"] .stRadio label[data-selected="true"] {
        color: var(--accent) !important;
        font-weight: 600;
        border-left-color: transparent;
        background: var(--accent-tint-2);
        border-radius: 8px;
    }
    /* 导航选中态：渐变竖线装饰（覆盖border-left实现渐变） */
    section[data-testid="stSidebar"] .stRadio label[data-selected="true"]::before {
        content: '';
        position: absolute;
        left: 2px;
        top: 50%;
        transform: translateY(-50%);
        width: 3px;
        height: 55%;
        background: var(--accent-grad);
        border-radius: 2px;
    }
    section[data-testid="stSidebar"] .stRadio label {
        position: relative;
    }
    /* 隐藏 radio 默认圆点 */
    section[data-testid="stSidebar"] .stRadio input[type="radio"] {
        display: none;
    }
    section[data-testid="stSidebar"] .stRadio label span:first-child {
        display: none;
    }

    /* ---------- 页面标题：左侧teal竖线 ---------- */
    [data-testid="stHeadingWithActionElements"] {
        position: relative;
        padding-left: 14px !important;
    }
    [data-testid="stHeadingWithActionElements"]::before {
        content: '';
        position: absolute;
        left: 0;
        top: 50%;
        transform: translateY(-50%);
        width: 5px;
        height: 60%;
        background: var(--accent-grad);
        border-radius: 3px;
    }
    [data-testid="stHeadingWithActionElements"] h1 {
        font-family: 'Space Grotesk', sans-serif !important;
        font-weight: 700;
        letter-spacing: -0.02em;
    }

    /* ---------- Metric 卡片：顶部渐变条 + 淡色底 + hover上浮 ---------- */
    div[data-testid="stMetric"] {
        background: var(--paper);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        padding: 16px 18px;
        box-shadow: var(--shadow-xs);
        transition: var(--transition);
        position: relative;
        overflow: hidden;
    }
    div[data-testid="stMetric"]:hover {
        border-color: var(--ash-light);
        box-shadow: var(--shadow-hover);
        transform: translateY(-2px);
    }
    /* 顶部渐变条 + 淡色底（统一紫粉橙暖调） */
    div[data-testid="stMetric"]::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 3px;
        background: linear-gradient(90deg, #7c3aed, #a855f7);
        border-radius: var(--radius) var(--radius) 0 0;
        z-index: 1;
    }
    div[data-testid="stMetric"]::after {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: linear-gradient(180deg, rgba(124,58,237,0.05) 0%, rgba(255,255,255,0) 55%);
        pointer-events: none;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(2) div[data-testid="stMetric"]::before {
        background: linear-gradient(90deg, #ec4899, #f59e0b);
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(2) div[data-testid="stMetric"]::after {
        background: linear-gradient(180deg, rgba(236,72,153,0.05) 0%, rgba(255,255,255,0) 55%);
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(3) div[data-testid="stMetric"]::before {
        background: linear-gradient(90deg, #f59e0b, #fb923c);
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(3) div[data-testid="stMetric"]::after {
        background: linear-gradient(180deg, rgba(245,158,11,0.05) 0%, rgba(255,255,255,0) 55%);
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(4) div[data-testid="stMetric"]::before {
        background: linear-gradient(90deg, #fb923c, #ef4444);
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(4) div[data-testid="stMetric"]::after {
        background: linear-gradient(180deg, rgba(251,146,60,0.05) 0%, rgba(255,255,255,0) 55%);
    }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        position: relative;
        z-index: 1;
    }
    div[data-testid="stMetric"] label,
    div[data-testid="stMetric"] div[data-testid="stMetricDelta"] {
        position: relative;
        z-index: 1;
    }
    div[data-testid="stMetric"] label {
        font-size: 0.7rem !important;
        color: var(--ash) !important;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 1.6rem !important;
        font-weight: 700;
        color: var(--ink);
        margin: 4px 0;
        white-space: nowrap;
        background: linear-gradient(135deg, #7c3aed, #a855f7);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(2) div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        background: linear-gradient(135deg, #ec4899, #f59e0b);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(3) div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        background: linear-gradient(135deg, #f59e0b, #fb923c);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(4) div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        background: linear-gradient(135deg, #fb923c, #ef4444);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    div[data-testid="stMetric"] div[data-testid="stMetricDelta"] {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.72rem !important;
        color: var(--slate);
    }

    /* ---------- 按钮 ---------- */
    .stButton > button {
        border-radius: var(--radius);
        font-weight: 500;
        font-size: 0.87rem;
        transition: var(--transition);
        border: 1px solid var(--line);
    }
    .stButton > button[kind="secondary"] {
        background: var(--paper);
        color: var(--slate);
    }
    .stButton > button[kind="secondary"]:hover {
        background: var(--canvas-2);
        border-color: var(--ash-light);
        color: var(--ink);
    }
    .stButton > button[kind="primary"] {
        background: var(--accent-grad-soft);
        border: 1px solid transparent;
        color: white;
        box-shadow: 0 1px 2px rgba(124,58,237,0.15);
    }
    .stButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #6d28d9 0%, #a855f7 100%);
        border-color: transparent;
        box-shadow: 0 4px 16px rgba(124,58,237,0.25);
    }

    /* ---------- 输入框/选择框 ---------- */
    .stTextInput > div > input,
    .stTextArea > div > textarea,
    .stSelectbox > div > div {
        border-radius: var(--radius) !important;
        border-color: var(--line) !important;
        transition: var(--transition);
    }
    .stTextInput > div > input:focus,
    .stTextArea > div > textarea:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-tint) !important;
    }

    /* ---------- 聊天消息 ---------- */
    .stChatMessage {
        border-radius: var(--radius-lg) !important;
    }

    /* ---------- Expander ---------- */
    div[data-testid="stExpander"] {
        border: 1px solid var(--line);
        border-radius: var(--radius) !important;
        overflow: hidden;
        background: var(--paper);
        box-shadow: var(--shadow-xs);
    }
    .streamlit-expanderHeader {
        font-size: 0.87rem;
        font-weight: 500;
        background: var(--paper);
    }

    /* ---------- 容器(border=True) ---------- */
    .stContainer[data-testid="stVerticalBlockBorderWrapper"] {
        border: 1px solid var(--line) !important;
        border-radius: var(--radius) !important;
        background: var(--paper) !important;
        box-shadow: var(--shadow-xs);
        transition: var(--transition);
    }
    .stContainer[data-testid="stVerticalBlockBorderWrapper"]:hover {
        border-color: var(--ash-light) !important;
        box-shadow: var(--shadow-sm);
    }

    /* ---------- Tabs ---------- */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        border-bottom: 1px solid var(--line);
        background: var(--paper);
        border-radius: var(--radius) var(--radius) 0 0;
        padding: 0 4px;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 7px 16px;
        font-size: 0.85rem;
        border-radius: 0;
        transition: var(--transition);
    }
    .stTabs [data-baseweb="tab"]:hover {
        background: var(--canvas-2);
    }
    .stTabs [aria-selected="true"] {
        color: var(--accent);
        font-weight: 600;
        border-bottom: 2px solid var(--accent);
        background: transparent;
    }
    .stTabs [aria-selected="false"] {
        color: var(--slate);
    }

    /* ---------- 状态提示 ---------- */
    .stAlert {
        border-radius: var(--radius) !important;
        border: 1px solid var(--line) !important;
    }
    div[data-testid="stAlert-container"][data-btntype="success"] {
        background: var(--success-tint) !important;
        border-color: rgba(5,150,105,0.2) !important;
    }
    div[data-testid="stAlert-container"][data-btntype="error"] {
        background: var(--danger-tint) !important;
        border-color: rgba(220,38,38,0.2) !important;
    }
    div[data-testid="stAlert-container"][data-btntype="warning"] {
        background: var(--warn-tint) !important;
        border-color: rgba(217,119,6,0.2) !important;
    }

    /* ---------- 标题 ---------- */
    h1 {
        font-family: 'Space Grotesk', sans-serif !important;
        font-weight: 700;
        letter-spacing: -0.02em;
    }
    h2, [data-testid="stSubheading"] {
        font-family: 'Space Grotesk', sans-serif !important;
        font-weight: 600;
        letter-spacing: -0.01em;
    }
    h3 {
        font-weight: 600;
        color: var(--ink);
    }

    /* ---------- 滚动条 ---------- */
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #d4d4d8; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--ash); }

    /* ---------- 链接 ---------- */
    a { color: var(--accent); text-decoration: none; transition: var(--transition); }
    a:hover { color: #6d28d9; text-decoration: underline; }

    /* ---------- 代码块 ---------- */
    .stCodeBlock {
        border-radius: var(--radius);
    }
    code {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.82rem;
    }

    /* ---------- DataFrame ---------- */
    .stDataFrame {
        border-radius: var(--radius);
        overflow: hidden;
        border: 1px solid var(--line);
    }

    /* ---------- 分隔线 ---------- */
    .stDivider > hr, hr {
        border: none !important;
        height: 1px !important;
        background: var(--line) !important;
    }

    /* ---------- Markdown 表格 ---------- */
    .stMarkdown table {
        border-radius: var(--radius);
        overflow: hidden;
        border: 1px solid var(--line);
    }
    .stMarkdown table th {
        background: var(--canvas-2);
        font-weight: 600;
        font-size: 0.82rem;
        color: var(--ink);
    }
    .stMarkdown table td {
        font-size: 0.83rem;
    }
    .stMarkdown table tr:hover td {
        background: var(--accent-tint-2);
    }

    /* ---------- 聊天输入框 ---------- */
    .stChatInput > div {
        border-radius: var(--radius-lg) !important;
    }

    /* ---------- 文件上传 ---------- */
    .stFileUploader > div {
        border-radius: var(--radius) !important;
        border-color: var(--line) !important;
    }

    /* ---------- 进度条 ---------- */
    .stProgress > div > div {
        background: var(--accent-grad-soft);
    }

    /* ---------- 选择框下拉菜单 ---------- */
    div[data-baseweb="select"] > div {
        border-radius: var(--radius) !important;
    }

    /* ---------- 标题副标题间距 ---------- */
    .stTitle + .stCaption {
        margin-top: -0.3rem;
        color: var(--ash);
        font-size: 0.82rem;
    }

    /* ---------- 数字输入框 ---------- */
    .stNumberInput > div > div > input {
        font-family: 'JetBrains Mono', monospace !important;
    }

    /* ---------- 微调: 减少默认间距 ---------- */
    .stMarkdown {
        margin-bottom: 0.3rem;
    }

    /* ================= 组件级渐变皮肤（全页面） ================= */

    /* ---------- 选择框/多选框：渐变下拉、紫色选中 ---------- */
    .stSelectbox [data-rac][role="group"] > input[role="combobox"],
    .stMultiSelect [data-rac][role="group"] > input[role="combobox"],
    .stTextInput [data-rac] > input,
    .stTextArea textarea {
        border-radius: var(--radius) !important;
        border-color: var(--line) !important;
        transition: var(--transition);
        background: var(--paper) !important;
    }
    .stSelectbox [data-rac][role="group"]:hover input,
    .stMultiSelect [data-rac][role="group"]:hover input,
    .stTextInput [data-rac]:hover input {
        border-color: rgba(124,58,237,0.4) !important;
    }
    .stSelectbox [data-rac][role="group"]:focus-within input,
    .stMultiSelect [data-rac][role="group"]:focus-within input,
    .stTextInput [data-rac]:focus-within input {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-tint) !important;
    }
    /* 下拉面板选项选中态 */
    [role="option"][aria-selected="true"],
    li[role="option"]:active {
        background: var(--accent-tint-2) !important;
        color: var(--accent) !important;
    }
    [data-baseweb="popover"] li[role="option"]:hover,
    li[role="option"]:hover {
        background: var(--accent-tint) !important;
    }
    /* 选择框下拉箭头：紫色（替代默认深灰） */
    .stSelectbox [data-rac] svg,
    .stMultiSelect [data-rac] svg {
        color: var(--accent) !important;
        fill: var(--accent) !important;
    }

    /* ---------- 滑动条：渐变轨道（React Aria 结构） ---------- */
    div[data-testid="stSlider"] div[data-testid="stSliderThumbValue"] {
        color: var(--accent) !important;
        font-weight: 600;
    }
    /* 滑块 thumb：紫色渐变圆形 */
    div[data-testid="stSlider"] div[data-rac][style*="position: absolute"] {
        background: var(--accent-grad-soft) !important;
        border: 2px solid #ffffff !important;
        box-shadow: 0 2px 8px rgba(124,58,237,0.4) !important;
    }
    /* 滑块轨道填充：紫粉渐变（React Aria 轨道为 emotion class e23vpic5，
       背景含动态百分比断点，需精确覆盖；同时保留内联样式兜底） */
    div[data-testid="stSlider"] div[class*="e23vpic5"] {
        background-image: linear-gradient(90deg, #7c3aed 0%, #ec4899 50%, #f59e0b 100%) !important;
    }
    div[data-testid="stSlider"] div[style*="background"] {
        background: linear-gradient(90deg, #7c3aed 0%, #a855f7 100%) !important;
    }

    /* ---------- 复选框：选中态紫色 ---------- */
    .stCheckbox [data-testid="stCheckbox"] label span[aria-checked="true"] {
        background: var(--accent) !important;
        border-color: var(--accent) !important;
    }
    .stCheckbox [data-testid="stCheckbox"] label span[aria-checked="true"] svg {
        color: white !important;
    }

    /* ---------- 聊天消息气泡：用户渐变底、助手白底 ---------- */
    .stChatMessage[data-testid="stChatMessage"] {
        border-radius: var(--radius-lg) !important;
        border: 1px solid var(--line);
        background: var(--paper);
        box-shadow: var(--shadow-xs);
    }
    .stChatMessage [data-testid="stChatMessageContent"] p {
        font-size: 0.9rem;
    }
    /* 用户消息渐变底 */
    div[data-testid="stChatMessage"][data-testid="stChatMessageAvatarUser"] {
        background: linear-gradient(135deg, var(--accent-tint-2) 0%, var(--accent-tint-3) 100%);
        border-color: rgba(124,58,237,0.15);
    }

    /* ---------- 聊天输入框：渐变边框聚焦 ---------- */
    div[data-testid="stChatInput"] {
        border-radius: var(--radius-lg) !important;
        border-color: var(--line) !important;
        background: var(--paper) !important;
        box-shadow: var(--shadow-xs);
        transition: var(--transition);
    }
    div[data-testid="stChatInput"]:focus-within {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-tint) !important;
    }
    /* 发送按钮：渐变底 */
    div[data-testid="stChatInput"] button[data-testid="stChatInputSubmitButton"]:not([disabled]) {
        background: var(--accent-grad-soft) !important;
        color: white !important;
        border: none !important;
    }

    /* ---------- Expander 头部 hover 渐变 ---------- */
    .streamlit-expanderHeader:hover {
        color: var(--accent) !important;
    }

    /* ---------- 文件上传：拖拽区渐变边框 ---------- */
    .stFileUploader > div:hover {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-tint) !important;
    }

    /* ---------- 数据帧表格：表头浅紫底 ---------- */
    .stDataFrame thead tr th {
        background: var(--accent-tint-2) !important;
        color: var(--slate) !important;
        font-weight: 600 !important;
    }

    /* ---------- 数字输入：聚焦紫光 ---------- */
    .stNumberInput > div > div > input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-tint) !important;
    }

    /* ---------- st.status 完成态徽章：浅紫渐变底 + 深紫文字 ---------- */
    details summary:has([data-testid="stExpanderIconCheck"]) {
        background: linear-gradient(135deg, var(--accent-tint-2), var(--accent-tint-3)) !important;
        border-radius: var(--radius) !important;
        padding: 6px 12px !important;
        color: var(--accent) !important;
        font-weight: 600 !important;
        border: 1px solid var(--accent-tint) !important;
        transition: var(--transition);
    }
    details summary:has([data-testid="stExpanderIconCheck"]):hover {
        border-color: var(--accent) !important;
        box-shadow: 0 2px 8px rgba(124,58,237,0.15) !important;
    }

    /* ---------- 指标卡片 hover 通用（非系统监控页） ---------- */
    div[data-testid="stMetric"] {
        border: 1px solid var(--line);
        border-radius: var(--radius);
        box-shadow: var(--shadow-xs);
        transition: var(--transition);
        position: relative;
        overflow: hidden;
    }
    div[data-testid="stMetric"]:hover {
        border-color: var(--ash-light);
        box-shadow: var(--shadow-hover);
        transform: translateY(-2px);
    }

    /* ---------- 状态徽章统一（st.success / st.error / st.warning / st.info） ---------- */
    div[data-testid="stAlert-container"] {
        border-radius: var(--radius) !important;
        border: 1px solid var(--line) !important;
    }
    div[data-testid="stAlert-container"][data-btntype="success"] {
        background: var(--success-tint) !important;
    }
    div[data-testid="stAlert-container"][data-btntype="error"] {
        background: var(--danger-tint) !important;
    }
    div[data-testid="stAlert-container"][data-btntype="warning"] {
        background: var(--warn-tint) !important;
    }
    div[data-testid="stAlert-container"][data-btntype="info"] {
        background: var(--accent-tint-2) !important;
    }
</style>
""", unsafe_allow_html=True)

# ==================== 移动端自适应（≤768px 手机） ====================
st.markdown("""
<style>
@media (max-width: 768px) {
    /* 主内容区收窄内边距 */
    .block-container {
        padding: 1rem 0.9rem 2rem !important;
        max-width: 100% !important;
    }
    /* 多列布局强制单列堆叠 */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
    [data-testid="stHorizontalBlock"] > div {
        flex: 1 1 100% !important;
        min-width: 100% !important;
        width: 100% !important;
    }
    /* 侧边栏抽屉宽度 */
    [data-testid="stSidebar"] {
        width: 290px !important;
    }
    /* 侧边栏长文本自动换行，避免截断 */
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] div {
        overflow-wrap: break-word !important;
        word-break: break-word !important;
    }
    /* 标题字号适配 */
    h1 { font-size: 1.35rem !important; }
    h2 { font-size: 1.15rem !important; }
    h3 { font-size: 1.02rem !important; }
    /* 按钮全宽方便触控 */
    .stButton > button,
    .stDownloadButton > button,
    .stFormSubmitButton > button {
        width: 100% !important;
    }
    /* 指标卡片紧凑 */
    div[data-testid="stMetric"] {
        padding: 0.6rem 0.8rem !important;
    }
    /* 隐藏装饰性顶栏，让出空间 */
    div[data-testid="stDecoration"] {
        display: none !important;
    }
}
</style>
""", unsafe_allow_html=True)


# ==================== 侧边栏 ====================
with st.sidebar:
    # 品牌区：精致的 logo + 标题 + 副标题
    st.markdown("""
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:2px;">
        <div style="width:36px;height:36px;border-radius:12px;background:linear-gradient(135deg,#7c3aed,#ec4899 50%,#f59e0b);display:flex;align-items:center;justify-content:center;font-size:1.1rem;flex-shrink:0;box-shadow:0 4px 12px rgba(124,58,237,0.25);">
            🏭
        </div>
        <div>
            <div style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:1.05rem;color:#1a1a2e;letter-spacing:-0.02em;line-height:1.2;">AI 工厂</div>
            <div style="font-size:0.72rem;color:#8e8ea0;font-weight:500;line-height:1.2;">李准的星小辰</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.caption(f"M5 Max · 137GB · v{APP_VERSION}")
    st.markdown("<hr/>", unsafe_allow_html=True)

    page = st.radio(
        "导航",
        ["📊 系统监控", "🧠 文本对话", "🔬 模型对比",
         "👁️ 图片理解", "🎨 图片生成",
         "🎥 视频理解", "🎬 视频生成",
         "🎤 语音识别", "🔊 语音合成",
         "📚 智能问答", "📈 Token 统计",
         "📋 日志查看", "📖 AI工厂说明"],
        index=0,
        label_visibility="collapsed"
    )

    st.markdown("<hr/>", unsafe_allow_html=True)

    # 服务状态
    st.markdown("<h3>服务状态</h3>", unsafe_allow_html=True)
    services = get_service_status()
    for name, info in services.items():
        icon = "●" if info["status"] == "online" else "○"
        color = "#7c3aed" if info["status"] == "online" else "#b8b8c8"
        model_str = f" <span style='font-size:0.68rem;color:#8e8ea0'>{info['model']}</span>" if info.get("model") else ""
        st.markdown(f"<div style='font-size:0.78rem;color:#4a4a68;padding:2px 0;'><span style='color:{color};font-size:0.6rem;'>{icon}</span> {name}{model_str}</div>", unsafe_allow_html=True)

    # 智能问答同步信息
    sync_info = get_last_sync_info()
    st.markdown("<hr/>", unsafe_allow_html=True)
    st.markdown("<h3>智能问答</h3>", unsafe_allow_html=True)
    if sync_info["time"]:
        st.markdown(f"<div style='font-size:0.78rem;color:#4a4a68;'>同步于 {sync_info['time'][:16]}</div>", unsafe_allow_html=True)
        st.caption(sync_info["result"])
    else:
        st.caption("暂无同步记录")


# ==================== 系统监控页 ====================
if page == "📊 系统监控":
    st.title("📊 系统监控")

    # 局部自动刷新区域：指标卡片+图表+服务状态在后台定时刷新，不触发全页 rerun
    @st.fragment(run_every=10)
    def _monitor_dashboard():
        update_sys_history()
        local_info = get_system_info()

        # 指标卡片
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("CPU 使用率", f"{local_info['cpu_percent']:.1f}%",
                       f"{psutil.cpu_count(logical=True)} 逻辑核心")
        with col2:
            st.metric("内存使用", f"{local_info['memory'].percent:.1f}%",
                       f"{local_info['memory'].used / 1024**3:.1f} / {local_info['memory'].total / 1024**3:.1f} GB")
        with col3:
            st.metric("磁盘使用", f"{local_info['disk'].percent:.1f}%",
                       f"{local_info['disk'].used / 1024**3:.0f} / {local_info['disk'].total / 1024**3:.0f} GB")
        with col4:
            if local_info["gpu"] and local_info["gpu"].get("available"):
                st.metric("GPU/MPS", "✅ 可用", "Apple Silicon")
            else:
                st.metric("GPU/MPS", "❌ 不可用")

        st.divider()

        # 实时图表
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("CPU 使用率趋势")
            fig_cpu = go.Figure()
            fig_cpu.add_trace(go.Scatter(
                y=list(st.session_state.sys_history["cpu"]),
                mode='lines+markers', name='CPU',
                line=dict(color='#7c3aed', width=2),
                fill='tozeroy', fillcolor='rgba(124,58,237,0.06)'
            ))
            fig_cpu.update_layout(yaxis_range=[0, 100], height=300,
                margin=dict(l=20, r=20, t=20, b=20),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig_cpu, use_container_width=True, key="frag_cpu_chart")

        with c2:
            st.subheader("内存使用趋势")
            fig_mem = go.Figure()
            fig_mem.add_trace(go.Scatter(
                y=list(st.session_state.sys_history["memory"]),
                mode='lines+markers', name='内存',
                line=dict(color='#ec4899', width=2),
                fill='tozeroy', fillcolor='rgba(236,72,153,0.06)'
            ))
            fig_mem.update_layout(yaxis_range=[0, 100], height=300,
                margin=dict(l=20, r=20, t=20, b=20),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig_mem, use_container_width=True, key="frag_mem_chart")

        st.divider()

        # 服务状态
        st.subheader("🔧 服务状态")
        frag_services = get_service_status()
        for name, info in frag_services.items():
            sc1, sc2, sc3 = st.columns([3, 1, 1])
            with sc1:
                st.write(f"**{name}**")
                if info.get("model"):
                    st.markdown(f"<span style='font-size:0.9em;color:#7c3aed'>{info['model']}</span>", unsafe_allow_html=True)
            with sc2:
                if info["status"] == "online":
                    st.markdown("<div style='background:linear-gradient(135deg,#ede9fe,#f5f3ff);color:#6d28d9;border:1px solid rgba(124,58,237,0.2);border-radius:8px;padding:4px 12px;text-align:center;font-size:0.82rem;font-weight:600;display:inline-block;'>● 运行中</div>", unsafe_allow_html=True)
                else:
                    st.markdown("<div style='background:#f5f5f7;color:#8e8ea0;border:1px solid #ececf1;border-radius:8px;padding:4px 12px;text-align:center;font-size:0.82rem;font-weight:500;display:inline-block;'>离线</div>", unsafe_allow_html=True)
            with sc3:
                st.caption(f"端口 {info['port']}")

        st.divider()

        # 智能问答同步信息
        st.subheader("📚 智能问答同步")
        sync_info = get_last_sync_info()
        sc_a, sc_b = st.columns([3, 5])
        with sc_a:
            st.write("**上次同步**")
            if sync_info["time"]:
                st.metric("同步时间", sync_info["time"])
            else:
                st.info("暂无同步记录")
        with sc_b:
            if sync_info["result"]:
                st.write("**同步结果**")
                st.metric("同步状态", sync_info["result"])
            else:
                st.write("**同步结果**")
                st.info("暂无同步记录")
        st.subheader("🔄 运行中的模型进程")
        frag_processes = get_process_info()
        if frag_processes:
            for proc in frag_processes:
                pc1, pc2, pc3 = st.columns([1, 2, 1])
                with pc1:
                    st.caption(f"PID: {proc['pid']}")
                with pc2:
                    st.caption(proc['cmdline'][:60])
                with pc3:
                    st.caption(f"{proc['memory_mb']:.0f} MB")
        else:
            st.info("没有检测到模型相关进程")

    _monitor_dashboard()

    # 不再有 time.sleep + st.rerun()，避免右上角奔跑小人动画


# ==================== 文本对话页 ====================
elif page == "🧠 文本对话":
    st.title("🧠 文本对话")

    # 模型选择
    llm_models = {
        "Qwen3.8-27B-4bit (轻量主力)": "/Users/lizhun/Desktop/星小辰工作空间/models/mlx-lm/Qwen3.8-27B-4bit",
        "Qwen3.6-35B-A3B-bf16 (快速)": "/Users/lizhun/Desktop/星小辰工作空间/models/mlx-lm/Qwen3.6-35B-A3B-bf16",
        "gemma4:12b (Ollama，仅中文)": "ollama:gemma4:12b",
    }
    selected_llm = st.selectbox("选择模型", list(llm_models.keys()))
    current_model = llm_models[selected_llm]
    st.caption(f"模型路径: {current_model}")

    # 初始化聊天历史
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [{
            "role": "system",
            "content": "你是一个有用的AI助手，基于本地大模型运行。请用中文回答。"
        }]

    # 参数设置
    with st.expander("⚙️ 参数设置"):
        col1, col2, col3 = st.columns(3)
        with col1:
            temperature = st.slider(
                "温度 (Temperature)", 0.0, 2.0, 0.7, 0.1,
                help="越高越随机，越低越确定"
            )
        with col2:
            max_tokens = st.slider("最大 Token 数", 64, 4096, 1024, 64, help="生成的最大长度")
        with col3:
            top_p = st.slider("Top P", 0.0, 1.0, 0.9, 0.05, help="核采样参数")

        system_prompt = st.text_area("系统提示词", value=st.session_state.chat_history[0]["content"], height=68)
        if system_prompt != st.session_state.chat_history[0]["content"]:
            st.session_state.chat_history[0]["content"] = system_prompt

    # 显示聊天历史
    for msg in st.session_state.chat_history:
        if msg["role"] != "system":
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

    # 用户输入
    if prompt := st.chat_input("输入消息..."):
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        with st.chat_message("assistant"):
            with st.spinner(f"思考中（{selected_llm.split()[0]}）..."):
                start_time = time.time()
                # 如果选择的是 Qwen3.8-27B（已加载在 8082），直接走 API；否则用本地调用指定模型路径
                if "Qwen3.8-27B" in selected_llm:
                    response, usage = call_llm_api(st.session_state.chat_history, max_tokens=max_tokens)
                else:
                    response, usage = call_llm_api(
                        st.session_state.chat_history,
                        max_tokens=max_tokens,
                        model_path=current_model
                    )
                elapsed = time.time() - start_time

                st.write(response)

                # 更新 Token 统计
                tokens_used = usage.get("total_tokens", 0)
                st.session_state.token_stats["total_tokens"] += tokens_used
                st.session_state.token_stats["total_requests"] += 1
                st.session_state.token_stats["history"].append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "tokens": tokens_used,
                    "elapsed": elapsed
                })

                if tokens_used > 0:
                    tok_per_sec = tokens_used / elapsed if elapsed > 0 else 0
                    st.caption(f"⏱️ {elapsed:.1f}s | 📝 {tokens_used} tokens | ⚡ {tok_per_sec:.1f} tok/s")

                # 记录操作日志
                log_activity("文本对话", f"模型={selected_llm.split()[0]} | 输入={prompt[:100]} | tokens={tokens_used}", duration=elapsed)

        st.session_state.chat_history.append({"role": "assistant", "content": response})

    # 清空对话按钮
    if st.button("🗑️ 清空对话"):
        st.session_state.chat_history = [{
            "role": "system",
            "content": "你是一个有用的AI助手，基于本地大模型运行。请用中文回答。"
        }]
        st.rerun()


# ==================== 模型对比页 ====================
# ==================== 模型对比页 ====================
elif page == "🔬 模型对比":
    st.title("🔬 模型对比")
    st.caption("选择多个模型进行多维度测试，对比性能和输出质量")

    # ---- 共享状态：st.cache_resource 保证跨 rerun 为同一对象，
    #      后台线程通过持有引用读写（线程不能访问 st.session_state）----
    _MC = _mc_store()

    # 所有可用模型
    all_models = {
        "Qwen3.8-27B (mlx-lm)": "/Users/lizhun/Desktop/星小辰工作空间/models/mlx-lm/Qwen3.8-27B-4bit",
        "Qwen3.6-35B (mlx-lm)": "/Users/lizhun/Desktop/星小辰工作空间/models/mlx-lm/Qwen3.6-35B-A3B-bf16",
        "Xing4.0 (星辰语义大模型)": "xing4.0",
        "gemma-4-12B (Ollama)": "ollama:gemma4:12b",
    }

    # 预设测试类型（全部自动执行，去掉自定义）
    test_presets = {
        "🧠 知识问答": "请解释量子计算的基本原理，以及它与经典计算的主要区别。用通俗易懂的语言，约200字。",
        "💻 代码生成": "用 Python 编写一个函数，实现对列表中重复元素的去重，并保持原有顺序。包含注释和测试用例。",
        "🔍 逻辑推理": "小明有5个苹果，给了小红2个，又买了3个。小明的妈妈拿走了小明一半的苹果。请问小明现在有几个苹果？请一步步推理。",
        "✍️ 创意写作": "以'最后一个程序员'为题，写一个100字左右的科幻微小说，要有反转。",
        "📊 数据分析": "一家公司2024年各季度营收为：Q1 1200万、Q2 1500万、Q3 1800万、Q4 2100万。请分析增长趋势，预测2025年Q1营收，并给出理由。",
        "🌐 多语言翻译": "将以下中文翻译成英文、日文和法文：'人工智能正在改变我们的生活方式，从医疗健康到教育娱乐，无处不在。'",
        "🎯 指令遵循": "请严格按照以下格式输出：\n1. 第一行输出你的名字\n2. 第二行输出今天是星期几\n3. 第三行用JSON格式输出 {\"color\": \"你最喜欢的颜色\"}",
    }

    # 后台线程执行对比测试，写入模块级 _MC，切换菜单后再回来进度与结果仍保留
    def _run_compare_worker(params):
        try:
            presets = params["test_presets"]
            sel_models = params["selected_models"]
            run_count = params["run_count"]
            max_tokens = params["max_tokens"]
            num_tests = len(presets)
            total = len(sel_models) * num_tests * run_count
            _MC["total"] = total
            _MC["current"] = 0
            all_test_results = {}
            all_perf_data = []
            current = 0
            system_prompt = "你是一个有用的AI助手。请用中文回答。"
            for test_name, test_prompt in presets.items():
                test_results = []
                for model_name in sel_models:
                    model_path = all_models[model_name]
                    model_runs = []
                    for run_idx in range(run_count):
                        current += 1
                        _MC["current"] = current
                        _MC["status"] = f"[{current}/{total}] {test_name} → {model_name} (第 {run_idx+1} 次)"
                        messages = [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": test_prompt}
                        ]
                        start_time = time.time()
                        response, usage = call_llm_api(messages, max_tokens=max_tokens, model_path=model_path)
                        elapsed = time.time() - start_time
                        completion_tokens = usage.get("completion_tokens", 0)
                        total_tokens = usage.get("total_tokens", 0)
                        prompt_tokens = usage.get("prompt_tokens", 0)
                        tok_per_sec = completion_tokens / elapsed if elapsed > 0 and completion_tokens > 0 else 0
                        model_runs.append({
                            "elapsed": elapsed,
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": total_tokens,
                            "tok_per_sec": tok_per_sec,
                            "response": response
                        })
                    avg_elapsed = sum(r["elapsed"] for r in model_runs) / len(model_runs)
                    avg_tok_per_sec = sum(r["tok_per_sec"] for r in model_runs) / len(model_runs)
                    avg_completion = sum(r["completion_tokens"] for r in model_runs) / len(model_runs)
                    result_entry = {
                        "model": model_name,
                        "elapsed": avg_elapsed,
                        "tok_per_sec": avg_tok_per_sec,
                        "completion_tokens": avg_completion,
                        "response": model_runs[0]["response"],
                    }
                    test_results.append(result_entry)
                    all_perf_data.append({
                        "model": model_name,
                        "test": test_name,
                        "elapsed": avg_elapsed,
                        "tok_per_sec": avg_tok_per_sec,
                        "completion_tokens": avg_completion,
                    })
                all_test_results[test_name] = test_results
            _MC["all_test_results"] = all_test_results
            _MC["all_perf_data"] = all_perf_data
            _MC["status"] = "✅ 全部测试完成！"
            _MC["result_ready"] = True
        except Exception as e:
            _MC["status"] = f"❌ 测试出错: {e}"
            _MC["all_test_results"] = None
            _MC["all_perf_data"] = None
        finally:
            _MC["running"] = False

    # 选择要对比的模型
    selected_models = st.multiselect(
        "选择要对比的模型（可多选）",
        list(all_models.keys()),
        default=list(all_models.keys())[:2]
    )

    if not selected_models:
        st.warning("请至少选择一个模型")
        st.stop()

    # 高级设置
    with st.expander("⚙️ 高级设置"):
        col1, col2 = st.columns(2)
        with col1:
            max_tokens = st.slider("最大输出 Token 数", 64, 2048, 512, 64)
        with col2:
            run_count = st.number_input("每个模型每项测试次数", 1, 3, 1, help="多次测试取平均值更准确")

    num_tests = len(test_presets)
    total_tests = len(selected_models) * num_tests * run_count
    est_min = total_tests * 20 // 60
    est_max = total_tests * 45 // 60
    st.info(f"📋 将自动执行 {num_tests} 项测试 × {len(selected_models)} 个模型 × {run_count} 次 = {total_tests} 次调用，预计 {est_min}-{est_max} 分钟")

    # 测试项说明
    with st.expander("📋 测试项说明（共7项）"):
        test_descs = {
            "🧠 知识问答": "解释量子计算原理，考察知识广度和表达准确性",
            "💻 代码生成": "Python 去重函数，考察代码质量和注释规范",
            "🔍 逻辑推理": "苹果数量推理题，考察分步推理能力",
            "✍️ 创意写作": "科幻微小说，考察创意和文采",
            "📊 数据分析": "季度营收趋势分析预测，考察数据洞察能力",
            "🌐 多语言翻译": "中译英/日/法，考察多语言能力",
            "🎯 指令遵循": "严格格式输出，考察指令执行力",
        }
        for name, desc in test_descs.items():
            st.markdown(f"- **{name}** — {desc}")

    # 点击开始：启动后台线程
    if st.button("🚀 开始全量对比测试", type="primary", disabled=_MC["running"]):
        _MC.update({
            "running": True, "status": "正在启动…", "current": 0, "total": total_tests,
            "result_ready": False, "all_test_results": None, "all_perf_data": None,
        })
        _params = {
            "test_presets": test_presets,
            "selected_models": selected_models,
            "run_count": run_count,
            "max_tokens": max_tokens,
        }
        threading.Thread(target=_run_compare_worker, args=(_params,), daemon=True).start()
        st.rerun()

    # ---- 运行中：展示进度并自动刷新（切换菜单后回来仍会续显）----
    if _MC["running"]:
        st.divider()
        st.subheader("📊 测试进度（后台运行中，可切换菜单，返回后自动续显）")
        _mc_total = _MC["total"] or 1
        progress = min(_MC["current"] / _mc_total, 1.0)
        st.progress(progress)
        st.caption(_MC["status"])
        time.sleep(1)
        st.rerun()

    # ---- 结果展示 ----
    if _MC["result_ready"] and _MC["all_perf_data"]:
        all_perf_data = _MC["all_perf_data"]
        all_test_results = _MC["all_test_results"]

        st.divider()
        st.subheader("📈 性能对比")

        import pandas as pd
        perf_df = pd.DataFrame(all_perf_data)
        summary_df = perf_df.groupby("model").agg(
            avg_time=("elapsed", "mean"),
            avg_tok_sec=("tok_per_sec", "mean"),
            avg_tokens=("completion_tokens", "mean"),
        ).round(2).reset_index()

        col1, col2 = st.columns(2)
        with col1:
            fig_time = go.Figure(data=[
                go.Bar(
                    x=summary_df["model"],
                    y=summary_df["avg_time"],
                    text=[f"{v:.1f}s" for v in summary_df["avg_time"]],
                    textposition='auto',
                    marker_color='#7c3aed'
                )
            ])
            fig_time.update_layout(title="平均响应时间 (7项汇总)", xaxis_title="模型", yaxis_title="秒", height=300,
                margin=dict(l=20, r=20, t=40, b=20),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig_time, use_container_width=True, config={'displaylogo': False, 'scrollZoom': True})
        with col2:
            fig_tok = go.Figure(data=[
                go.Bar(
                    x=summary_df["model"],
                    y=summary_df["avg_tok_sec"],
                    text=[f"{v:.1f}" for v in summary_df["avg_tok_sec"]],
                    textposition='auto',
                    marker_color='#ec4899'
                )
            ])
            fig_tok.update_layout(title="平均吞吐量 (7项汇总)", xaxis_title="模型", yaxis_title="tokens/秒", height=300,
                margin=dict(l=20, r=20, t=40, b=20),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig_tok, use_container_width=True, config={'displaylogo': False, 'scrollZoom': True})

        # 汇总表格
        st.subheader("📋 汇总数据")
        st.dataframe(summary_df.style.format({"avg_time": "{:.2f}", "avg_tok_sec": "{:.1f}", "avg_tokens": "{:.0f}"}), use_container_width=True)

        # 按测试类型展示输出内容对比
        st.divider()
        st.subheader("💬 各项测试输出对比")
        for test_name, results in all_test_results.items():
            with st.expander(f"{test_name}（{len(results)} 个模型）"):
                cols = st.columns(len(results))
                for i, r in enumerate(results):
                    with cols[i]:
                        st.markdown(f"**{r['model']}**")
                        st.caption(f"⏱️ {r['elapsed']:.1f}s | ⚡ {r['tok_per_sec']:.1f} tok/s")
                        st.write(r["response"])

        # 生成报告
        st.divider()
        st.subheader("📄 生成报告")

        if st.button("📥 下载 Markdown 报告"):
            report_models = [r["model"] for r in next(iter(all_test_results.values()), [])] or list(all_models.keys())
            report = f"""# 模型对比测试报告

**测试时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 测试参数
- **测试项目**: {num_tests} 项全量测试
- **测试模型**: {", ".join(report_models)}
- **最大输出 Tokens**: {max_tokens}
- **每项测试次数**: {run_count}

## 性能汇总

| 模型 | 平均响应时间 | 平均吞吐量 (tok/s) |
|------|-------------|-------------------|
"""
            for _, row in summary_df.iterrows():
                report += f"| {row['model']} | {row['avg_time']:.2f}s | {row['avg_tok_sec']:.1f} |\n"

            report += "\n## 各项测试输出\n\n"
            for test_name, results in all_test_results.items():
                report += f"### {test_name}\n\n"
                for r in results:
                    report += f"**{r['model']}** ({r['elapsed']:.1f}s, {r['tok_per_sec']:.1f} tok/s)\n\n"
                    report += f"{r['response']}\n\n---\n\n"

            report_path = os.path.join(OUTPUT_REPORTS, f"model_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
            os.makedirs(OUTPUT_REPORTS, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report)

            st.success("✅ 报告已保存")
            st.info(f"📁 已保存到：`{report_path}`")
            report_filename = os.path.basename(report_path)
            st.download_button(
                label="📥 另存为...",
                data=report,
                file_name=report_filename,
                mime="text/markdown"
            )
# ==================== 图片理解页 ====================
elif page == "👁️ 图片理解":
    st.title("👁️ 图片理解")
    st.caption("基于 Qwen3.8-27B 多模态模型（与文本对话共用同一模型）")

    # 共享任务状态（跨 rerun 保留，切换菜单再回来不丢）
    _st = _bt_store().setdefault("img_understand", {
        "running": False, "status": "", "done": False, "ok": False,
        "thinking": "", "answer": "", "full_text": "", "time_str": "",
        "md_path": "", "error": "",
    })

    # 后台线程：VLM 流式分析，进度与结果写入 _st
    def _img_u_worker(temp_path, prompt, uploaded_name):
        try:
            script_path = "/tmp/vlm_stream_gen.py"
            with open(script_path, "w") as f:
                f.write(r'''import sys, json, contextlib, time
from mlx_vlm import load, stream_generate

model_path = "/Users/lizhun/Desktop/星小辰工作空间/models/mlx-lm/Qwen3.8-27B-4bit"
temp_path = sys.argv[1]
prompt_text = sys.argv[2]

t0 = time.time()
with contextlib.redirect_stdout(sys.stderr):
    model, processor = load(model_path)
load_time = time.time() - t0
print(json.dumps({"load_time": f"{load_time:.1f}"}), flush=True)

messages = [{"role": "user", "content": [
    {"type": "image", "image": temp_path},
    {"type": "text", "text": prompt_text}
]}]
chat_prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

t1 = time.time()
for result in stream_generate(model, processor, prompt=chat_prompt, image=temp_path, max_tokens=8192):
    token_id = result.token if hasattr(result, 'token') else None
    if token_id is not None and token_id >= 0:
        decoded = processor.decode([token_id])
        if decoded:
            try:
                print(json.dumps({"text": decoded}), flush=True)
            except BrokenPipeError:
                break
print(json.dumps({"gen_time": f"{time.time() - t1:.1f}"}), flush=True)
''')
            import subprocess as sp
            proc = sp.Popen(
                ["/Users/lizhun/.local/share/TeleAgent/runtimes/python/bin/python3", script_path, temp_path, prompt],
                stdout=sp.PIPE, stderr=sp.DEVNULL, text=True
            )
            _st["status"] = "🔄 正在加载模型（首次约30秒）..."
            time_info = {"load": "", "gen": ""}
            collected = []
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if "load_time" in data:
                        time_info["load"] = data["load_time"]
                        _st["status"] = f"✅ 模型加载完成（{data['load_time']}秒），正在生成回复..."
                    elif "gen_time" in data:
                        time_info["gen"] = data["gen_time"]
                    elif "text" in data:
                        collected.append(data["text"])
                        _st["full_text"] = "".join(collected)
                except json.JSONDecodeError:
                    pass
            try:
                proc.wait(timeout=600)
            except sp.TimeoutExpired:
                proc.kill()
                proc.wait()

            full_text = "".join(collected)
            if full_text.strip():
                ti_parts = []
                if time_info["load"]:
                    ti_parts.append(f"模型加载 {time_info['load']}秒")
                if time_info["gen"]:
                    ti_parts.append(f"生成 {time_info['gen']}秒")
                time_str = " · ".join(ti_parts)

                parts = full_text.split("\n\n\n")
                if len(parts) >= 2:
                    thinking = parts[0].strip()
                    answer = "\n\n\n".join(parts[1:]).strip()
                else:
                    thinking = ""
                    answer = full_text.strip()

                save_content = f"# 图片分析结果\n\n**图片：** {uploaded_name}\n**提问：** {prompt}\n**时间：** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
                if thinking:
                    save_content += f"## 思考过程\n\n{thinking}\n\n---\n\n"
                save_content += f"## 分析结果\n\n{answer or full_text}\n"
                if time_str:
                    save_content += f"\n⏱️ {time_str}\n"
                md_path = os.path.join(OUTPUT_IMAGE_RECOG, f"img_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(save_content)

                _st.update(ok=True, thinking=thinking, answer=answer,
                           full_text=full_text, time_str=time_str, md_path=md_path,
                           status="✅ 分析完成")
                log_activity("图片理解", f"图片={uploaded_name} | 提问={prompt[:80]} | {time_str}",
                             duration=float(time_info['gen']) if time_info['gen'] else None)
            else:
                _st["status"] = "❌ 视觉模型未生成内容"
        except Exception as e:
            _st["status"] = f"❌ 分析出错: {e}"
        finally:
            _st["done"] = True
            _st["running"] = False

    uploaded_file = st.file_uploader("上传图片", type=["png", "jpg", "jpeg", "webp"])

    if uploaded_file:
        st.image(uploaded_file, caption="上传的图片", use_container_width=True)
        prompt = st.text_input("提问", value="详细描述这张图片的内容")

        if st.button("🔍 分析图片", type="primary", disabled=_st["running"]):
            temp_path = f"/tmp/upload_{uploaded_file.name}"
            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getvalue())
            _st.update({"running": True, "done": False, "ok": False, "status": "正在启动…",
                        "thinking": "", "answer": "", "full_text": "", "time_str": "", "md_path": ""})
            threading.Thread(target=_img_u_worker, args=(temp_path, prompt, uploaded_file.name), daemon=True).start()
            st.rerun()

    # ---- 运行中：进度与已生成文本（切换菜单后回来仍续显）----
    if _st["running"]:
        st.divider()
        st.subheader("🔍 分析进度（后台运行中，可切换菜单，返回后自动续显）")
        st.caption(_st["status"])
        if _st["full_text"]:
            st.markdown(_st["full_text"][-2000:])
        time.sleep(1)
        st.rerun()

    # ---- 完成：展示结果 ----
    if _st["done"] and _st["ok"]:
        st.divider()
        st.success("✅ 分析结果")
        st.write(_st["answer"] or _st["full_text"])
        if _st["time_str"]:
            st.caption(f"⏱️ {_st['time_str']}")
        st.info(f"📁 已保存到：`{_st['md_path']}`")
        with open(_st["md_path"], "rb") as f:
            st.download_button("📥 另存为...", f, file_name=os.path.basename(_st["md_path"]), mime="text/markdown")
        if _st["thinking"]:
            with st.expander("💭 思考过程"):
                st.write(_st["thinking"])

    if _st["done"] and not _st["ok"]:
        st.error(f"分析失败：{_st['status']}")
# ==================== 视频理解页 ====================
elif page == "🎥 视频理解":
    st.title("🎥 视频理解")
    st.caption("基于 Qwen3.8-27B 多模态模型，支持视频内容分析与理解")

    # 共享任务状态（跨 rerun 保留，切换菜单再回来不丢）
    _st = _bt_store().setdefault("video_understand", {
        "running": False, "status": "", "done": False, "ok": False,
        "thinking": "", "answer": "", "full_text": "", "time_str": "",
        "md_path": "", "error": "",
    })

    # 后台线程：VLM 流式分析，进度与结果写入 _st
    def _vid_u_worker(temp_video_path, prompt, uploaded_name):
        try:
            script_path = "/tmp/vlm_video_analyze.py"
            with open(script_path, "w") as f:
                f.write(r'''import sys, json, contextlib, time
from mlx_vlm import load, stream_generate

model_path = "/Users/lizhun/Desktop/星小辰工作空间/models/mlx-lm/Qwen3.8-27B-4bit"
video_path = sys.argv[1]
prompt_text = sys.argv[2]

t0 = time.time()
with contextlib.redirect_stdout(sys.stderr):
    model, processor = load(model_path)
load_time = time.time() - t0
print(json.dumps({"load_time": f"{load_time:.1f}"}), flush=True)

messages = [{"role": "user", "content": [
    {"type": "video", "video": video_path},
    {"type": "text", "text": prompt_text}
]}]
chat_prompt = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

t1 = time.time()
for result in stream_generate(model, processor, prompt=chat_prompt, video=video_path, max_tokens=8192):
    token_id = result.token if hasattr(result, 'token') else None
    if token_id is not None and token_id >= 0:
        decoded = processor.decode([token_id])
        if decoded:
            try:
                print(json.dumps({"text": decoded}), flush=True)
            except BrokenPipeError:
                break
print(json.dumps({"gen_time": f"{time.time() - t1:.1f}"}), flush=True)
''')
            import subprocess as sp
            proc = sp.Popen(
                ["/Users/lizhun/.local/share/TeleAgent/runtimes/python/bin/python3", script_path, temp_video_path, prompt],
                stdout=sp.PIPE, stderr=sp.DEVNULL, text=True
            )
            _st["status"] = "🔄 正在加载模型（首次约30秒）..."
            time_info = {"load": "", "gen": ""}
            collected = []
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if "load_time" in data:
                        time_info["load"] = data["load_time"]
                        _st["status"] = f"✅ 模型加载完成（{data['load_time']}秒），正在生成回复..."
                    elif "gen_time" in data:
                        time_info["gen"] = data["gen_time"]
                    elif "text" in data:
                        collected.append(data["text"])
                        _st["full_text"] = "".join(collected)
                except json.JSONDecodeError:
                    pass
            try:
                proc.wait(timeout=600)
            except sp.TimeoutExpired:
                proc.kill()
                proc.wait()

            full_text = "".join(collected)
            if full_text.strip():
                ti_parts = []
                if time_info["load"]:
                    ti_parts.append(f"模型加载 {time_info['load']}秒")
                if time_info["gen"]:
                    ti_parts.append(f"生成 {time_info['gen']}秒")
                time_str = " · ".join(ti_parts)

                parts = full_text.split("\n\n\n")
                if len(parts) >= 2:
                    thinking = parts[0].strip()
                    answer = "\n\n\n".join(parts[1:]).strip()
                else:
                    thinking = ""
                    answer = full_text.strip()

                save_content = f"# 视频分析结果\n\n**视频：** {uploaded_name}\n**提问：** {prompt}\n**时间：** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
                if thinking:
                    save_content += f"## 思考过程\n\n{thinking}\n\n---\n\n"
                save_content += f"## 分析结果\n\n{answer or full_text}\n"
                if time_str:
                    save_content += f"\n⏱️ {time_str}\n"
                md_path = os.path.join(OUTPUT_VIDEO_RECOG, f"video_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(save_content)

                _st.update(ok=True, thinking=thinking, answer=answer,
                           full_text=full_text, time_str=time_str, md_path=md_path,
                           status="✅ 分析完成")
            else:
                _st["status"] = "❌ 视频分析失败，请检查视频格式是否受支持（推荐 mp4）"
        except Exception as e:
            _st["status"] = f"❌ 分析出错: {e}"
        finally:
            _st["done"] = True
            _st["running"] = False

    uploaded_video = st.file_uploader("上传视频", type=["mp4", "avi", "mov", "mkv", "webm"])

    if uploaded_video:
        video_bytes = uploaded_video.getvalue()
        st.video(video_bytes)
        prompt = st.text_input("提问", value="详细描述这个视频的内容")

        if st.button("🔍 分析视频", type="primary", disabled=_st["running"]):
            temp_video_path = f"/tmp/upload_{uploaded_video.name}"
            with open(temp_video_path, "wb") as f:
                f.write(video_bytes)
            _st.update({"running": True, "done": False, "ok": False, "status": "正在启动…",
                        "thinking": "", "answer": "", "full_text": "", "time_str": "", "md_path": ""})
            threading.Thread(target=_vid_u_worker, args=(temp_video_path, prompt, uploaded_video.name), daemon=True).start()
            st.rerun()

    # ---- 运行中：进度与已生成文本（切换菜单后回来仍续显）----
    if _st["running"]:
        st.divider()
        st.subheader("🔍 分析进度（后台运行中，可切换菜单，返回后自动续显）")
        st.caption(_st["status"])
        if _st["full_text"]:
            st.markdown(_st["full_text"][-2000:])
        time.sleep(1)
        st.rerun()

    # ---- 完成：展示结果 ----
    if _st["done"] and _st["ok"]:
        st.divider()
        st.success("✅ 分析结果")
        st.write(_st["answer"] or _st["full_text"])
        if _st["time_str"]:
            st.caption(f"⏱️ {_st['time_str']}")
        st.info(f"📁 已保存到：`{_st['md_path']}`")
        with open(_st["md_path"], "rb") as f:
            st.download_button("📥 另存为...", f, file_name=os.path.basename(_st["md_path"]), mime="text/markdown")
        if _st["thinking"]:
            with st.expander("💭 思考过程"):
                st.write(_st["thinking"])

    if _st["done"] and not _st["ok"]:
        st.error(f"分析失败：{_st['status']}")
# ==================== 图片生成页 ====================
elif page == "🎨 图片生成":
    st.title("🎨 图片生成")
    st.caption("SANA 1.5 / SDXL 1.0 / Qwen-Image — 通过 ComfyUI API 本地推理")

    # 共享任务状态（跨 rerun 保留，切换菜单再回来不丢）
    _st = _bt_store().setdefault("img_gen", {
        "running": False, "done": False, "ok": False, "progress": 0.0,
        "text": "", "img_path": None, "error": "", "gen_time": 0,
        "t_start": 0, "model_label": "", "prompt_short": "",
    })

    # 后台线程：ComfyUI 生成，进度与结果写入 _st
    def _img_gen_worker(prompt, model_name, width, height, steps, seed, cfg):
        try:
            def on_progress(step, total, status):
                elapsed = time.time() - _st["t_start"]
                if status == "loading_model":
                    _st["progress"] = 0.02
                    _st["text"] = f"🔄 正在加载模型... ({elapsed:.0f}s)"
                elif status == "sampling":
                    pct = step / total if total > 0 else 0.0
                    if step > 0 and elapsed > 0:
                        eta = elapsed / step * (total - step)
                        _st["text"] = f"🎨 采样中... 第 {step}/{total} 步 ({elapsed:.0f}s | 预计还剩 {int(eta//60)}分{int(eta%60)}秒)"
                    else:
                        _st["text"] = f"🎨 采样中... 第 {step}/{total} 步 ({elapsed:.0f}s)"
                    _st["progress"] = pct
                elif status == "done":
                    _st["progress"] = 0.95
                    _st["text"] = f"✅ 采样完成，正在解码图像... ({elapsed:.0f}s)"

            img_path, error = generate_image_comfyui(
                prompt, model=model_name,
                width=width, height=height,
                steps=steps, seed=seed, cfg=cfg,
                progress_callback=on_progress
            )
            _st["gen_time"] = time.time() - _st["t_start"]
            if img_path:
                _st.update(ok=True, img_path=img_path, progress=1.0,
                           text=f"生成完成！耗时 {_st['gen_time']:.1f} 秒")
                log_activity("图片生成", f"模型={model_name} | 尺寸={width}x{height} | 提示词={prompt[:80]}", duration=_st['gen_time'])
            else:
                _st["error"] = error
                _st["text"] = f"生成失败: {error}"
                log_activity("图片生成", f"模型={model_name} | 失败: {error}", status="error")
        except Exception as e:
            _st["error"] = str(e)
            _st["text"] = f"生成出错: {e}"
        finally:
            _st["done"] = True
            _st["running"] = False

    col1, col2 = st.columns([1, 1])

    with col1:
        # 检测可用模型
        comfy_online = False
        try:
            import requests as _req
            _r = _req.get("http://localhost:8188/system_stats", timeout=3)
            comfy_online = _r.status_code == 200
        except:
            pass

        if not comfy_online:
            st.warning("⚠️ ComfyUI 未运行（端口 8188），图片生成不可用。请先启动 ComfyUI。")

        model_options = ["SANA 1.5 (轻量快速)", "SDXL 1.0 (高质量)", "Qwen-Image (中文最强)"]
        model_choice = st.selectbox("模型", model_options, help="SANA: 1.6B 轻量秒级出图 | SDXL: 3.5B 生态成熟 | Qwen-Image: 20B 中文理解最强")

        model_key = {"SANA": "sana", "SDXL": "sdxl", "Qwen": "qwen_image"}
        model_name = "sana"
        for key, val in model_key.items():
            if key in model_choice:
                model_name = val
                break

        prompt = st.text_area("提示词", value="a cute orange cat sitting on a windowsill, watching the sunset, photorealistic",
                             help="Qwen-Image 支持中文提示词，效果更好")

        with st.expander("⚙️ 参数设置"):
            col_a, col_b = st.columns(2)
            with col_a:
                img_width = st.selectbox("宽度", [512, 768, 1024, 1280], index=2)
                img_height = st.selectbox("高度", [512, 768, 1024, 1280], index=2)
            with col_b:
                if model_name == "sana":
                    default_cfg, default_steps = 5.0, 28
                elif model_name == "sdxl":
                    default_cfg, default_steps = 7.0, 25
                else:
                    default_cfg, default_steps = 1.0, 8
                cfg = st.slider("Guidance Scale (CFG)", 0.0, 15.0, default_cfg, 0.5,
                               help="越高越遵循提示词，但可能过度饱和")
                steps = st.slider("推理步数", 1, 50, default_steps, help="步数越多质量越好但更慢")
                seed = st.number_input("随机种子", value=-1, help="-1 表示随机")

        if st.button("🎨 生成图片", type="primary", disabled=(not comfy_online) or _st["running"]):
            _st.update(running=True, done=False, ok=False, progress=0.0,
                       text=f"初始化（{model_name.upper()} · {steps}步 · {img_width}x{img_height}）...",
                       img_path=None, error="")
            _st["t_start"] = time.time()
            _st["model_label"] = model_name
            _st["prompt_short"] = prompt[:50]
            threading.Thread(target=_img_gen_worker, args=(prompt, model_name, img_width, img_height, steps, int(seed), cfg), daemon=True).start()
            st.rerun()

        if _st["running"]:
            st.progress(min(max(_st["progress"], 0.0), 1.0), text=_st["text"])
            time.sleep(1)
            st.rerun()
        elif _st["done"] and _st["ok"]:
            st.progress(1.0, text=_st["text"])
            st.image(_st["img_path"], caption=f"[{_st['model_label']}] {_st['prompt_short']}", use_container_width=True)
            img_filename = os.path.basename(_st["img_path"])
            st.info(f"📁 已保存到：`{_st['img_path']}`")
            with open(_st["img_path"], "rb") as f:
                st.download_button("📥 另存为...", f, file_name=img_filename, mime="image/png")
        elif _st["done"] and not _st["ok"]:
            st.error(f"生成失败: {_st['error']}")

    with col2:
        st.subheader("📁 历史生成")
        image_dir = OUTPUT_IMAGE_GEN
        if os.path.exists(image_dir):
            images = sorted(
                [f for f in os.listdir(image_dir) if f.endswith(('.png', '.jpg'))],
                key=lambda x: os.path.getmtime(os.path.join(image_dir, x)),
                reverse=True
            )[:5]
            for img_file in images:
                st.image(os.path.join(image_dir, img_file), caption=img_file, use_container_width=True)
# ==================== 视频生成页 ====================
elif page == "🎬 视频生成":
    st.title("🎬 视频生成")

    # 检测 ComfyUI
    comfy_online = False
    try:
        _r = requests.get("http://localhost:8188/system_stats", timeout=3)
        comfy_online = _r.status_code == 200
    except:
        pass
    if not comfy_online:
        st.warning("⚠️ ComfyUI 未运行（端口 8188），视频生成不可用。请先启动 ComfyUI。")

    tab_t2v, tab_i2v = st.tabs(["📝 文生视频", "🖼️ 图生视频"])

    # 共享任务状态（跨 rerun 保留，切换菜单再回来不丢）
    _bt_v = _bt_store()
    _st_t2v = _bt_v.setdefault("video_t2v", {
        "running": False, "done": False, "ok": False, "progress": 0.0,
        "text": "", "vid_path": None, "error": "", "gen_time": 0, "t_start": 0,
    })
    _st_i2v = _bt_v.setdefault("video_i2v", {
        "running": False, "done": False, "ok": False, "progress": 0.0,
        "text": "", "vid_path": None, "error": "", "gen_time": 0, "t_start": 0,
    })

    # 后台线程：文生视频
    def _t2v_worker(prompt_txt, profile_key, w, h, dur, seed):
        try:
            def on_progress(step, total, status):
                elapsed = time.time() - _st_t2v["t_start"]
                if status == "loading_model":
                    _st_t2v["progress"] = 0.02
                    _st_t2v["text"] = f"🔄 正在加载模型... ({elapsed:.0f}s)"
                elif status == "sampling":
                    pct = step / total if total > 0 else 0.0
                    if step > 0 and elapsed > 0:
                        eta = elapsed / step * (total - step)
                        _st_t2v["text"] = f"🎬 推理中... 第 {step}/{total} 步 ({elapsed:.0f}s | 预计还剩 {int(eta//60)}分{int(eta%60)}秒)"
                    else:
                        _st_t2v["text"] = f"🎬 推理中... 第 {step}/{total} 步 ({elapsed:.0f}s)"
                    _st_t2v["progress"] = pct
                elif status == "done":
                    _st_t2v["progress"] = 0.95
                    _st_t2v["text"] = f"✅ 推理完成，正在渲染视频... ({elapsed:.0f}s)"

            vid_path, error = generate_video_comfyui(
                prompt_txt,
                generation_profile=profile_key,
                width=w, height=h,
                duration=dur, seed=seed,
                progress_callback=on_progress
            )
            _st_t2v["gen_time"] = time.time() - _st_t2v["t_start"]
            if vid_path:
                _st_t2v.update(ok=True, vid_path=vid_path, progress=1.0,
                               text=f"生成完成！耗时 {_st_t2v['gen_time']:.0f} 秒")
                log_activity("视频生成", f"模式={profile_key} | 分辨率={w}x{h} | 时长={dur}s | 提示词={prompt_txt[:60]}", duration=_st_t2v['gen_time'])
            else:
                _st_t2v["error"] = error
                _st_t2v["text"] = f"生成失败: {error}"
                log_activity("视频生成", f"失败: {error}", status="error")
        except Exception as e:
            _st_t2v["error"] = str(e)
            _st_t2v["text"] = f"生成出错: {e}"
        finally:
            _st_t2v["done"] = True
            _st_t2v["running"] = False

    # 后台线程：图生视频
    def _i2v_worker(temp_img_path, prompt_txt, dur, w, h, steps, seed):
        try:
            def on_progress(step, total, status):
                elapsed = time.time() - _st_i2v["t_start"]
                if status == "loading_model":
                    _st_i2v["progress"] = 0.02
                    _st_i2v["text"] = f"🔄 正在加载模型... ({elapsed:.0f}s)"
                elif status == "sampling":
                    pct = step / total if total > 0 else 0.0
                    if step > 0 and elapsed > 0:
                        eta = elapsed / step * (total - step)
                        _st_i2v["text"] = f"🎬 推理中... 第 {step}/{total} 步 ({elapsed:.0f}s | 预计还剩 {int(eta//60)}分{int(eta%60)}秒)"
                    else:
                        _st_i2v["text"] = f"🎬 推理中... 第 {step}/{total} 步 ({elapsed:.0f}s)"
                    _st_i2v["progress"] = pct
                elif status == "done":
                    _st_i2v["progress"] = 0.95
                    _st_i2v["text"] = f"✅ 推理完成，正在渲染视频... ({elapsed:.0f}s)"

            vid_path, error = generate_image_to_video_local(
                temp_img_path, prompt_txt,
                duration=dur,
                width=w, height=h,
                steps=steps, seed=seed,
                progress_callback=on_progress
            )
            _st_i2v["gen_time"] = time.time() - _st_i2v["t_start"]
            if os.path.exists(temp_img_path):
                os.remove(temp_img_path)
            if vid_path:
                _st_i2v.update(ok=True, vid_path=vid_path, progress=1.0,
                               text=f"生成完成！耗时 {_st_i2v['gen_time']:.0f} 秒")
                log_activity("视频生成(图生)", f"分辨率={w}x{h} | 时长={dur}s | 提示词={prompt_txt[:60]}", duration=_st_i2v['gen_time'])
            else:
                _st_i2v["error"] = error
                _st_i2v["text"] = f"生成失败: {error}"
        except Exception as e:
            _st_i2v["error"] = str(e)
            _st_i2v["text"] = f"生成出错: {e}"
        finally:
            _st_i2v["done"] = True
            _st_i2v["running"] = False

    # ==================== 文生视频 Tab ====================
    with tab_t2v:
        st.caption("MiniMax H3 MLX 本地推理，视频+同步音频一起生成")

        prompt_t2v = st.text_area("提示词",
            value="一只橘猫趴在窗台上，看着窗外夕阳西下，毛发被金色阳光照亮。音效：远处城市的轻柔环境音，没有说话声。",
            help="支持中英文，建议包含画面描述和音频描述",
            key="t2v_prompt")

        with st.expander("⚙️ 参数设置", expanded=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                gen_profile = st.selectbox("生成模式", [
                    "Turbo 4 Fast (最快)",
                    "Turbo 8 Balanced (平衡)",
                    "Full 20 Quality (最高质量)"
                ], help="Turbo 4: 5步最快 | Turbo 8: 9步平衡 | Full 20: 21步最高质量",
                key="t2v_profile")
                gen_profile_key = gen_profile.split(" (")[0].strip()
            with col2:
                resolution = st.selectbox("分辨率", [
                    "864x480 (推荐)",
                    "768x448 (轻量)",
                    "960x544 (高清)",
                    "640x384 (极速)"
                ], key="t2v_res")
                width, height = map(int, resolution.split(" ")[0].split("x"))
            with col3:
                duration = st.slider("时长（秒）", 5.0, 10.0, 5.0, 0.5, help="5秒最稳定，最长10秒", key="t2v_dur")
                seed = st.number_input("随机种子", value=0, help="0 表示随机", key="t2v_seed")

        st.caption("💡 Turbo 4 Fast 模式约 2-4 分钟出视频，Full 20 Quality 约 5-10 分钟。生成含同步音频。")

        if st.button("🎬 生成视频", type="primary", disabled=(not comfy_online) or _st_t2v["running"], key="t2v_btn"):
            _st_t2v.update(running=True, done=False, ok=False, progress=0.0,
                           text=f"初始化（MiniMax H3 · {gen_profile_key}）...", vid_path=None, error="")
            _st_t2v["t_start"] = time.time()
            threading.Thread(target=_t2v_worker, args=(prompt_t2v, gen_profile_key, width, height, duration, int(seed)), daemon=True).start()
            st.rerun()

        if _st_t2v["running"]:
            st.progress(min(max(_st_t2v["progress"], 0.0), 1.0), text=_st_t2v["text"])
            time.sleep(1)
            st.rerun()
        elif _st_t2v["done"] and _st_t2v["ok"]:
            st.progress(1.0, text=_st_t2v["text"])
            st.success("视频生成成功！（含同步音频）")
            st.video(_st_t2v["vid_path"])
            vid_filename = os.path.basename(_st_t2v["vid_path"])
            st.info(f"📁 已保存到：`{_st_t2v['vid_path']}`")
            with open(_st_t2v["vid_path"], "rb") as f:
                st.download_button("📥 另存为...", f, file_name=vid_filename, mime="video/mp4", key="t2v_dl")
            audio_files = sorted([f for f in os.listdir(OUTPUT_VIDEO_GEN) if f.endswith('_audio.')])
            if audio_files:
                latest_audio = audio_files[-1]
                audio_path = os.path.join(OUTPUT_VIDEO_GEN, latest_audio)
                st.caption(f"🎵 音频文件：`{audio_path}`")
        elif _st_t2v["done"] and not _st_t2v["ok"]:
            st.error(f"生成失败: {_st_t2v['error']}")

    # ==================== 图生视频 Tab ====================
    with tab_i2v:
        st.caption("上传一张图片作为首帧，MiniMax H3 MLX 本地推理生成视频（含同步音频）")

        uploaded_img = st.file_uploader("上传图片（作为视频首帧）", type=["png", "jpg", "jpeg", "webp"],
                                        key="i2v_upload")

        if uploaded_img:
            st.image(uploaded_img, caption="首帧图片", use_container_width=True)

            prompt_i2v = st.text_area("提示词",
                value="让画面动起来，镜头缓缓推进，光影自然变化。音效：环境自然音。",
                help="描述你希望图片如何动起来",
                key="i2v_prompt")

            with st.expander("⚙️ 参数设置", expanded=True):
                col1, col2, col3 = st.columns(3)
                with col1:
                    i2v_profile = st.selectbox("生成模式", [
                        "Turbo 4 Fast (最快)",
                        "Turbo 8 Balanced (平衡)",
                        "Full 20 Quality (最高质量)"
                    ], help="Turbo 4: 5步最快 | Turbo 8: 9步平衡 | Full 20: 21步最高质量",
                    key="i2v_profile")
                    i2v_profile_key = i2v_profile.split(" (")[0].strip()
                    i2v_steps = {"Turbo 4 Fast": 5, "Turbo 8 Balanced": 9, "Full 20 Quality": 21}.get(i2v_profile_key, 5)
                with col2:
                    i2v_resolution = st.selectbox("分辨率", [
                        "864x480 (推荐)",
                        "768x448 (轻量)",
                        "960x544 (高清)",
                        "640x384 (极速)"
                    ], key="i2v_res")
                    i2v_width, i2v_height = map(int, i2v_resolution.split(" ")[0].split("x"))
                with col3:
                    i2v_duration = st.slider("时长（秒）", 5.0, 10.0, 5.0, 0.5, help="5秒最稳定", key="i2v_dur")
                    i2v_seed = st.number_input("随机种子", value=0, help="0 表示随机",
                                               key="i2v_seed")

            st.caption("💡 Turbo 4 Fast 模式约 3-5 分钟出视频。图生视频使用本地 MiniMax H3 模型，无需 API key。")

            if st.button("🎬 图生视频", type="primary", disabled=_st_i2v["running"], key="i2v_btn"):
                temp_img_path = os.path.join(OUTPUT_VIDEO_GEN, f"_i2v_input_{int(time.time())}.png")
                with open(temp_img_path, "wb") as f:
                    f.write(uploaded_img.getvalue())
                _st_i2v.update(running=True, done=False, ok=False, progress=0.0,
                               text="正在加载模型...", vid_path=None, error="")
                _st_i2v["t_start"] = time.time()
                threading.Thread(target=_i2v_worker, args=(temp_img_path, prompt_i2v, i2v_duration, i2v_width, i2v_height, i2v_steps, int(i2v_seed)), daemon=True).start()
                st.rerun()

            if _st_i2v["running"]:
                st.progress(min(max(_st_i2v["progress"], 0.0), 1.0), text=_st_i2v["text"])
                time.sleep(1)
                st.rerun()
            elif _st_i2v["done"] and _st_i2v["ok"]:
                st.progress(1.0, text=_st_i2v["text"])
                st.success("图生视频成功！（含同步音频）")
                st.video(_st_i2v["vid_path"])
                vid_filename = os.path.basename(_st_i2v["vid_path"])
                st.info(f"📁 已保存到：`{_st_i2v['vid_path']}`")
                with open(_st_i2v["vid_path"], "rb") as f:
                    st.download_button("📥 另存为...", f, file_name=vid_filename, mime="video/mp4", key="i2v_dl")
            elif _st_i2v["done"] and not _st_i2v["ok"]:
                st.error(f"生成失败: {_st_i2v['error']}")

    # 恢复最近视频（从 ComfyUI history 捞回会话中断丢失的视频）
    if comfy_online:
        with st.expander("🔧 恢复最近视频（会话中断/超时后捞回）"):
            if st.button("🔎 扫描 ComfyUI 未搬回的视频", key="recover_videos_btn"):
                try:
                    _recover_url = "http://localhost:8188"
                    hist = requests.get(f"{_recover_url}/history", timeout=10).json()
                    recovered = []
                    existing_files = set(os.listdir(OUTPUT_VIDEO_GEN)) if os.path.exists(OUTPUT_VIDEO_GEN) else set()
                    for pid, info in sorted(hist.items(), key=lambda x: x[1].get("status", {}).get("messages", [[0,{}]])[0][1].get("timestamp", 0) if x[1].get("status",{}).get("messages") else 0, reverse=True):
                        if not info.get("status", {}).get("completed", False):
                            continue
                        outputs = info.get("outputs", {})
                        for node_id, node_out in outputs.items():
                            if "images" in node_out:
                                for vid in node_out["images"]:
                                    fname = vid.get("filename", "")
                                    if fname.endswith(('.mp4', '.webm', '.gif')):
                                        vid_url = f"{_recover_url}/view?filename={fname}&subfolder={vid.get('subfolder','')}&type={vid.get('type','output')}"
                                        vid_resp = requests.get(vid_url, timeout=60, stream=True)
                                        content = vid_resp.content
                                        new_path = os.path.join(OUTPUT_VIDEO_GEN, f"minimax_h3_{int(time.time())}_{node_id}.mp4")
                                        with open(new_path, "wb") as f:
                                            f.write(content)
                                        recovered.append(f"视频: {fname} → {os.path.basename(new_path)}")
                            if "audio" in node_out:
                                for aud in node_out["audio"]:
                                    fname = aud.get("filename", "")
                                    if fname.endswith(('.flac', '.wav')):
                                        aud_url = f"{_recover_url}/view?filename={fname}&subfolder={aud.get('subfolder','')}&type={aud.get('type','output')}"
                                        aud_resp = requests.get(aud_url, timeout=60)
                                        aud_ext = os.path.splitext(fname)[1]
                                        new_path = os.path.join(OUTPUT_VIDEO_GEN, f"minimax_h3_{int(time.time())}_audio{aud_ext}")
                                        with open(new_path, "wb") as f:
                                            f.write(aud_resp.content)
                                        recovered.append(f"音频: {fname} → {os.path.basename(new_path)}")
                    if recovered:
                        st.success(f"恢复完成！共搬回 {len(recovered)} 个文件：")
                        for r in recovered:
                            st.text(f"  ✓ {r}")
                        st.rerun()
                    else:
                        st.info("没有发现未搬回的视频。")
                except Exception as e:
                    st.error(f"扫描失败: {e}")

    # 显示已有视频（两个 tab 共享）
    st.divider()
    st.subheader("📁 历史视频")
    video_dir = OUTPUT_VIDEO_GEN
    if os.path.exists(video_dir):
        videos = sorted(
            [f for f in os.listdir(video_dir) if f.endswith('.mp4')],
            key=lambda x: os.path.getmtime(os.path.join(video_dir, x)),
            reverse=True
        )[:5]
        for vid in videos:
            st.video(os.path.join(video_dir, vid))
# ==================== 语音识别页 ====================
elif page == "🎤 语音识别":
    st.title("🎤 语音识别")
    st.caption("本地三引擎 ASR：SenseVoiceSmall（≤3min，CPU，最快）| Seaco-Paraformer（中文专用，内置说话人分离+标点+热词）| 星辰慧记（>10min，云端，带说话人分离）")
    st.info("🗣️ 说话人分离：Seaco-Paraformer 和 SenseVoice 内置支持（cam++ 声纹模型）。")

    # 共享任务状态（跨 rerun 保留，切换菜单再回来不丢）
    _st = _bt_store().setdefault("asr", {
        "running": False, "done": False, "ok": False, "status": "",
        "logs": [], "data": None, "md_path": "", "json_path": "", "error": "",
    })

    # 语音识别三引擎脚本（voice-suite 技能）
    _VOICE_SUITE_SCRIPT = os.path.expanduser("~/.config/TeleAgent/skills/voice-suite/scripts/transcribe.py")
    _TRANSCRIBE_OK = os.path.exists(_VOICE_SUITE_SCRIPT)

    # 后台线程：执行 ASR 子进程，进度与结果写入 _st
    def _asr_worker(cmd, audio_name):
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            _st["status"] = "🔄 识别中...（首次会加载模型，请耐心等待）"
            log_lines = []
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                log_lines.append(line)
                _st["logs"] = log_lines[-20:]
                if line.startswith("音频时长") or line.startswith("选择引擎") or line.startswith("引擎:"):
                    _st["status"] = line
            proc.wait(timeout=3600)

            if proc.returncode == 0 and os.path.exists(_st["json_path"]):
                with open(_st["json_path"], "r", encoding="utf-8") as f:
                    data = json.load(f)
                md_path = os.path.join(OUTPUT_SPEECH_RECOG, f"asr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
                md_content = f"# 语音识别结果\n\n- 引擎: {data['engine']}\n- 音频时长: {data.get('audio_duration_s', 0)}s\n- 总耗时: {data.get('total_time_s', 0)}s\n\n## 完整文本\n\n{data['text']}\n\n"
                if data.get("segments"):
                    md_content += "## 分段信息\n\n"
                    for seg in data["segments"]:
                        spk_val = seg.get("speaker")
                        spk = f" [说话人 {spk_val}]" if spk_val is not None else ""
                        md_content += f"- {seg.get('start_ms', 0)/1000:.1f}s-{seg.get('end_ms', 0)/1000:.1f}s{spk}: {seg.get('text', '')}\n"
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                _st.update(ok=True, data=data, md_path=md_path, status="✅ 识别完成")
                log_activity("语音识别", f"引擎={data['engine']} | 音频={audio_name} | 时长={data.get('audio_duration_s', 0)}s | 耗时={data.get('total_time_s', 0)}s", duration=data.get('total_time_s', 0))
            else:
                _st["error"] = "".join(log_lines)[-500:]
                _st["status"] = f"❌ 识别失败: {_st['error'][-120:]}"
                log_activity("语音识别", f"音频={audio_name}", status="error")
        except Exception as e:
            _st["error"] = str(e)
            _st["status"] = f"❌ 识别出错: {e}"
        finally:
            _st["done"] = True
            _st["running"] = False

    uploaded_audio = st.file_uploader("上传音频文件", type=["mp3", "wav", "m4a", "flac", "aac", "ogg", "pcm"])

    if uploaded_audio:
        st.audio(uploaded_audio)

        col1, col2 = st.columns(2)
        with col1:
            engine_choice = st.selectbox(
                "识别引擎",
                ["auto (按时长自动选择)", "sensevoice (SenseVoiceSmall, 快/轻)", "seaco (Seaco-Paraformer, 中文+说话人分离)", "huiji (星辰慧记云端, 长音频+说话人)"],
                help="auto 会按音频时长自动选择最优引擎；seaco 中文专用，内置说话人分离，带标点",
            )
        with col2:
            hot_words = st.text_input("热词（逗号分隔，可选）", value="", help="提升特定词汇识别准确率，星辰慧记/Seaco 引擎生效")
        col_spk, col_mode = st.columns(2)
        with col_spk:
            enable_diarization = st.checkbox("🗣️ 本地说话人分离", value=False, help="基于 cam++ 声纹模型，本地运行，任何引擎可用")
        with col_mode:
            speech_mode = st.radio("说话模式（星辰慧记引擎生效）", ["多人", "单人"], horizontal=True)

        if st.button("🎤 开始识别", type="primary", disabled=_st["running"]):
            # 保存临时文件
            temp_path = f"/tmp/asr_input_{int(time.time())}_{uploaded_audio.name}"
            with open(temp_path, "wb") as f:
                f.write(uploaded_audio.getvalue())

            engine = engine_choice.split(" ")[0]
            script_path = _VOICE_SUITE_SCRIPT
            if not os.path.exists(script_path):
                st.error("transcribe.py 脚本不存在，请先安装 voice-suite 技能")
            else:
                result_json = os.path.join(OUTPUT_SPEECH_RECOG, f"asr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
                cmd = [
                    sys.executable, script_path, temp_path,
                    "--engine", engine,
                    "--output", result_json,
                ]
                if hot_words:
                    cmd += ["--hot-words", hot_words]
                cmd += ["--speech-mode", "1" if speech_mode == "单人" else "-1"]
                if enable_diarization:
                    cmd += ["--diarization"]
                _st.update(running=True, done=False, ok=False, status="🔄 识别中...（首次会加载模型，请耐心等待）",
                           logs=[], data=None, md_path="", json_path=result_json, error="")
                threading.Thread(target=_asr_worker, args=(cmd, uploaded_audio.name), daemon=True).start()
                st.rerun()

    # ---- 运行中：显示状态与日志（切换菜单后回来仍续显）----
    if _st["running"]:
        st.divider()
        st.subheader("🎤 识别进度（后台运行中，可切换菜单，返回后自动续显）")
        st.info(_st["status"])
        if _st["logs"]:
            with st.expander("📋 最近日志"):
                st.code("\n".join(_st["logs"][-30:]))
        time.sleep(1)
        st.rerun()

    # ---- 完成：展示结果 ----
    if _st["done"] and _st["ok"] and _st["data"]:
        data = _st["data"]
        st.divider()
        st.success(f"识别完成！引擎: {data['engine']}，总耗时: {data.get('total_time_s', 0)}s")
        st.subheader("📋 识别结果")
        st.text_area("完整文本", value=data["text"], height=200)

        if data.get("segments"):
            st.divider()
            st.subheader("⏱️ 分段信息")
            has_speaker = any("speaker" in seg and seg["speaker"] is not None for seg in data["segments"])
            if has_speaker:
                current_speaker = None
                for seg in data["segments"]:
                    spk = seg.get("speaker")
                    seg_text = seg.get("text", "")
                    if seg_text.strip():
                        if spk is not None and spk != current_speaker:
                            current_speaker = spk
                            st.markdown(f"**说话人 {spk}**")
                        st.write(f"{seg.get('start_ms', 0)/1000:.1f}s-{seg.get('end_ms', 0)/1000:.1f}s: {seg_text}")
            else:
                for seg in data["segments"]:
                    seg_text = seg.get("text", "")
                    if seg_text.strip():
                        st.write(f"{seg.get('start_ms', 0)/1000:.1f}s-{seg.get('end_ms', 0)/1000:.1f}s: {seg_text}")

        st.divider()
        st.info(f"📁 已保存 JSON：`{_st['json_path']}`")
        st.info(f"📁 已保存文本：`{_st['md_path']}`")
        with open(_st["md_path"], "rb") as f:
            st.download_button("📥 另存为 Markdown...", f, file_name=os.path.basename(_st["md_path"]), mime="text/markdown")
        with open(_st["json_path"], "rb") as f:
            st.download_button("📥 另存为 JSON...", f, file_name=os.path.basename(_st["json_path"]), mime="application/json")

    if _st["done"] and not _st["ok"]:
        st.error(f"识别失败: {_st['error'][-500:]}")
# ==================== 语音合成页 ====================
elif page == "🔊 语音合成":
    st.title("🔊 语音合成")
    st.caption("edge-tts（微软云端，快速）| Qwen3-TTS-0.6B（本地离线，多音色+指令控制+声音克隆）")

    # 共享任务状态（跨 rerun 保留，切换菜单再回来不丢）
    _st = _bt_store().setdefault("tts", {
        "running": False, "done": False, "ok": False, "status": "",
        "output_path": None, "audio_mime": "audio/mp3", "success_msg": "",
        "metrics": None, "error": "",
    })

    # Qwen3-TTS 模型懒加载（st.cache_resource 跨 rerun 缓存同一模型对象，
    # 且须在主线程调用（点击按钮时）加载，后台线程只持有引用使用）
    @st.cache_resource(show_spinner=False)
    def _load_qwen_cv_model():
        import torch as _torch
        from qwen_tts import Qwen3TTSModel
        return Qwen3TTSModel.from_pretrained(
            os.path.expanduser("~/Desktop/星小辰工作空间/models/tts/Qwen3-TTS-12Hz-0.6B-CustomVoice"),
            device_map="cpu", dtype=_torch.float32,
        )

    @st.cache_resource(show_spinner=False)
    def _load_qwen_base_model():
        import torch as _torch
        from qwen_tts import Qwen3TTSModel
        return Qwen3TTSModel.from_pretrained(
            os.path.expanduser("~/Desktop/星小辰工作空间/models/tts/Qwen3-TTS-12Hz-0.6B-Base"),
            device_map="cpu", dtype=_torch.float32,
        )

    # 后台线程：edge-tts
    def _edge_worker(text_val, voice_id, rate, volume, pitch):
        try:
            _st["status"] = "🔊 正在生成（edge-tts）..."
            output_path, error = synthesize_speech(text_val, voice_id, rate=rate, volume=volume, pitch=pitch)
            if output_path:
                _st.update(ok=True, output_path=output_path, audio_mime="audio/mp3",
                           success_msg="生成完成！（edge-tts）", status="✅ 生成完成")
                log_activity("语音合成(edge-tts)", f"音色={voice_id} | 文本={text_val[:60]}")
            else:
                _st["error"] = error
                _st["status"] = f"❌ 生成失败: {error}"
                log_activity("语音合成(edge-tts)", f"失败: {error}", status="error")
        except Exception as e:
            _st["error"] = str(e)
            _st["status"] = f"❌ 生成出错: {e}"
        finally:
            _st["done"] = True
            _st["running"] = False

    # 后台线程：Qwen3-TTS 预置音色
    def _qwen_worker(text_val, speaker_id, lang_id, instruct_val, model, output_path):
        try:
            _st["status"] = "🔊 正在合成（Qwen3-TTS）..."
            t0 = time.time()
            kwargs = dict(text=text_val, language=lang_id, speaker=speaker_id)
            if instruct_val.strip():
                kwargs["instruct"] = instruct_val.strip()
            wavs, sr = model.generate_custom_voice(**kwargs)
            infer_time = time.time() - t0
            import soundfile as _sf
            _sf.write(output_path, wavs[0], sr)
            audio_dur = len(wavs[0]) / sr
            rtf = infer_time / audio_dur if audio_dur > 0 else 0
            _st.update(ok=True, output_path=output_path, audio_mime="audio/wav",
                       success_msg="生成完成！（Qwen3-TTS 预置音色，本地离线）", status="✅ 生成完成",
                       metrics={"合成耗时": f"{infer_time:.1f}s", "音频时长": f"{audio_dur:.1f}s", "RTF (实时率)": f"{rtf:.2f}"})
            log_activity("语音合成(Qwen3-TTS)", f"音色={speaker_id} | 文本={text_val[:60]} | RTF={rtf:.2f}", duration=infer_time)
        except Exception as e:
            _st["error"] = str(e)
            _st["status"] = f"❌ 合成失败: {e}"
            log_activity("语音合成(Qwen3-TTS)", f"失败: {e}", status="error")
        finally:
            _st["done"] = True
            _st["running"] = False

    # 后台线程：声音克隆
    def _clone_worker(text_val, lang_id, ref_tmp, ref_text, xvec_only, model, output_path):
        try:
            _st["status"] = "🔊 正在克隆合成..."
            t0 = time.time()
            wavs, sr = model.generate_voice_clone(
                text=text_val,
                language=lang_id,
                ref_audio=ref_tmp,
                ref_text=ref_text if not xvec_only else None,
                x_vector_only_mode=xvec_only,
                non_streaming_mode=True,
            )
            infer_time = time.time() - t0
            try:
                os.remove(ref_tmp)
            except OSError:
                pass
            import soundfile as _sf
            _sf.write(output_path, wavs[0], sr)
            audio_dur = len(wavs[0]) / sr
            rtf = infer_time / audio_dur if audio_dur > 0 else 0
            _mode_label = "X-vector" if xvec_only else "ICL"
            _st.update(ok=True, output_path=output_path, audio_mime="audio/wav",
                       success_msg=f"克隆生成完成！（{_mode_label} 模式，本地离线）", status="✅ 生成完成",
                       metrics={"克隆耗时": f"{infer_time:.1f}s", "音频时长": f"{audio_dur:.1f}s", "RTF (实时率)": f"{rtf:.2f}"})
            log_activity("语音合成(克隆)", f"模式={_mode_label} | 文本={text_val[:60]} | RTF={rtf:.2f}", duration=infer_time)
        except Exception as e:
            _st["error"] = str(e)
            _st["status"] = f"❌ 克隆失败: {e}"
        finally:
            _st["done"] = True
            _st["running"] = False

    text = st.text_area("输入文本", value="你好，我是本地AI工厂的语音合成模块。今天天气真不错，适合出门走走。", height=80)

    tts_engine = st.radio("合成引擎", ["edge-tts（微软云端）", "Qwen3-TTS（本地离线）"], horizontal=True)

    if "edge-tts" in tts_engine:
        col1, col2 = st.columns(2)
        with col1:
            voice = st.selectbox("语音", [
                "zh-CN-XiaoxiaoNeural (女声-温暖)",
                "zh-CN-XiaoyiNeural (女声-活泼)",
                "zh-CN-YunjianNeural (男声-激情)",
                "zh-CN-YunxiNeural (男声-阳光)",
                "zh-CN-YunyangNeural (男声-专业)",
            ])
        with st.expander("⚙️ 参数设置"):
            col_a, col_b = st.columns(2)
            with col_a:
                rate = st.slider("语速", -50, 50, 0, help="负数变慢，正数变快")
                volume = st.slider("音量", -50, 50, 0, help="负数变小，正数变大")
            with col_b:
                pitch = st.slider("音调", -50, 50, 0, help="负数变低，正数变高")

        if st.button("🔊 生成语音", type="primary", key="btn_edge_tts", disabled=_st["running"]):
            voice_id = voice.split(" ")[0]
            _st.update(running=True, done=False, ok=False, status="🔊 正在生成（edge-tts）...",
                       output_path="", audio_mime="audio/mp3", success_msg="", error="", metrics=None)
            threading.Thread(target=_edge_worker, args=(text, voice_id, rate, volume, pitch), daemon=True).start()
            st.rerun()

    else:
        # ── Qwen3-TTS 本地离线合成 ──
        _qwen_tts_cv_path = os.path.expanduser("~/Desktop/星小辰工作空间/models/tts/Qwen3-TTS-12Hz-0.6B-CustomVoice")
        _qwen_tts_base_path = os.path.expanduser("~/Desktop/星小辰工作空间/models/tts/Qwen3-TTS-12Hz-0.6B-Base")

        if not os.path.exists(_qwen_tts_cv_path):
            st.warning("Qwen3-TTS CustomVoice 模型未找到，请确认路径: " + _qwen_tts_cv_path)
        else:
            # 子模式选择：预置音色 / 声音克隆
            tts_submode = st.radio("合成模式", ["预置音色（9种音色+指令控制）", "声音克隆（上传参考音频）"], horizontal=True)

            if "预置音色" in tts_submode:
                st.info("Qwen3-TTS 首次加载约 1 秒，CPU 推理约 6-15 秒（取决于文本长度）。支持 9 种预置音色、10 种语言、自然语言指令控制。")

                speaker_options = [
                    "Vivian (女声-明亮，中文)",
                    "Serena (女声-温柔，中文)",
                    "Uncle_Fu (男声-沉稳，中文)",
                    "Dylan (男声-北京腔，中文)",
                    "Eric (男声-四川腔，中文)",
                    "Ryan (男声-动感，英语)",
                    "Aiden (男声-阳光，英语)",
                    "Ono_Anna (女声-活泼，日语)",
                    "Sohee (女声-温暖，韩语)",
                ]
                speaker_sel = st.selectbox("说话人（音色）", speaker_options, help="推荐使用每个音色的母语以获得最佳效果")

                lang_options = ["Auto（自动检测）", "Chinese（中文）", "English（英语）", "Japanese（日语）", "Korean（韩语）", "German（德语）", "French（法语）", "Russian（俄语）", "Portuguese（葡萄牙语）", "Spanish（西班牙语）", "Italian（意大利语）"]
                lang_sel = st.selectbox("语言", lang_options, index=0)

                instruct_val = st.text_input(
                    "指令（可选，如：用愤怒的语气说 / 语速放慢 / 温柔地读）",
                    value="",
                    help="用自然语言描述你想要的语气、情感、语速等效果，留空则使用默认风格"
                )

                if st.button("🔊 生成语音（Qwen3-TTS）", type="primary", key="btn_qwen_tts", disabled=_st["running"]):
                    output_path = os.path.join(OUTPUT_AUDIO_TTS, f"tts_qwen3_{int(time.time())}.wav")
                    os.makedirs(OUTPUT_AUDIO_TTS, exist_ok=True)
                    try:
                        _model = _load_qwen_cv_model()  # 主线程加载（首次约30s）
                    except Exception as e:
                        _st.update(done=True, ok=False, error=f"模型加载失败: {e}", status="❌ 模型加载失败")
                        st.rerun()
                    else:
                        speaker_id = speaker_sel.split(" ")[0]
                        lang_map = {
                            "Auto": "Auto", "Chinese": "Chinese", "English": "English",
                            "Japanese": "Japanese", "Korean": "Korean", "German": "German",
                            "French": "French", "Russian": "Russian", "Portuguese": "Portuguese",
                            "Spanish": "Spanish", "Italian": "Italian",
                        }
                        lang_id = lang_map[lang_sel.split("（")[0]]
                        _st.update(running=True, done=False, ok=False, status="🔊 正在合成（Qwen3-TTS）...",
                                   output_path="", audio_mime="audio/wav", success_msg="", error="", metrics=None)
                        threading.Thread(target=_qwen_worker, args=(text, speaker_id, lang_id, instruct_val, _model, output_path), daemon=True).start()
                        st.rerun()

            else:
                # ── 声音克隆模式 ──
                _base_available = os.path.exists(_qwen_tts_base_path)

                if not _base_available:
                    st.warning("Qwen3-TTS Base 模型未找到，声音克隆需要 Base 模型: " + _qwen_tts_base_path)
                else:
                    st.info("上传一段参考音频（3秒以上），AI 会克隆该音色来朗读你输入的文本。支持两种模式：ICL（需参考文本，效果更好）和 X-vector（只需音频，更方便）。")

                    clone_mode = st.radio("克隆模式", ["X-vector（只需音频，更方便）", "ICL（需参考文本，效果更好）"], horizontal=True)

                    ref_audio_file = st.file_uploader("上传参考音频", type=["wav", "mp3", "flac"], help="上传一段清晰的参考音频（建议 3-10 秒）")

                    ref_text_val = ""
                    if "ICL" in clone_mode:
                        ref_text_val = st.text_input("参考文本", value="", help="输入参考音频对应的文字内容（ICL 模式必填）")

                    clone_lang_options = ["Auto（自动检测）", "Chinese（中文）", "English（英语）", "Japanese（日语）", "Korean（韩语）", "German（德语）", "French（法语）", "Russian（俄语）", "Portuguese（葡萄牙语）", "Spanish（西班牙语）", "Italian（意大利语）"]
                    clone_lang_sel = st.selectbox("语言", clone_lang_options, index=0, key="clone_lang")

                    if st.button("🔊 克隆生成语音", type="primary", key="btn_voice_clone", disabled=_st["running"]):
                        if ref_audio_file is None:
                            st.error("请先上传参考音频")
                        elif "ICL" in clone_mode and not ref_text_val.strip():
                            st.error("ICL 模式需要填写参考文本")
                        else:
                            output_path = os.path.join(OUTPUT_AUDIO_TTS, f"tts_clone_{int(time.time())}.wav")
                            os.makedirs(OUTPUT_AUDIO_TTS, exist_ok=True)
                            try:
                                _base_model = _load_qwen_base_model()  # 主线程加载（首次约30s）
                            except Exception as e:
                                _st.update(done=True, ok=False, error=f"模型加载失败: {e}", status="❌ 模型加载失败")
                                st.rerun()
                            else:
                                ref_tmp = os.path.join(OUTPUT_AUDIO_TTS, f"_ref_tmp_{int(time.time())}.wav")
                                with open(ref_tmp, "wb") as f:
                                    f.write(ref_audio_file.getvalue())
                                _xvec_only = "X-vector" in clone_mode
                                lang_map = {
                                    "Auto": "Auto", "Chinese": "Chinese", "English": "English",
                                    "Japanese": "Japanese", "Korean": "Korean", "German": "German",
                                    "French": "French", "Russian": "Russian", "Portuguese": "Portuguese",
                                    "Spanish": "Spanish", "Italian": "Italian",
                                }
                                _lang_id = lang_map[clone_lang_sel.split("（")[0]]
                                _st.update(running=True, done=False, ok=False, status="🔊 正在克隆声音...",
                                           output_path="", audio_mime="audio/wav", success_msg="", error="", metrics=None)
                                threading.Thread(target=_clone_worker, args=(text, _lang_id, ref_tmp, ref_text_val.strip(), _xvec_only, _base_model, output_path), daemon=True).start()
                                st.rerun()

    # ---- 运行中：显示进度（切换菜单后回来仍续显）----
    if _st["running"]:
        st.divider()
        st.subheader("🔊 合成进度（后台运行中，可切换菜单，返回后自动续显）")
        st.info(_st["status"])
        time.sleep(1)
        st.rerun()

    # ---- 完成：展示结果 ----
    if _st["done"] and _st["ok"] and _st["output_path"]:
        st.divider()
        st.success(_st["success_msg"])
        with open(_st["output_path"], "rb") as f:
            st.audio(f.read(), format=_st["audio_mime"])
        tts_filename = os.path.basename(_st["output_path"])
        st.info(f"📁 已保存到：`{_st['output_path']}`")
        with open(_st["output_path"], "rb") as f:
            st.download_button("📥 另存为...", f, file_name=tts_filename, mime=_st["audio_mime"])
        if _st.get("metrics"):
            _cols = st.columns(3)
            for _i, (_k, _v) in enumerate(_st["metrics"].items()):
                with _cols[_i]:
                    st.metric(_k, _v)

    if _st["done"] and not _st["ok"]:
        st.error(f"合成失败: {_st['error']}")

    # 历史音频
    st.divider()
    st.subheader("📁 历史音频")
    audio_dir = OUTPUT_AUDIO_TTS
    if os.path.exists(audio_dir):
        audios = [f for f in os.listdir(audio_dir) if f.endswith(('.mp3', '.wav'))]
        for audio_file in sorted(audios)[-5:]:
            st.audio(os.path.join(audio_dir, audio_file))
# ==================== 智能问答页 ====================
elif page == "📚 智能问答":
    st.title("📚 智能问答")
    st.caption("RAGFlow 向量检索 + FTS5 本地全文搜索 + LLM 推理")

    # RAGFlow 使用 API Key 认证
    ragflow_headers = {"Authorization": f"Bearer {RAGFLOW_API_KEY}"}

    tab0, tab1, tab2, tab3 = st.tabs(["💬 智能问答", "🔍 语义搜索 (RAGFlow)", "📁 本地全文搜索 (FTS5)", "📋 文档列表"])

    with tab0:
        st.subheader("智能问答")
        st.caption("自然语言提问 → RAGFlow 检索 + LLM 推理 → 结构化回答（含溯源）")

        # 默认知识库 ID
        DEFAULT_DATASET = "87f65a229ebe11f192791d16a28c2dcb"

        # 问答输入
        qa_query = st.text_input(
            "提问",
            placeholder="例：鹤壁公司7月核心平台基础类收入同比增幅是多少？",
            key="qa_query_input"
        )

        # 参数控制
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            top_k = st.slider("检索文档数 (top_k)", 3, 15, 8, key="qa_top_k")
        with col_b:
            sim_threshold = st.slider("相似度阈值", 0.0, 1.0, 0.2, 0.05, key="qa_sim_threshold")
        with col_c:
            # 动态获取可用模型列表（只显示 source=config 的 provider 的模型）
            try:
                # /api/models 端点已过滤 source=config，只返回可调用的 provider
                _providers_resp = requests.get(f"{LLM_PROXY_URL}/api/models", timeout=5)
                _available_models = []
                if _providers_resp.status_code == 200:
                    for p in _providers_resp.json().get("providers", []):
                        for m in p.get("models", []):
                            full_id = m.get("full_id", "")
                            if full_id and "embedding" not in full_id.lower():
                                _available_models.append(full_id)
                if not _available_models:
                    _available_models = ["NewApi/chat-pro"]
            except:
                _available_models = ["NewApi/chat-pro"]
            # 把 NewApi/chat-pro 排到第一位作为缺省模型
            if "NewApi/chat-pro" in _available_models:
                _available_models.remove("NewApi/chat-pro")
                _available_models.insert(0, "NewApi/chat-pro")
            llm_model = st.selectbox("LLM", _available_models, index=0, key="qa_llm_model")

        ask_clicked = st.button("🚀 提问", type="primary", key="qa_ask", use_container_width=True)

        if ask_clicked and qa_query:
            import traceback

            # Step 1: RAGFlow 检索
            with st.status("🔍 RAGFlow 语义检索中...", expanded=True) as status1:
                t0 = time.time()
                try:
                    retrieval_resp = requests.post(
                        f"{RAGFLOW_URL}/api/v1/retrieval",
                        headers=ragflow_headers,
                        json={
                            "question": qa_query,
                            "dataset_ids": [DEFAULT_DATASET],
                            "page": 1,
                            "page_size": top_k,
                            "similarity_threshold": sim_threshold,
                        },
                        timeout=30
                    )
                    retrieval_data = retrieval_resp.json()
                    if retrieval_data.get("code") != 0:
                        st.error(f"RAGFlow 检索失败: {retrieval_data.get('message', 'unknown')}")
                        st.stop()

                    chunks = retrieval_data.get("data", {}).get("chunks", [])
                    elapsed_retrieval = time.time() - t0

                    if not chunks:
                        st.warning("未检索到相关文档片段，请尝试调整相似度阈值或换一种问法。")
                        st.stop()

                    # 去重 + 截取，同时构建文档名→本地路径映射
                    seen_docs = set()
                    context_parts = []
                    source_docs = []
                    qa_doc_path_map = {}
                    try:
                        _qa_conn = sqlite3.connect(FTS_DB_PATH)
                        _qa_cur = _qa_conn.cursor()
                    except:
                        _qa_conn = None
                        _qa_cur = None
                    for c in chunks:
                        doc_name = c.get("document_keyword", "未知文档")
                        content = c.get("content", "").strip()
                        if not content:
                            continue
                        if doc_name not in seen_docs:
                            seen_docs.add(doc_name)
                            source_docs.append(doc_name)
                            # 查找本地文件路径
                            if _qa_cur:
                                _qa_cur.execute("SELECT file_path FROM files WHERE file_name = ? LIMIT 1", (doc_name,))
                                _qa_row = _qa_cur.fetchone()
                                if _qa_row:
                                    qa_doc_path_map[doc_name] = _qa_row[0]
                        context_parts.append(f"【来源: {doc_name}】\n{content}")
                    if _qa_conn:
                        _qa_conn.close()

                    context = "\n\n---\n\n".join(context_parts[:top_k])
                    st.write(f"检索到 {len(chunks)} 个片段，来自 {len(seen_docs)} 篇文档（耗时 {elapsed_retrieval:.1f}s）")
                    for d in source_docs[:5]:
                        st.write(f"  - {d}")
                    status1.update(label=f"✅ 检索完成（{elapsed_retrieval:.1f}s）", state="complete")

                except Exception as e:
                    status1.update(label="❌ 检索失败", state="error")
                    st.error(f"检索出错: {str(e)}")
                    st.stop()

            # Step 2: LLM 推理
            with st.status("🧠 LLM 推理分析中...", expanded=True) as status2:
                t1 = time.time()

                system_prompt = """你是河南电信收入经营分析专家。根据检索到的文档内容回答用户问题。

要求：
1. 从文档中提取具体数据，不要编造
2. 如果数据包含数值，用 markdown 表格结构化展示
3. 表格要有清晰的表头
4. 如果有排名信息，也要列出
5. 在回答最后附上"趋势评价"（如上升/下降/持平）
6. 回答开头标注数据来源文件名
7. 如果文档中没有相关数据，明确告知用户

格式示例：
数据来自《文件名》：

[指标或主题标题]

| 指标 | 数值 | 全省排名 |
|------|------|----------|
| ... | ... | ... |

趋势评价：xxx
"""

                user_message = f"""检索到的文档内容：

{context}

---

用户问题：{qa_query}

请根据以上文档内容回答。只使用文档中的数据，不要编造。"""

                try:
                    llm_payload = {
                        "model": llm_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message}
                        ],
                        "temperature": 0.3,
                        "max_tokens": 2048,
                        "stream": False
                    }
                    llm_resp = requests.post(
                        f"{LLM_PROXY_URL}/v1/chat/completions",
                        json=llm_payload,
                        timeout=120
                    )

                    if llm_resp.status_code != 200:
                        st.error(f"LLM 调用失败: HTTP {llm_resp.status_code} - {llm_resp.text[:200]}")
                        st.stop()

                    llm_result = llm_resp.json()
                    answer = llm_result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    usage = llm_result.get("usage", {})
                    elapsed_llm = time.time() - t1

                    # 更新 token 统计
                    total_tokens = usage.get("total_tokens", 0)
                    st.session_state.token_stats["total_tokens"] += total_tokens
                    st.session_state.token_stats["total_requests"] += 1
                    st.session_state.token_stats["history"].append({
                        "time": datetime.now().strftime("%H:%M"),
                        "tokens": total_tokens,
                        "elapsed": elapsed_llm
                    })

                    st.write(f"LLM 推理完成（耗时 {elapsed_llm:.1f}s, {total_tokens} tokens）")
                    status2.update(label=f"✅ 推理完成（{elapsed_llm:.1f}s）", state="complete")

                except Exception as e:
                    status2.update(label="❌ 推理失败", state="error")
                    st.error(f"LLM 推理出错: {str(e)}")
                    st.stop()

            # Step 3: 展示结果
            log_activity("智能问答", f"问题={qa_query[:50]} | 模型={llm_model} | tokens={total_tokens} | 耗时={time.time()-t0:.1f}s")

            # 缓存结果到 session_state（让打开文件按钮能正常工作）
            st.session_state['qa_result'] = {
                'answer': answer,
                'elapsed_retrieval': elapsed_retrieval,
                'elapsed_llm': elapsed_llm,
                'total_tokens': total_tokens,
                'source_docs': source_docs,
                'qa_doc_path_map': qa_doc_path_map,
                'chunks': chunks[:top_k],
                'query': qa_query,
            }

        # 从缓存渲染问答结果（放在 if ask_clicked 外面，确保打开文件按钮能工作）
        if 'qa_result' in st.session_state and st.session_state['qa_result']:
            _qr = st.session_state['qa_result']
            st.divider()

            # 渲染回答
            st.markdown(_qr['answer'])

            # 元信息
            st.divider()
            meta_col1, meta_col2, meta_col3 = st.columns(3)
            with meta_col1:
                st.caption(f"🔍 检索耗时: {_qr['elapsed_retrieval']:.1f}s")
            with meta_col2:
                st.caption(f"🧠 推理耗时: {_qr['elapsed_llm']:.1f}s")
            with meta_col3:
                st.caption(f"📊 Tokens: {_qr['total_tokens']:,}")

            # 来源文档列表 + 打开按钮
            _src_docs = _qr['source_docs']
            _doc_paths = _qr['qa_doc_path_map']
            with st.expander(f"📚 数据来源文档（{len(_src_docs)} 篇）", expanded=False):
                for i, d in enumerate(_src_docs):
                    _dp = _doc_paths.get(d, "")
                    with st.container(border=True):
                        st.markdown(f"📄 **{d}**")
                        # 路径失效时自动修复
                        if not (_dp and os.path.exists(_dp)):
                            _fixed_dp, _fix_src = resolve_file_path(d)
                            if _fixed_dp:
                                _dp = _fixed_dp
                                _doc_paths[d] = _dp  # 更新缓存
                        if _dp and os.path.exists(_dp):
                            _c1, _c2 = st.columns(2)
                            with _c1:
                                if st.button("📂 打开文件", key=f"qa_open_{i}"):
                                    os.system(f'open "{_dp}"')
                            with _c2:
                                if st.button("📁 打开所在文件夹", key=f"qa_open_dir_{i}"):
                                    os.system(f'open -R "{_dp}"')
                        else:
                            st.caption("⚠️ 本地未找到此文件路径")

            # 引用的检索片段
            _qr_chunks = _qr['chunks']
            with st.expander("🔎 检索片段详情", expanded=False):
                for i, c in enumerate(_qr_chunks, 1):
                    doc_name = c.get("document_keyword", "?")
                    content = c.get("content", "").strip()
                    sim = c.get("similarity", 0)
                    with st.container(border=True):
                        st.markdown(f"**片段 {i}** — `{doc_name}` (相似度: {sim:.3f})")
                        st.text(content[:500] + ("..." if len(content) > 500 else ""))

    with tab1:
        st.subheader("语义搜索")
        st.caption("基于 RAGFlow 向量检索，适合模糊语义匹配")

        # 动态获取文档列表
        kb_list = []
        try:
            resp = requests.get(
                f"{RAGFLOW_URL}/api/v1/datasets?page=1&page_size=100",
                headers=ragflow_headers, timeout=10
            )
            if resp.status_code == 200 and resp.json().get("code") == 0:
                kb_list = resp.json().get("data", [])
        except requests.RequestException:
            pass

        if not kb_list:
            st.warning("无法连接 RAGFlow 服务或暂无知识库，请检查端口 8086 是否运行")
        else:
            kb_options = {f"{kb['name']} ({kb.get('document_count',0)}篇)": kb['id'] for kb in kb_list}
            kb_options['全部文档库'] = ""

            selected_kb = st.selectbox("选择文档库", list(kb_options.keys()), index=len(kb_options)-1)
            query = st.text_input("搜索内容", placeholder="输入关键词...", key="ragflow_query")

            if st.button("🔍 搜索", type="primary", key="ragflow_search") and query:
                with st.spinner("搜索中..."):
                    try:
                        if kb_options[selected_kb]:
                            search_dataset_ids = [kb_options[selected_kb]]
                        else:
                            search_dataset_ids = [kb['id'] for kb in kb_list]

                        payload = {
                            "question": query,
                            "dataset_ids": search_dataset_ids,
                            "top_k": 10,
                        }
                        resp = requests.post(
                            f"{RAGFLOW_URL}/api/v1/retrieval",
                            json=payload, headers=ragflow_headers, timeout=30
                        )

                        all_results = []
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("code") == 0:
                                for chunk in data.get("data", {}).get("chunks", []):
                                    all_results.append({
                                        "content": chunk.get("content", ""),
                                        "document": chunk.get("document_keyword", "未知"),
                                        "similarity": chunk.get("similarity", 0),
                                    })

                        if all_results:
                            all_results.sort(key=lambda x: x["similarity"], reverse=True)
                            st.success(f"找到 {len(all_results)} 条相关结果")
                            log_activity("RAGFlow搜索", f"关键词={query} | 结果数={len(all_results)}")

                            # 构建 文档名→本地路径 的映射
                            doc_path_map = {}
                            try:
                                conn = sqlite3.connect(FTS_DB_PATH)
                                cur2 = conn.cursor()
                                for r in all_results:
                                    doc_name = r["document"]
                                    if doc_name not in doc_path_map:
                                        cur2.execute("SELECT file_path FROM files WHERE file_name = ? LIMIT 1", (doc_name,))
                                        row = cur2.fetchone()
                                        if row:
                                            doc_path_map[doc_name] = row[0]
                                conn.close()
                            except:
                                pass

                            # 缓存到 session_state
                            st.session_state['ragflow_results'] = all_results[:10]
                            st.session_state['ragflow_doc_paths'] = doc_path_map
                        else:
                            st.info("未找到相关内容")
                            st.session_state['ragflow_results'] = []

                    except Exception as e:
                        st.error(f"搜索出错: {str(e)}")

            # 从缓存渲染结果（放在 if 搜索按钮 外面，确保按钮能正常工作）
            if 'ragflow_results' in st.session_state and st.session_state['ragflow_results']:
                _results = st.session_state['ragflow_results']
                _doc_paths = st.session_state.get('ragflow_doc_paths', {})
                import re as _re
                for i, r in enumerate(_results):
                    doc_name = r["document"]
                    doc_path = _doc_paths.get(doc_name, "")
                    with st.container(border=True):
                        st.markdown(f"### {i+1}. 📄 {doc_name}")
                        st.caption(f"相似度: {r['similarity']:.4f}")
                        # 高亮显示摘要
                        _content = r["content"]
                        _snippet_md = _re.sub(r'【(.+?)】', r'**:orange[\1]**', _content)
                        st.markdown(f"> {_snippet_md}")
                        # 路径失效时自动修复
                        if not (doc_path and os.path.exists(doc_path)):
                            _fixed_dp, _fix_src = resolve_file_path(doc_name)
                            if _fixed_dp:
                                doc_path = _fixed_dp
                                _doc_paths[doc_name] = doc_path
                        if doc_path and os.path.exists(doc_path):
                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("📂 打开文件", key=f"rag_open_{i}"):
                                    os.system(f'open "{doc_path}"')
                            with col2:
                                if st.button("📁 打开所在文件夹", key=f"rag_open_dir_{i}"):
                                    os.system(f'open -R "{doc_path}"')
                        else:
                            st.caption("⚠️ 本地未找到此文件路径")

    with tab2:
        st.subheader("本地全文搜索")
        st.caption("基于 SQLite FTS5 trigram 分词，直接搜索 ~/Desktop/工作/ 下的文件内容")

        if not os.path.exists(FTS_DB_PATH):
            st.error(f"FTS5 索引数据库不存在：{FTS_DB_PATH}")
            st.info("请先运行：python3 build_fts_index.py")
        else:
            # --- 索引统计 ---
            try:
                conn = sqlite3.connect(FTS_DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM files")
                total_files = cur.fetchone()[0]
                cur.execute("SELECT MAX(indexed_at) FROM files")
                last_index = cur.fetchone()[0] or "N/A"
                cur.execute("SELECT category, COUNT(*) FROM files GROUP BY category ORDER BY COUNT(*) DESC")
                cat_stats = cur.fetchall()
                conn.close()

                col_info1, col_info2, col_info3 = st.columns(3)
                with col_info1:
                    st.metric("已索引文件", f"{total_files}")
                with col_info2:
                    st.metric("最后索引时间", last_index[:16] if last_index != "N/A" else "N/A")
                with col_info3:
                    db_size = os.path.getsize(FTS_DB_PATH) / 1024 / 1024
                    st.metric("索引大小", f"{db_size:.1f} MB")
            except Exception as e:
                st.error(f"读取索引统计失败: {e}")
                total_files = 0
                cat_stats = []

            if total_files > 0:
                st.divider()

                @st.fragment
                def fts_search_fragment():
                    # --- 搜索表单 ---
                    col_search1, col_search2, col_search3 = st.columns([2, 1, 1])
                    with col_search1:
                        fts_query = st.text_input(
                            "搜索关键词",
                            placeholder="输入关键词，多词用空格分隔（AND 搜索）...",
                            key="fts_query"
                        )
                    with col_search2:
                        category_options = ["全部分类"] + [c[0] for c in cat_stats]
                        selected_category = st.selectbox("分类过滤", category_options, key="fts_category")
                    with col_search3:
                        ext_options = ["全部类型", "xlsx", "pdf", "pptx", "docx", "xls"]
                        selected_ext = st.selectbox("文件类型", ext_options, key="fts_ext")

                    col_limit, col_btn, col_stats = st.columns([1, 1, 2])
                    with col_limit:
                        result_limit = st.selectbox("结果数量", [10, 20, 50], index=0, key="fts_limit")
                    with col_btn:
                        st.write("")
                        search_clicked = st.button("🔍 搜索", type="primary", key="fts_search", use_container_width=True)
                    with col_stats:
                        st.write("")
                        if st.button("📊 索引统计", key="fts_stats_btn"):
                            st.session_state.show_fts_stats = True

                    # --- 索引统计详情 ---
                    if st.session_state.get("show_fts_stats"):
                        with st.expander("📊 索引统计详情", expanded=True):
                            try:
                                _conn = sqlite3.connect(FTS_DB_PATH)
                                _cur = _conn.cursor()
                                _cur.execute("SELECT file_ext, COUNT(*) FROM files GROUP BY file_ext ORDER BY COUNT(*) DESC")
                                ext_stats = _cur.fetchall()
                                _conn.close()

                                col_s1, col_s2 = st.columns(2)
                                with col_s1:
                                    st.markdown("**按分类：**")
                                    for cat, cnt in cat_stats:
                                        st.markdown(f"- {cat}: {cnt} 个")
                                with col_s2:
                                    st.markdown("**按类型：**")
                                    for ext, cnt in ext_stats:
                                        st.markdown(f"- {ext}: {cnt} 个")
                            except Exception as e:
                                st.error(f"统计失败: {e}")

                    # --- 执行搜索 ---
                    if search_clicked and fts_query:
                        try:
                            _t0 = time.time()
                            conn = sqlite3.connect(FTS_DB_PATH)
                            cur = conn.cursor()
                            raw_terms = fts_query.strip().split()
                            long_groups = []  # 每组是一个词的trigram列表
                            short_terms = []
                            for t in raw_terms:
                                if len(t) >= 6:
                                    # 长词按3字滑窗切分，去重
                                    seen = set()
                                    chunks = []
                                    for i in range(len(t) - 2):
                                        seg = t[i:i+3]
                                        if seg not in seen:
                                            seen.add(seg)
                                            chunks.append(seg)
                                    long_groups.append(chunks)
                                elif len(t) >= 3:
                                    long_groups.append([t])
                                else:
                                    short_terms.append(t)

                            # 构建过滤条件
                            conditions = []
                            params = []
                            if selected_category != "全部分类":
                                conditions.append("f.category = ?")
                                params.append(selected_category)
                            if selected_ext != "全部类型":
                                conditions.append("f.file_ext = ?")
                                params.append(f".{selected_ext}")
                            filter_clause = " AND ".join(conditions) if conditions else "1=1"

                            # 构建FTS5 MATCH表达式：组内OR，组间AND
                            def build_fts_match(groups):
                                parts = []
                                for g in groups:
                                    if len(g) == 1:
                                        parts.append(f'"{g[0]}"')
                                    else:
                                        parts.append('(' + ' OR '.join(f'"{x}"' for x in g) + ')')
                                return ' AND '.join(parts)

                            has_long = len(long_groups) > 0
                            if has_long and not short_terms:
                                mode = 'fts'
                                fts_match = build_fts_match(long_groups)
                            elif has_long and short_terms:
                                mode = 'hybrid'
                                fts_match = build_fts_match(long_groups)
                            elif short_terms and not has_long:
                                mode = 'like'
                                fts_match = None
                            else:
                                mode = 'fts'
                                fts_match = fts_query

                            if mode == 'fts':
                                sql = f"""
                                    SELECT f.file_path, f.file_name, f.file_ext, f.file_dir,
                                           f.category, f.mtime, f.size,
                                           snippet(fts, 0, '【', '】', '…', 30) as snippet,
                                           bm25(fts) as rank
                                    FROM fts
                                    JOIN fts_map m ON fts.rowid = m.fts_rowid
                                    JOIN files f ON m.file_id = f.id
                                    WHERE fts MATCH ?
                                      AND {filter_clause}
                                    ORDER BY rank
                                    LIMIT ?
                                """
                                cur.execute(sql, [fts_match] + params + [result_limit])
                                results = cur.fetchall()

                            elif mode == 'hybrid':
                                like_conditions = " AND ".join(["fts.content LIKE ?" for _ in short_terms])
                                like_params = [f"%{t}%" for t in short_terms]
                                sql = f"""
                                    SELECT f.file_path, f.file_name, f.file_ext, f.file_dir,
                                           f.category, f.mtime, f.size,
                                           snippet(fts, 0, '【', '】', '…', 30) as snippet,
                                           bm25(fts) as rank
                                    FROM fts
                                    JOIN fts_map m ON fts.rowid = m.fts_rowid
                                    JOIN files f ON m.file_id = f.id
                                    WHERE fts MATCH ?
                                      AND {like_conditions}
                                      AND {filter_clause}
                                    ORDER BY rank
                                    LIMIT ?
                                """
                                cur.execute(sql, [fts_match] + like_params + params + [result_limit])
                                results = cur.fetchall()

                            else:  # like mode
                                like_conditions = " AND ".join(["fts.content LIKE ?" for _ in short_terms if short_terms])
                                like_params = [f"%{t}%" for t in short_terms] if short_terms else [f"%{fts_query}%"]
                                sql = f"""
                                    SELECT f.file_path, f.file_name, f.file_ext, f.file_dir,
                                           f.category, f.mtime, f.size,
                                           fts.content, 0.0 as rank
                                    FROM fts
                                    JOIN fts_map m ON fts.rowid = m.fts_rowid
                                    JOIN files f ON m.file_id = f.id
                                    WHERE {like_conditions}
                                      AND {filter_clause}
                                    LIMIT ?
                                """
                                cur.execute(sql, like_params + params + [result_limit])
                                raw_results = cur.fetchall()
                                # 手动提取摘要
                                results = []
                                for row in raw_results:
                                    content = row[7] or ""
                                    # 简单摘要：找到第一个匹配关键词附近的一段
                                    first_pos = -1
                                    first_term = ""
                                    for t in (short_terms if short_terms else [fts_query]):
                                        pos = content.find(t)
                                        if pos >= 0 and (first_pos < 0 or pos < first_pos):
                                            first_pos = pos
                                            first_term = t
                                    if first_pos < 0:
                                        snippet_text = content[:150]
                                    else:
                                        start = max(0, first_pos - 50)
                                        end = min(len(content), first_pos + len(first_term) + 100)
                                        snippet_text = content[start:end]
                                        for t in (short_terms if short_terms else [fts_query]):
                                            snippet_text = snippet_text.replace(t, f"【{t}】")
                                        if start > 0:
                                            snippet_text = "…" + snippet_text
                                        if end < len(content):
                                            snippet_text = snippet_text + "…"
                                    results.append((row[0], row[1], row[2], row[3], row[4], row[5], row[6], snippet_text, row[8]))

                            conn.close()
                            _search_ms = (time.time() - _t0) * 1000
                            st.session_state['fts_results'] = results
                            st.session_state['fts_searched_query'] = fts_query
                            st.session_state['fts_search_ms'] = _search_ms
                            log_activity("FTS5搜索", f"关键词={fts_query} | 分类={selected_category} | 结果数={len(results)} | 耗时={_search_ms:.1f}ms")

                        except Exception as e:
                            st.error(f"搜索出错: {str(e)}")

                    # --- 渲染结果（搜索后或按钮点击后都走这里） ---
                    if 'fts_results' in st.session_state and st.session_state['fts_results']:
                        _results = st.session_state['fts_results']
                        _query = st.session_state.get('fts_searched_query', '')
                        _ms = st.session_state.get('fts_search_ms', 0)
                        st.success(f"找到 {len(_results)} 条结果" + (f"（搜索耗时 {_ms:.0f}ms）" if _ms else ""))
                        for i, row in enumerate(_results, 1):
                            fpath, fname, fext, fdir, category, mtime, size, snippet_text, rank = row
                            size_str = f"{size/1024:.0f}KB" if size < 1024*1024 else f"{size/1024/1024:.1f}MB"
                            file_icon = {
                                '.xlsx': '📊', '.xls': '📊',
                                '.pdf': '📕',
                                '.pptx': '📄',
                                '.docx': '📝',
                                '.txt': '📃', '.md': '📃'
                            }.get(fext, '📄')
                            # 转换摘要高亮：【词】→ Markdown加粗+背景色
                            import re
                            snippet_md = re.sub(r'【(.+?)】', r'**:orange[\1]**', snippet_text)
                            title = f"{file_icon} {fname}"
                            meta_parts = [f"`{category}`", f"`{fext}`", f"`{size_str}`", f"`{mtime[:10]}`"]
                            if rank != 0:
                                meta_parts.append(f"`相关度 {rank:.4f}`")
                            meta = " ".join(meta_parts)
                            with st.container(border=True):
                                st.markdown(f"### {i}. {title}")
                                st.caption(f"📁 {fdir}")
                                st.markdown(meta)
                                st.markdown(f"> {snippet_md}")
                                # 路径失效时自动修复
                                if not os.path.exists(fpath):
                                    _fixed_dp, _ = resolve_file_path(fname)
                                    if _fixed_dp:
                                        fpath = _fixed_dp
                                col1, col2 = st.columns(2)
                                with col1:
                                    if st.button("📂 打开文件", key=f"open_{i}"):
                                        os.system(f'open "{fpath}"')
                                with col2:
                                    if st.button("📁 打开所在文件夹", key=f"open_dir_{i}"):
                                        os.system(f'open -R "{fpath}"')
                    elif search_clicked and fts_query:
                        st.info(f"未找到与 \"{fts_query}\" 相关的内容。", icon='ℹ️')

                fts_search_fragment()

    with tab3:
        st.subheader("文档列表")

        try:
            resp = requests.get(
                f"{RAGFLOW_URL}/api/v1/datasets?page=1&page_size=100",
                headers=ragflow_headers, timeout=10
            )
            kbs = []
            if resp.status_code == 200 and resp.json().get("code") == 0:
                kbs = resp.json().get("data", [])

            if not kbs:
                st.info("暂无知识库")
            else:
                for kb in kbs:
                    col1, col2, col3 = st.columns([3, 1, 1])
                    with col1:
                        st.write(f"📚 **{kb.get('name', '未知')}**")
                    with col2:
                        st.caption(f"文档: {kb.get('document_count', 0)}")
                    with col3:
                        if st.button("查看", key=f"kb_{kb.get('id', '')}"):
                            st.session_state.selected_kb = kb.get('id', '')

        except Exception as e:
            st.error(f"获取文档列表失败: {str(e)}")

        # 显示选中知识库的文档列表
        if st.session_state.get("selected_kb"):
            kb_id = st.session_state.selected_kb
            st.divider()
            st.subheader("📄 文档列表")

            try:
                doc_resp = requests.get(
                    f"{RAGFLOW_URL}/api/v1/datasets/{kb_id}/documents?page=1&page_size=100",
                    headers=ragflow_headers, timeout=10
                )
                docs = []
                total_docs = 0
                if doc_resp.status_code == 200:
                    doc_data = doc_resp.json()
                    if doc_data.get("code") == 0:
                        docs = doc_data.get("data", {}).get("docs", [])
                        total_docs = doc_data.get("data", {}).get("total", len(docs))

                if docs:
                    col1, col2, col3 = st.columns([2, 1, 1])
                    with col1:
                        st.caption(f"共 {total_docs} 篇文档")
                    with col2:
                        rows_per_page = st.selectbox("每页显示", [10, 20, 50, 100], index=0, key="rows_per_page")
                    with col3:
                        total_pages = max(1, (len(docs) + rows_per_page - 1) // rows_per_page)
                        page_num = st.number_input("页码", min_value=1, max_value=total_pages, value=1, key="page_num")

                    start_idx = (page_num - 1) * rows_per_page
                    end_idx = min(start_idx + rows_per_page, len(docs))
                    page_docs = docs[start_idx:end_idx]

                    for doc in page_docs:
                        # RAGFlow: run="DONE" 表示解析完成, status="1" 表示已启用
                        status_icon = "✅" if doc.get("run") == "DONE" else "⏳"
                        size_str = f"{doc.get('size', 0)/1024:.0f}KB" if doc.get('size', 0) < 1024*1024 else f"{doc.get('size', 0)/1024/1024:.1f}MB"
                        doc_name = doc.get('name', '未知')
                        chunk_count = doc.get('chunk_count', 0)
                        token_count = doc.get('token_count', 0)
                        st.write(f"{status_icon} {doc_name} ({chunk_count} chunks, {token_count:,} tokens, {size_str})")

                    st.divider()
                    col1, col2, col3 = st.columns([1, 2, 1])
                    with col2:
                        st.caption(f"第 {page_num}/{total_pages} 页 | 显示 {start_idx+1}-{end_idx} 条")
                else:
                    st.info("暂无文档")
            except Exception as e:
                st.error(f"获取文档列表失败: {str(e)}")


# ==================== Token 统计页 ====================
elif page == "📈 Token 统计":
    st.title("📈 Token 使用统计")

    stats = st.session_state.token_stats

    # 总览卡片
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("总 Token 数", f"{stats['total_tokens']:,}")
    with col2:
        st.metric("总请求数", f"{stats['total_requests']:,}")
    with col3:
        uptime = datetime.now() - stats["start_time"]
        st.metric("运行时间", f"{uptime.seconds // 3600}h {(uptime.seconds % 3600) // 60}m")
    with col4:
        if stats["total_requests"] > 0:
            avg = stats["total_tokens"] / stats["total_requests"]
            st.metric("平均 Token/请求", f"{avg:.0f}")

    st.divider()

    # Token 使用趋势
    if stats["history"]:
        st.subheader("📊 Token 使用趋势")

        history_list = list(stats["history"])
        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=[h["time"] for h in history_list],
            y=[h["tokens"] for h in history_list],
            name='Tokens',
            marker_color='#7c3aed'
        ))
        fig.update_layout(
            height=400,
            margin=dict(l=20, r=20, t=20, b=20),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
        )

        st.plotly_chart(fig, use_container_width=True)

        # 速度趋势
        st.subheader("⚡ 推理速度趋势")
        fig_speed = go.Figure()
        fig_speed.add_trace(go.Scatter(
            x=[h["time"] for h in history_list],
            y=[h["tokens"] / h["elapsed"] if h["elapsed"] > 0 else 0 for h in history_list],
            mode='lines+markers',
            name='tok/s',
            line=dict(color='#ec4899', width=2)
        ))
        fig_speed.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=20, b=20),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
        )
        st.plotly_chart(fig_speed, use_container_width=True)

        # 详细记录
        st.subheader("📋 详细记录")
        import pandas as pd
        df = pd.DataFrame(history_list)
        df.columns = ["时间", "Token数", "耗时(秒)"]
        df["速度(tok/s)"] = (df["Token数"] / df["耗时(秒)"]).round(1)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("暂无 Token 使用记录，请先进行文本对话")


# ==================== 日志查看页 ====================
elif page == "📋 日志查看":
    st.title("📋 日志查看")

    # 日志文件映射：显示名 → 路径
    LOG_FILES = {
        "📝 操作日志（AI工厂活动）": ACTIVITY_LOG_FILE,
        "WebUI 主日志": "/tmp/streamlit-webui.log",
        "AI 看门狗": "/tmp/ai-watchdog.log",
        "路由服务器 (8082)": "/tmp/router-server.log",
        "ComfyUI (8188)": "/tmp/comfyui_main.log",
        "ComfyUI 次日志": "/tmp/comfyui.log",
        "收入看板 (8503)": "/tmp/dashboard.log",
        "网格收入看板 (8510)": "/tmp/grid-income.log",
        "Token 日报": "/tmp/token-daily-report.log",
        "VLM 推理": "/tmp/vlm_stderr.log",
        "量子密信隧道": "/tmp/zmx-tunnel.log",
    }

    # 过滤出实际存在的日志
    available_logs = {}
    for name, path in LOG_FILES.items():
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            available_logs[name] = path

    if not available_logs:
        st.warning("暂无可用日志文件")
    else:
        col_sel, col_lines, col_search = st.columns([3, 1, 2])
        with col_sel:
            log_name = st.selectbox("选择日志", list(available_logs.keys()))
            log_path = available_logs[log_name]
        with col_lines:
            tail_lines = st.selectbox("显示行数", [50, 100, 200, 500, 1000, "全部"], index=1)
        with col_search:
            keyword = st.text_input("搜索过滤（可选）", value="", placeholder="输入关键词过滤")

        # 获取文件信息
        log_size = os.path.getsize(log_path)
        log_mtime = datetime.fromtimestamp(os.path.getmtime(log_path)).strftime("%Y-%m-%d %H:%M:%S")
        st.caption(f"📁 {log_path} | {log_size / 1024:.1f}KB | 最后更新: {log_mtime}")

        # 读取日志
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            # 关键词过滤
            if keyword.strip():
                kw = keyword.strip().lower()
                all_lines = [l for l in all_lines if kw in l.lower()]

            # 截取尾部
            if tail_lines != "全部":
                display_lines = all_lines[-tail_lines:]
            else:
                display_lines = all_lines

            total = len(all_lines)
            shown = len(display_lines)
            st.info(f"共 {total} 行" + (f"（关键词「{keyword}」过滤后）" if keyword.strip() else "") + f"，显示最后 {shown} 行")

            # 显示日志
            log_text = "".join(display_lines)
            st.code(log_text, language="text", height=500)

            # 下载按钮
            st.download_button(
                label="📥 下载完整日志",
                data=log_text.encode("utf-8"),
                file_name=os.path.basename(log_path),
                mime="text/plain",
            )
        except Exception as e:
            st.error(f"读取日志失败: {e}")


# ==================== AI 工厂说明页 ====================
elif page == "📖 AI工厂说明":
    st.title("📖 AI 工厂说明")

    import textwrap
    intro_md = textwrap.dedent("""\
    ---
    ## 🏭 项目简介

    **本地 AI 工厂** 是基于 MacBook Pro M5 Max / 137GB 搭建的**全本地多模态 AI 环境**，所有模型在本地运行，无需联网，数据不出设备。

    > 作者：李准的星小辰 · 版本：v{APP_VERSION}

    ---

    ## 🧩 功能模块

    | 模块 | 功能 | 技术栈 |
    |------|------|--------|
    | 📊 系统监控 | CPU/内存/磁盘/GPU 实时监控，服务状态，模型进程 | Streamlit + psutil + Plotly |
    | 🧠 文本对话 | 多模型对话，支持上下文，Token 统计 | mlx-lm / Ollama（Qwen3.8-27B 主力） |
    | 🔬 模型对比 | 同一 prompt 并行调用多个模型，横向对比 | MLX 多端口并发 |
    | 👁️ 图片理解 | 图片上传 + AI 描述/OCR/问答 | Qwen3.8-27B-4bit（多模态） |
    | 🎬 视频理解 | 视频抽帧 + AI 分析 | Qwen3.8-27B-4bit |
    | 🎨 图片生成 | 文生图，1024×1024 | ComfyUI + SDXL / SANA |
    | 🎬 视频生成 | 文生视频，5 秒 24fps | ComfyUI + MiniMax H3 4-bit |
    | 🎤 语音识别 | 音频转文字，多人说话分离 | SenseVoiceSmall / Seaco-Paraformer + cam++ |
    | 🔊 语音合成 | 文字转语音，音色克隆 | Qwen3-TTS-0.6B + edge-tts |
    | 📚 智能问答 | 知识库问答 + 全文搜索 | RAGFlow (9380) + FTS5 本地索引 |
    | 📈 Token 统计 | 各模型使用量/费用统计 | 内置 Token 计数器 |
    | 📋 日志查看 | AI 工厂操作日志 + 模型服务日志 | /tmp/ai-factory-activity.log |

    ---

    ## 🤖 模型清单

    ### 文本大模型 (LLM)
    | 模型 | 大小 | 速度 | 用途 |
    |------|------|------|------|
    | Qwen3.8-27B-4bit | 15 GB | ~31.5 tps | 日常主力（常驻 8082），多模态兼视觉理解 |
    | Qwen3.6-35B-A3B-bf16 | 65 GB | ~30.8 tps | MoE 通用对话/推理，激活 3B |
    | gemma4:12b | 7.6 GB | ~56.1 tps | 最快响应，短消息/翻译（Ollama） |

    ### 图像/视频生成
    | 模型 | 用途 |
    |------|------|
    | SDXL Base 1.0 | 文生图，1024×1024，20 步 |
    | SANA 1.5 1.6B | 文生图，1024×1024，FP32 |
    | MiniMax H3 4-bit | 文生视频，864×480，5 秒 24fps |

    ### 语音
    | 模型 | 类型 | 用途 |
    |------|------|------|
    | Qwen3-TTS-0.6B | TTS | 9 种预置音色 + 声音克隆 |
    | SenseVoiceSmall | ASR | 极速语音识别（0.78s CPU） |
    | Seaco-Paraformer | ASR | 长音频识别 + 说话人分离 |
    | edge-tts | TTS | 云端备选 |

    ### 知识库与嵌入
    | 模型 | 用途 |
    |------|------|
    | RAGFlow v0.27.0 | 知识库问答（459 文档，bge-large-zh 向量化） |
    | bge-large-zh | 向量嵌入，1024 维，via Ollama |
    | FTS5 本地索引 | 全文搜索，454 文件，53MB SQLite |

    ---

    ## 🔀 智能路由

    统一入口 `http://localhost:8082`（常驻）/ `http://localhost:8088`（代理），OpenAI 兼容 API：

    | 规则 | 首选模型 | 降级链 |
    |------|----------|--------|
    | 图片理解 | Qwen3.8-27B | → Qwen3.6-35B |
    | 代码任务 | Qwen3.8-27B | → 远程 Qwen3.6 |
    | 推理任务 | Qwen3.6-35B-MoE | → Qwen3.8 → 远程 |
    | 短消息 (≤200 token) | gemma4:12b | → Qwen3.8 |
    | 长文本 (≥4000 token) | Qwen3.8-27B | → Qwen3.6-35B |
    | 默认兜底 | Qwen3.8-27B | → 远程 → Qwen3.6-35B |

    ---

    ## 📡 服务架构

    | 服务 | 端口 | 说明 |
    |------|------|------|
    | AI 工厂 WebUI | 8501 | 本页面，Streamlit |
    | LLM 常驻服务 | 8082 | Qwen3.8-27B-4bit（launchd 托管） |
    | OpenAI 兼容代理 | 8088 | 多模型统一入口 |
    | ComfyUI | 8188 | 图片/视频生成 |
    | RAGFlow | 9380 / 8086 | 知识库（API / 代理） |
    | Ollama | 11434 | gemma4:12b + bge-large-zh |

    ---

    ## 🔒 数据安全

    - 所有模型推理在本地完成，**数据不上传**
    - 知识库文档（RAGFlow + FTS5）本地存储，每日 9:30 自动增量同步
    - 操作日志记录在 `/tmp/ai-factory-activity.log`
    - 远程 LLM (vLLM 106.0.4.142) 仅作为降级备选

    ---

    ## 📁 项目目录

    ```
    local-ai-factory/
    ├── webui.py                  # 主程序（Streamlit WebUI）
    ├── rag_sync.py               # RAGFlow 知识库同步脚本
    ├── ragflow-docker/           # RAGFlow Docker 部署
    ├── router_config.yaml        # 智能路由配置
    ├── start_all.sh / stop_all.sh  # 服务启停脚本
    ├── output/                   # 生成内容（图片/视频/音频/报告）
    └── README.md / CHANGELOG.md  # 项目文档
    ```
    """)
    st.markdown(intro_md.replace("{APP_VERSION}", APP_VERSION))


# ==================== 底部信息 ====================
st.divider()
st.markdown(f"""
<div style='text-align:center;padding:0.5rem 0 0.8rem;'>
    <span style='font-size:0.74rem;color:#8e8ea0;font-weight:400;'>
        🏭 本地 AI 工厂 <span style='font-family:JetBrains Mono,monospace;font-size:0.72rem;'>v{APP_VERSION}</span>
        &nbsp;·&nbsp; {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        &nbsp;·&nbsp; MacBook Pro M5 Max · 137GB
        &nbsp;·&nbsp; <span style='font-weight:600;background:linear-gradient(135deg,#7c3aed,#ec4899 50%,#f59e0b);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;'>作者：李准的星小辰</span>
    </span>
</div>
""", unsafe_allow_html=True)
