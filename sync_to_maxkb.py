#!/usr/bin/env python3
"""
同步 Obsidian vault 到 MaxKB 知识库
"""
import requests
import os
import sys
import glob
import time

MAXKB_URL = "http://localhost:8085"
VAULT_PATH = os.path.expanduser("~/Library/Mobile Documents/iCloud~md~obsidian/Documents/my obsidian vault")

def get_token():
    resp = requests.post(f"{MAXKB_URL}/admin/api/user/login",
                        json={"username": "admin", "password": "admin123", "captcha": ""})
    return resp.json()["data"]["token"]

def create_kb(token, name, desc=""):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(f"{MAXKB_URL}/admin/api/workspace/default/knowledge",
                        headers=headers,
                        json={"name": name, "desc": desc, "type": 0})
    data = resp.json()
    if data.get("code") == 200:
        return data["data"]["id"]
    else:
        print(f"创建知识库失败: {data.get('message')}")
        return None

def upload_document(token, kb_id, file_path, doc_name=None):
    """上传单个 markdown 文档到知识库"""
    headers = {"Authorization": f"Bearer {token}"}

    if not doc_name:
        doc_name = os.path.basename(file_path)

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    if len(content.strip()) < 10:
        return False

    # 分割文档
    resp = requests.post(f"{MAXKB_URL}/admin/api/workspace/default/knowledge/{kb_id}/document/split",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"content": content, "name": doc_name})
    data = resp.json()
    if data.get("code") != 200:
        print(f"  分割失败: {data.get('message')}")
        return False

    # 创建文档
    resp = requests.post(f"{MAXKB_URL}/admin/api/workspace/default/knowledge/{kb_id}/document",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"name": doc_name, "content": content})
    data = resp.json()
    if data.get("code") == 200:
        return True
    else:
        print(f"  上传失败: {data.get('message')}")
        return False

def get_md_files(directory):
    """获取目录下所有 markdown 文件"""
    files = []
    for root, dirs, filenames in os.walk(directory):
        for f in filenames:
            if f.endswith(".md"):
                files.append(os.path.join(root, f))
    return files

def main():
    print("=" * 50)
    print("同步 Obsidian vault 到 MaxKB")
    print("=" * 50)

    # 登录
    print("\n1. 登录 MaxKB...")
    token = get_token()
    print("   ✅ 登录成功")

    # 创建知识库
    print("\n2. 创建知识库...")
    kb1_id = create_kb(token, "李准的笔记", "个人笔记和工作文档")
    kb2_id = create_kb(token, "claudecode转存", "Claude Code 转存资料")

    if not kb1_id or not kb2_id:
        print("   ❌ 创建知识库失败")
        return

    print(f"   ✅ 李准的笔记: {kb1_id}")
    print(f"   ✅ claudecode转存: {kb2_id}")

    # 上传根目录和工作文档
    print("\n3. 上传李准的笔记...")
    root_files = glob.glob(os.path.join(VAULT_PATH, "*.md"))
    work_files = get_md_files(os.path.join(VAULT_PATH, "工作文档"))

    all_files = root_files + work_files
    success = 0
    for i, f in enumerate(all_files):
        name = os.path.relpath(f, VAULT_PATH)
        print(f"   [{i+1}/{len(all_files)}] {name}", end="")
        if upload_document(token, kb1_id, f, name):
            print(" ✅")
            success += 1
        else:
            print(" ❌")
        time.sleep(0.1)  # 避免请求过快

    print(f"   上传完成: {success}/{len(all_files)}")

    # 上传 claudecode转存
    print("\n4. 上传 claudecode转存...")
    cc_files = get_md_files(os.path.join(VAULT_PATH, "claudecode转存"))
    success = 0
    for i, f in enumerate(cc_files):
        name = os.path.relpath(f, VAULT_PATH)
        print(f"   [{i+1}/{len(cc_files)}] {name[:60]}", end="")
        if upload_document(token, kb2_id, f, name):
            print(" ✅")
            success += 1
        else:
            print(" ❌")
        time.sleep(0.1)

    print(f"   上传完成: {success}/{len(cc_files)}")

    # 保存 ID
    with open(os.path.join(os.path.dirname(__file__), ".maxkb_ids"), "w") as f:
        f.write(f"KB1={kb1_id}\nKB2={kb2_id}\n")

    print("\n" + "=" * 50)
    print("同步完成！")
    print(f"李准的笔记: {kb1_id}")
    print(f"claudecode转存: {kb2_id}")

if __name__ == "__main__":
    main()
