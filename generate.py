#!/usr/bin/env python3
"""Flux 图片生成 CLI 工具"""
import json
import urllib.request
import urllib.parse
import time
import os
import sys

COMFYUI_URL = "http://localhost:8188"

def load_workflow(workflow_path):
    """加载工作流文件并转换为 API 格式"""
    with open(workflow_path, 'r') as f:
        data = json.load(f)

    # 转换节点列表为字典格式
    if "nodes" in data and isinstance(data["nodes"], list):
        prompt = {}
        for node in data["nodes"]:
            node_id = str(node["id"])
            prompt[node_id] = {
                "class_type": node["type"],
                "inputs": {}
            }
            if "widgets_values" in node:
                # 根据节点类型设置输入
                if node["type"] == "UNETLoader":
                    prompt[node_id]["inputs"]["unet_name"] = node["widgets_values"][0]
                    prompt[node_id]["inputs"]["weight_dtype"] = node["widgets_values"][1]
                elif node["type"] == "DualCLIPLoader":
                    prompt[node_id]["inputs"]["clip_name1"] = node["widgets_values"][0]
                    prompt[node_id]["inputs"]["clip_name2"] = node["widgets_values"][1]
                    prompt[node_id]["inputs"]["type"] = node["widgets_values"][2]
                elif node["type"] == "VAELoader":
                    prompt[node_id]["inputs"]["vae_name"] = node["widgets_values"][0]
                elif node["type"] == "CLIPTextEncode":
                    prompt[node_id]["inputs"]["text"] = node["widgets_values"][0]
                elif node["type"] == "EmptySD3LatentImage":
                    prompt[node_id]["inputs"]["width"] = node["widgets_values"][0]
                    prompt[node_id]["inputs"]["height"] = node["widgets_values"][1]
                    prompt[node_id]["inputs"]["batch_size"] = node["widgets_values"][2]
                elif node["type"] == "KSampler":
                    prompt[node_id]["inputs"]["seed"] = node["widgets_values"][0]
                    prompt[node_id]["inputs"]["steps"] = node["widgets_values"][2]
                    prompt[node_id]["inputs"]["cfg"] = node["widgets_values"][3]
                    prompt[node_id]["inputs"]["sampler_name"] = node["widgets_values"][4]
                    prompt[node_id]["inputs"]["scheduler"] = node["widgets_values"][5]
                    prompt[node_id]["inputs"]["denoise"] = node["widgets_values"][6]
                elif node["type"] == "SaveImage":
                    prompt[node_id]["inputs"]["filename_prefix"] = node["widgets_values"][0]

        # 处理链接关系
        if "links" in data:
            for link in data["links"]:
                link_id, from_node, from_slot, to_node, to_slot, link_type = link
                from_node_str = str(from_node)
                to_node_str = str(to_node)

                # 确定输入名称
                if to_node_str in prompt:
                    target_class = prompt[to_node_str]["class_type"]
                    if target_class == "KSampler":
                        if to_slot == 0:
                            input_name = "model"
                        elif to_slot == 1:
                            input_name = "positive"
                        elif to_slot == 2:
                            input_name = "negative"
                        elif to_slot == 3:
                            input_name = "latent_image"
                        else:
                            input_name = f"input_{to_slot}"
                    elif target_class == "VAEDecode":
                        if to_slot == 0:
                            input_name = "samples"
                        elif to_slot == 1:
                            input_name = "vae"
                        else:
                            input_name = f"input_{to_slot}"
                    elif target_class == "SaveImage":
                        input_name = "images"
                    elif target_class == "CLIPTextEncode":
                        input_name = "clip"
                    else:
                        input_name = f"input_{to_slot}"

                    prompt[to_node_str]["inputs"][input_name] = [from_node_str, from_slot]

        return prompt
    else:
        # 已经是 API 格式
        return data

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

def wait_for_completion(prompt_id, timeout=300):
    """等待生成完成"""
    start = time.time()
    while time.time() - start < timeout:
        history = get_history(prompt_id)
        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            if outputs:
                return outputs
        time.sleep(1)
    return None

def generate(workflow_path, prompt_text=None):
    """生成图片"""
    print(f"📂 加载工作流: {workflow_path}")
    workflow = load_workflow(workflow_path)

    # 更新提示词
    if prompt_text:
        for node_id, node in workflow.items():
            if node.get("class_type") == "CLIPTextEncode":
                if "inputs" in node and "text" in node["inputs"]:
                    if "blurry" not in node["inputs"]["text"]:
                        node["inputs"]["text"] = prompt_text
                        print(f"📝 更新提示词: {prompt_text[:50]}...")
                        break

    print("🚀 提交生成任务...")
    result = queue_prompt(workflow)
    prompt_id = result.get("prompt_id")
    print(f"📋 任务 ID: {prompt_id}")

    print("⏳ 等待生成完成...")
    outputs = wait_for_completion(prompt_id)

    if outputs:
        print("✅ 生成完成！")
        # 查找输出图片
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                for img in node_output["images"]:
                    filename = img.get("filename")
                    subfolder = img.get("subfolder", "")
                    print(f"🖼️  图片已保存: {subfolder}/{filename}")
                    # 构建查看链接
                    view_url = f"{COMFYUI_URL}/view?filename={urllib.parse.quote(filename)}"
                    if subfolder:
                        view_url += f"&subfolder={urllib.parse.quote(subfolder)}"
                    print(f"🔗 查看: {view_url}")

                    # 复制到项目目录
                    import shutil
                    comfyui_path = os.path.expanduser(f"~/ComfyUI/output/{filename}")
                    project_output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "image")
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
    if len(sys.argv) < 2:
        print("用法:")
        print(f"  {sys.argv[0]} schnell [提示词]")
        print(f"  {sys.argv[0]} dev [提示词]")
        print(f"  {sys.argv[0]} <workflow.json> [提示词]")
        sys.exit(1)

    # 确定工作流文件
    script_dir = os.path.dirname(os.path.abspath(__file__))
    arg = sys.argv[1]

    if arg == "schnell":
        workflow_path = os.path.join(script_dir, "flux_schnell_workflow.json")
    elif arg == "dev":
        workflow_path = os.path.join(script_dir, "flux_dev_workflow.json")
    elif arg.endswith(".json"):
        workflow_path = arg
    else:
        # 第一个参数当作提示词
        workflow_path = os.path.join(script_dir, "flux_schnell_workflow.json")

    # 获取提示词
    prompt_text = None
    if len(sys.argv) > 2:
        prompt_text = " ".join(sys.argv[2:])
    elif arg not in ["schnell", "dev"] and not arg.endswith(".json"):
        prompt_text = arg

    generate(workflow_path, prompt_text)
