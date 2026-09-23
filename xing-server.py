#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xing4.0 本地推理服务（transformers + PyTorch MPS）
OpenAI 兼容 API：/v1/models, /v1/chat/completions
作者：李准的星小辰
"""
import os, time, threading, json, argparse
os.environ.setdefault('HF_HUB_OFFLINE', '1')

import torch
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn
from pydantic import BaseModel, Field
from typing import List, Optional

MODEL_DIR = os.environ.get("XING_MODEL_DIR", "/Users/lizhun/Downloads/xing40")
PORT = int(os.environ.get("XING_PORT", "8089"))

app = FastAPI(title="Xing4.0 Local Server")

_model = None
_tokenizer = None
_load_time = None
_model_meta = {}

def load_model():
    global _model, _tok, _load_time
    from transformers import AutoModelForCausalLM, AutoTokenizer
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] 加载 tokenizer...", flush=True)
    _tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    print(f"[{time.strftime('%H:%M:%S')}] 加载模型 (bf16 → CPU)...", flush=True)
    _model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR,
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    print(f"[{time.strftime('%H:%M:%S')}] CPU 加载完成，转 MPS...", flush=True)
    _model = _model.to("mps")
    _model.eval()
    _load_time = time.time() - t0
    mem = torch.mps.current_allocated_memory() / 1024**3
    print(f"[{time.strftime('%H:%M:%S')}] 模型加载完成，耗时 {_load_time:.0f}s，MPS 占用 {mem:.1f} GB", flush=True)

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    max_tokens: int = 1024
    temperature: float = 0.7
    top_p: float = 0.95
    stream: bool = False
    repetition_penalty: float = 1.05

@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [{"id": "xing4.0", "object": "model", "owned_by": "local-mps"}],
    }

@app.get("/health")
def health():
    return {"status": "ok", "model": "xing4.0", "loaded": _model is not None,
            "load_time_s": round(_load_time, 1) if _model is not None else None}

@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    if _model is None:
        return JSONResponse({"error": {"message": "模型尚未加载完成，请稍后重试", "type": "not_loaded"}}, status_code=503)
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    text = _tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = _tok(text, return_tensors="pt").to("mps")
    prompt_tokens = inputs["input_ids"].shape[1]

    t0 = time.time()
    with torch.no_grad():
        out = _model.generate(
            **inputs,
            max_new_tokens=req.max_tokens,
            do_sample=True,
            temperature=req.temperature,
            top_p=req.top_p,
            repetition_penalty=req.repetition_penalty,
            pad_token_id=_tok.pad_token_id if _tok.pad_token_id is not None else _tok.eos_token_id,
        )
    gen_tokens = out[0][inputs["input_ids"].shape[1]:]
    resp_text = _tok.decode(gen_tokens, skip_special_tokens=True)
    elapsed = time.time() - t0
    print(f"[{time.strftime('%H:%M:%S')}] 生成 {len(gen_tokens)} tokens，耗时 {elapsed:.1f}s，{len(gen_tokens)/max(elapsed,0.001):.1f} tok/s", flush=True)

    return {
        "id": f"chatcmpl-xing-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "xing4.0",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": resp_text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": len(gen_tokens),
                  "total_tokens": prompt_tokens + len(gen_tokens)},
    }

def JSON(obj):
    import json as _j
    from fastapi.responses import JSONResponse
    return JSONResponse(obj)

if __name__ == "__main__":
    load_model()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")