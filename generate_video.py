#!/usr/bin/env python3
"""CogVideoX 视频生成 CLI 工具"""
import json
import urllib.request
import urllib.parse
import time
import os
import sys
import shutil

COMFYUI_URL = "http://localhost:8188"

# CogVideoX 工作流
WORKFLOW = {
    "1": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "FLUX.1-schnell/model.safetensors",
            "type": "sd3"
        }
    },
    "2": {
        "class_type": "CogVideoTextEncode",
        "inputs": {
            "prompt": "A cat walking on the moon, cinematic, 4k",
            "clip": ["1", 0]
        }
    },
    "3": {
        "class_type": "CogVideoTextEncode",
        "inputs": {
            "prompt": "blurry, low quality, distorted",
            "clip": ["1", 0]
        }
    },
    "4": {
        "class_type": "CogVideoXModelLoader",
        "inputs": {
            "model": "CogVideoX-5b/diffusion_pytorch_model-00001-of-00002.safetensors",
            "base_precision": "bf16",
            "quantization": "disabled",
            "load_device": "main_device",
            "enable_sequential_cpu_offload": False
        }
    },
    "5": {
        "class_type": "CogVideoXVAELoader",
        "inputs": {
            "model_name": "CogVideoX-5b/diffusion_pytorch_model.safetensors",
            "precision": "bf16"
        }
    },
    "6": {
        "class_type": "CogVideoSampler",
        "inputs": {
            "model": ["4", 0],
            "positive": ["2", 0],
            "negative": ["3", 0],
            "num_frames": 49,
            "steps": 30,
            "cfg": 6.0,
            "seed": 42,
            "scheduler": "CogVideoXDDIM"
        }
    },
    "7": {
        "class_type": "CogVideoDecode",
        "inputs": {
            "samples": ["6", 0],
            "vae": ["5", 0],
            "enable_vae_tiling": True,
            "tile_sample_min_height": 240,
            "tile_sample_min_width": 360,
            "tile_overlap_factor_height": 0.2,
            "tile_overlap_factor_width": 0.2,
            "auto_tile_size": True
        }
    },
    "8": {
        "class_type": "SaveAnimatedWEBP",
        "inputs": {
            "images": ["7", 0],
            "filename_prefix": "cogvideo_output",
            "fps": 8,
            "lossless": False,
            "quality": 80,
            "method": "default"
        }
    }
}

def queue_prompt(workflow):
    """提交工作流到 ComfyUI"""
    data = json.dumps({"prompt": workflow}).encode('utf-8')
    req = urllib.request.Request(
        f"{COMFYUI_URL}/prompt",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def get_history(prompt_id):
    """获取生成历史"""
    with urllib.request.urlopen(f"{COMFYUI_URL}/history/{prompt_id}") as resp:
        return json.loads(resp.read())

def wait_for_completion(prompt_id, timeout=600):
    """等待生成完成"""
    start = time.time()
    while time.time() - start < timeout:
        history = get_history(prompt_id)
        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            if outputs:
                return outputs
        time.sleep(2)
    return None

def generate_video(prompt_text=None):
    """生成视频"""
    workflow = WORKFLOW.copy()

    # 更新提示词
    if prompt_text:
        workflow["1"]["inputs"]["prompt"] = prompt_text
        print(f"📝 提示词: {prompt_text[:50]}...")

    print("🚀 提交视频生成任务...")
    result = queue_prompt(workflow)
    prompt_id = result.get("prompt_id")
    print(f"📋 任务 ID: {prompt_id}")

    print("⏳ 等待生成完成（可能需要几分钟）...")
    outputs = wait_for_completion(prompt_id)

    if outputs:
        print("✅ 视频生成完成！")
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                for img in node_output["images"]:
                    filename = img.get("filename")
                    subfolder = img.get("subfolder", "")
                    print(f"🎬 视频已保存: {subfolder}/{filename}")

                    # 复制到项目目录
                    comfyui_path = os.path.expanduser(f"~/ComfyUI/output/{filename}")
                    project_output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "video")
                    os.makedirs(project_output, exist_ok=True)
                    project_path = os.path.join(project_output, filename)
                    if os.path.exists(comfyui_path):
                        shutil.copy2(comfyui_path, project_path)
                        print(f"📁 已复制到: {project_path}")
        return True
    else:
        print("❌ 生成超时")
        return False

if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    generate_video(prompt)
