#!/usr/bin/env python3
"""
本地 AI 工厂 - 模型 API 服务封装层
包含: LLM 对话、ComfyUI 图片/视频生成、本地图生视频、语音识别/合成
作者：李准的星小辰
"""
import os, json, time, random, threading, subprocess
import requests

# ---- 本地服务调用禁用系统代理（macOS 系统代理 127.0.0.1:7892 会劫持 localhost 请求）----
os.environ["NO_PROXY"] = "localhost,127.0.0.1,127.*,10.*,192.168.*,*.local"
os.environ["no_proxy"] = "localhost,127.0.0.1,127.*,10.*,192.168.*,*.local"

# ---- 常量（与 webui.py 保持一致）----
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
LLM_API_URL = "http://localhost:8082"
COMFYUI_URL = "http://localhost:8188"
VISION_API_URL = "http://localhost:8081"
OUTPUT_IMAGE_GEN = os.path.join(OUTPUT_DIR, "图像生成")
OUTPUT_VIDEO_GEN = os.path.join(OUTPUT_DIR, "视频生成")
OUTPUT_AUDIO_TTS = os.path.join(OUTPUT_DIR, "语音合成")
OUTPUT_REPORTS = os.path.join(OUTPUT_DIR, "测试报告")
OUTPUT_IMAGE_RECOG = os.path.join(OUTPUT_DIR, "图像识别")
OUTPUT_VIDEO_RECOG = os.path.join(OUTPUT_DIR, "视频识别")
OUTPUT_SPEECH_RECOG = os.path.join(OUTPUT_DIR, "语音识别")


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
    elif model_path == "xing4.0":
        # 本地 Xing4.0（8089，transformers + MPS，OpenAI 兼容）
        try:
            effective_max_tokens = max(max_tokens, 1024)
            payload = {
                "messages": messages,
                "max_tokens": effective_max_tokens,
                "model": "xing4.0",
            }
            resp = requests.post(
                "http://localhost:8089/v1/chat/completions",
                json=payload,
                timeout=1800  # Xing4.0 生成慢（约1.2 tok/s），1024 tokens 约需15分钟
            )
            if resp.status_code == 200:
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content") or msg.get("reasoning") or "(模型未返回内容)"
                usage = data.get("usage", {})
                return content, usage
            else:
                return f"API 错误: {resp.status_code} - {resp.text[:200]}", {}
        except Exception as e:
            return f"API 连接失败（Xing4.0 生成较慢，请耐心等待或调低 Max Token）: {str(e)}", {}
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


