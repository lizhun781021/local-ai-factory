#!/usr/bin/env python3
"""
RAGFlow 知识库与本地工作目录增量同步脚本
==========================================
功能：
- 扫描 ~/Desktop/工作/ 目录下的 xlsx/pdf/pptx/docx/txt/md 文件
- 与 RAGFlow 知识库（河南电信工作知识库）比对
- 新增：本地有、知识库没有 → 上传+解析
- 修改：本地 mtime/hash 变化 → 删除旧记录+重新上传解析
- 删除：本地没了、知识库有 → 删除记录
- 跳过 .~ 开头的 Office 临时文件和 0 字节空文件

用法：
    python3 ragflow_sync.py              # 执行同步
    python3 ragflow_sync.py --dry-run    # 只比对不执行，预览变更
    python3 ragflow_sync.py --verbose    # 详细日志
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

# ==================== 配置 ====================
RAGFLOW_API = "http://localhost:9380/api/v1"
API_TOKEN = "ragflow-sFxr5UX1yM7ABLgOCjJOH11LOtJWC54DziBMVxavMh0"
DATASET_ID = "87f65a229ebe11f192791d16a28c2dcb"  # 河南电信工作知识库
LOCAL_DIR = os.path.expanduser("~/Desktop/工作")
SUPPORTED_EXTS = {".xlsx", ".xlsm", ".xls", ".csv", ".pdf", ".pptx", ".ppt", ".docx", ".doc", ".txt", ".md"}
# 跳过的目录
SKIP_DIRS = {".temp", ".Trash", "node_modules", "__pycache__", ".git", "fts_index"}
# 跳过的文件名模式（Office/WPS 临时文件）
SKIP_PREFIXES = (".~", "~$", ".")

LOG_FILE = os.path.expanduser("~/Desktop/星小辰工作空间/local-ai-factory/output/logs/rag_sync.log")


def log(msg, verbose=False):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ==================== API 封装 ====================
def api_request(method, path, data=None, files=None):
    """RAGFlow REST API 请求"""
    url = f"{RAGFLOW_API}{path}"
    headers = {"Authorization": f"Bearer {API_TOKEN}"}

    if files:
        # multipart 上传
        import io
        import uuid
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        for k, (fname, fpath) in files.items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'.encode())
            body.write(b"Content-Type: application/octet-stream\r\n\r\n")
            with open(fpath, "rb") as f:
                body.write(f.read())
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(url, data=body.getvalue(), headers=headers, method=method)
    elif data is not None:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers=headers, method=method)
    else:
        req = urllib.request.Request(url, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"code": e.code, "error": e.read().decode()[:200]}
    except Exception as e:
        return {"code": -1, "error": str(e)}


def get_dataset_docs():
    """获取知识库所有文档"""
    all_docs = []
    page = 1
    while True:
        r = api_request("GET", f"/datasets/{DATASET_ID}/documents?page={page}&page_size=100")
        if r.get("code") != 0:
            log(f"  [错误] 获取文档列表失败: {r}")
            break
        docs = r.get("data", {}).get("docs", [])
        if not docs:
            break
        all_docs.extend(docs)
        if len(docs) < 100:
            break
        page += 1
    return all_docs


def upload_doc(filepath, display_name=None):
    """上传单个文件，display_name 为 RAGFlow 中的文档名称"""
    fname = display_name or os.path.basename(filepath)
    r = api_request("POST", f"/datasets/{DATASET_ID}/documents", files={"file": (fname, filepath)})
    if r.get("code") != 0:
        return None, f"上传失败: {r.get('message', r.get('error', ''))}"
    doc = r["data"][0]
    return doc["id"], None


def parse_doc(doc_id):
    """触发解析"""
    r = api_request("POST", f"/datasets/{DATASET_ID}/documents/parse", data={"document_ids": [doc_id]})
    if r.get("code") != 0:
        return False, f"解析失败: {r.get('message', r.get('error', ''))}"
    return True, None


def delete_docs(doc_ids):
    """删除文档"""
    if not doc_ids:
        return True, None
    r = api_request("DELETE", f"/datasets/{DATASET_ID}/documents", data={"ids": doc_ids})
    if r.get("code") != 0:
        return False, f"删除失败: {r.get('message', r.get('error', ''))}"
    return True, None


def get_doc_info(doc_id):
    """获取单个文档信息"""
    r = api_request("GET", f"/datasets/{DATASET_ID}/documents/{doc_id}")
    if r.get("code") != 0:
        return None
    return r.get("data", {})


# ==================== 本地扫描 ====================
def scan_local_files():
    """扫描本地目录所有支持的文件"""
    files = {}
    for root, dirs, fnames in os.walk(LOCAL_DIR):
        # 跳过隐藏目录和临时目录
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in fnames:
            # 跳过临时/隐藏文件
            if fname.startswith(tuple(SKIP_PREFIXES)) or fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTS:
                continue
            fpath = os.path.join(root, fname)
            try:
                size = os.path.getsize(fpath)
                if size == 0:  # 跳过空文件
                    continue
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                rel = os.path.relpath(fpath, LOCAL_DIR)
                files[rel] = {"path": fpath, "size": size, "mtime": mtime, "ext": ext}
            except OSError:
                continue
    return files


def file_md5(filepath, chunk=8192):
    """计算文件 MD5"""
    h = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                h.update(data)
        return h.hexdigest()
    except Exception:
        return None


# ==================== 同步逻辑 ====================
def sync(dry_run=False, verbose=False):
    log("=" * 60)
    log(f"RAGFlow 知识库同步开始（{'DRY-RUN 预览' if dry_run else '实际执行'}）")
    log(f"本地目录: {LOCAL_DIR}")

    # 1. 扫描本地文件
    local_files = scan_local_files()
    log(f"本地扫描到 {len(local_files)} 个有效文件")
    if verbose:
        for rel in sorted(local_files):
            log(f"  [本地] {rel}")

    # 2. 获取知识库文档
    remote_docs = get_dataset_docs()
    log(f"知识库现有 {len(remote_docs)} 个文档")

    # 建立远程文档映射（按名称）
    remote_by_name = {}
    for doc in remote_docs:
        remote_by_name[doc.get("name", "")] = doc

    to_upload = []      # 新增
    to_update = []      # 修改（删旧传新）
    to_delete = []      # 删除

    # 归一化函数：移除 RAGFlow 上传重名时自动加的 (1)/(2) 后缀
    def norm_name(fname):
        import re
        return re.sub(r"\(\d+\)(?=\.)", "", fname)

    # 3. 本地文件去重与命名
    #    规则：
    #    - 同名同内容（MD5一致）→ 视为真正的副本，只保留 mtime 最新的一份
    #    - 同名不同内容（MD5不同）→ 都保留，非主版本用「【父目录】文件名」命名
    #    这样知识库能覆盖所有不同内容的版本，不会因为同名而丢内容
    from collections import defaultdict
    by_fname = defaultdict(list)  # filename -> [(rel, info)]
    for rel, info in local_files.items():
        fname = os.path.basename(rel)
        by_fname[fname].append((rel, info))

    local_entries = {}  # display_name -> {path, size, mtime, rel}
    for fname, entries in by_fname.items():
        if len(entries) == 1:
            rel, info = entries[0]
            local_entries[fname] = {**info, "rel": rel}
        else:
            # 同名多文件：按 MD5 分组
            md5_groups = defaultdict(list)
            for rel, info in entries:
                h = file_md5(info["path"])
                md5_groups[h or str(id(info))].append((rel, info))

            if len(md5_groups) == 1:
                # 内容完全相同 → 只保留最新版
                group = list(md5_groups.values())[0]
                best = max(group, key=lambda x: x[1]["mtime"])
                local_entries[fname] = {**best[1], "rel": best[0]}
            else:
                # 内容不同 → 各保留最新版，用【父目录】前缀区分
                all_variants = []
                for h, group in md5_groups.items():
                    best = max(group, key=lambda x: x[1]["mtime"])
                    all_variants.append((best[0], best[1]))
                # 按 mtime 降序：最新版用原文件名，其余加前缀
                all_variants.sort(key=lambda x: x[1]["mtime"], reverse=True)

                used_names = set()
                for i, (rel, info) in enumerate(all_variants):
                    if i == 0:
                        display_name = fname
                    else:
                        parent = os.path.basename(os.path.dirname(rel)) or "root"
                        display_name = f"【{parent}】{fname}"
                        counter = 2
                        while display_name in used_names:
                            display_name = f"【{parent}{counter}】{fname}"
                            counter += 1
                    used_names.add(display_name)
                    local_entries[display_name] = {**info, "rel": rel}

    log(f"去重后本地条目数: {len(local_entries)}（同名去重 {len(local_files) - len(local_entries)} 个）")

    # 4. 比对：遍历本地条目（以 display_name 为准）
    for dname, info in sorted(local_entries.items()):
        exact_doc = remote_by_name.get(dname)
        if exact_doc is not None:
            # 远程存在同名文档 → 比较 size
            remote_size = exact_doc.get("size", 0)
            if remote_size != info["size"] and remote_size > 0:
                to_update.append((dname, info))  # 内容确实变了 → 删旧传新
            # size 相同视为未变化，跳过
        else:
            to_upload.append((dname, info))  # 远程无同名 → 新增

# 5. 遍历远程文档，找出需要删除的：
    #    - 本地已不存在的文件（用户删除）
    #    - 远程 (N) 副本：仅当本地存在对应的**真实主文件**时才删除冗余副本。
    #      若用户本地文件名本身就带 (N)（如 xxx(1).xlsx），则该文件已被第 4 步按
    #      真实文件名处理（视为独立文件），绝不会作为"副本"删除。
    #      同时用 seen 集合防止同一副本被重复加入删除列表（同 ID 二次删除会报 402）。
    import re
    seen_ids = set()
    for name, doc in remote_by_name.items():
        if name.startswith(tuple(SKIP_PREFIXES)):
            continue
        if name in local_entries:
            continue  # 已在第 4 步处理
        if doc["id"] in seen_ids:
            continue  # 去重：避免同一 ID 重复删除
        base = norm_name(name)
        if base in local_entries and name != base:
            # 远程是 (N) 副本，且本地存在对应主文件 → 删除冗余副本
            to_delete.append((name, doc["id"]))
            seen_ids.add(doc["id"])
        elif re.search(r"\(\d+\)\.", name) and base not in local_entries:
            # 远程 (N) 版本，本地也没有对应主文件 → 保守保留，仅提示
            log(f"  ℹ️ 远程 (N) 版本 {name} 在本地无对应源文件，保留不处理")
        elif name not in local_entries:
            # 本地没有对应文件 → 本地已删除，同步删除
            to_delete.append((name, doc["id"]))
            seen_ids.add(doc["id"])
        elif re.search(r"\(\d+\)\.", name):
            # 远程 (N) 副本，本地连主文件也没有 → 保守保留，仅提示
            log(f"  ℹ️ 远程 (N) 版本 {name} 在本地无对应源文件，保留不处理")
        else:
            # 本地没有对应文件 → 本地已删除，同步删除
            to_delete.append((name, doc["id"]))

    # ==================== 输出计划 ====================
    log(f"\n📋 同步计划：新增 {len(to_upload)} 个，修改 {len(to_update)} 个，删除 {len(to_delete)} 个")

    if to_upload:
        log("  ➕ 新增：")
        for name, _ in to_upload:
            log(f"    - {name}")

    if to_update:
        log("  ✏️ 修改：")
        for name, _ in to_update:
            log(f"    - {name}")

    if to_delete:
        log("  🗑️ 删除：")
        for name, _ in to_delete:
            log(f"    - {name}")

    if dry_run:
        log("DRY-RUN 模式，未执行任何变更")
        return {"upload": len(to_upload), "update": len(to_update), "delete": len(to_delete)}

    # ==================== 执行变更 ====================
    results = {"upload": 0, "update": 0, "delete": 0, "failed": []}

    # 删除
    if to_delete:
        ids = [d[1] for d in to_delete]  # to_delete 元素为 (name, doc_id)
        ok, err = delete_docs(ids)
        if ok:
            results["delete"] = len(to_delete)
            log(f"  ✅ 已删除 {len(to_delete)} 个文档")
        else:
            log(f"  ❌ {err}")

    # 上传新增
    for name, info in to_upload:
        doc_id, err = upload_doc(info["path"], display_name=name)
        if not doc_id:
            results["failed"].append((name, err))
            log(f"  ❌ 上传失败 {name}: {err}")
            continue
        ok, perr = parse_doc(doc_id)
        if ok:
            results["upload"] += 1
            log(f"  ✅ 新增 {name}（解析已触发）")
        else:
            results["failed"].append((name, perr))
            log(f"  ⚠️ {name} 已上传但解析失败: {perr}")

    # 修改（删除旧+上传新）
    for name, info in to_update:
        # 删除旧的
        old_doc = remote_by_name.get(name)
        if old_doc:
            ok, err = delete_docs([old_doc["id"]])
            if not ok:
                results["failed"].append((name, f"删除旧版本失败: {err}"))
                log(f"  ❌ 修改失败 {name}: {err}")
                continue
        doc_id, err = upload_doc(info["path"], display_name=name)
        if not doc_id:
            results["failed"].append((name, err))
            log(f"  ❌ 上传失败 {name}: {err}")
            continue
        ok, perr = parse_doc(doc_id)
        if ok:
            results["update"] += 1
            log(f"  ✅ 更新 {name}（旧版删除，新版解析已触发）")
        else:
            results["failed"].append((name, perr))
            log(f"  ⚠️ {name} 已更新但解析失败: {perr}")

    log(f"\n✅ 同步完成：新增 {results['upload']}，更新 {results['update']}，删除 {results['delete']}")
    if results["failed"]:
        log(f"⚠️ {len(results['failed'])} 个失败:")
        for name, err in results["failed"]:
            log(f"    - {name}: {err}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAGFlow 知识库增量同步")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    parser.add_argument("--verbose", action="store_true", help="显示详细日志")
    args = parser.parse_args()

    sync(args.dry_run, args.verbose)