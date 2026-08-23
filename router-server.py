#!/usr/bin/env python3
"""
本地多模型路由平台 - 统一 API 服务
====================================
端口: 8606
框架: FastAPI + uvicorn

完全兼容 OpenAI API 格式:
  POST /v1/chat/completions   → 聊天补全（支持流式/非流式）
  GET  /v1/models             → 模型列表
  GET  /v1/models/{model}     → 单模型详情
  GET  /health                → 健康检查

管理接口:
  GET  /admin/stats           → 路由统计
  GET  /admin/models          → 模型健康状态
  GET  /admin/rules           → 路由规则
  POST /admin/reload          → 热重载配置
  POST /admin/health-check    → 手动触发健康检查

启动:
  python3 router-server.py
  或
  uvicorn router-server:app --host 0.0.0.0 --port 8606
"""

import json
import time
import asyncio
from typing import Optional
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel as PydanticModel, Field

from router import RouterEngine, logger
from evaluator import EvalEngine
from smart_router import SmartRouter
from lifecycle import LifecycleManager

# ==================== 初始化 ====================
engine = RouterEngine()
eval_engine = EvalEngine()
smart_router = SmartRouter(engine)
# 注入到路由引擎，使路由决策时能查智能策略
engine.smart_router = smart_router
# 生命周期管理器：统一管理本地模型按需启动 + 空闲卸载
lifecycle_manager = LifecycleManager(engine)
engine.lifecycle_manager = lifecycle_manager

app = FastAPI(
    title="本地多模型路由+评测平台",
    description="统一 OpenAI 兼容入口 + 多模型自动评测",
    version="2.0.0",
)


# ==================== 请求/响应模型 ====================
class ChatMessage(PydanticModel):
    role: str
    content: str | list  # 支持多模态


class ChatCompletionRequest(PydanticModel):
    model: Optional[str] = None   # None = 自动路由
    messages: list[ChatMessage]
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 0.9
    stream: Optional[bool] = False
    # 透传其他参数
    extra: dict = Field(default_factory=dict, exclude=True)


# ==================== OpenAI 兼容接口 ====================
@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """OpenAI 兼容的聊天补全接口"""
    messages = [m.model_dump() for m in req.messages]

    # 流式请求特殊处理
    if req.stream:
        return await _handle_stream(req, messages)

    # 非流式：路由引擎统一处理
    result = engine.route_and_forward(
        messages=messages,
        model=req.model,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        stream=False,
    )

    if result.error:
        # 所有模型都失败
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": f"所有模型均不可用。尝试顺序: {result.tried_models}",
                    "type": "all_models_unavailable",
                    "code": "no_healthy_model",
                }
            }
        )

    # 在响应中注入路由信息
    response = result.response
    if isinstance(response, dict):
        response["_router"] = {
            "rule": result.matched_rule,
            "model": result.selected_model,
            "backend": result.selected_backend,
            "latency": round(result.latency, 3),
            "tried": result.tried_models,
        }

    return response


async def _handle_stream(req: ChatCompletionRequest, messages: list):
    """处理流式请求"""
    # Step 1: 匹配规则（不实际转发，只决定目标）
    rule_name, target_model, fallback_list = engine._match_rule(messages, req.model)
    logger.info(f"[stream] 路由决策: 规则={rule_name}, 目标={target_model}")

    try_order = [target_model] + fallback_list

    async def stream_generator():
        for model_name in try_order:
            if model_name not in engine.models:
                continue

            m = engine.models[model_name]

            # 健康检查
            if time.time() - m.last_check > 5:
                is_healthy = engine.check_model_health(model_name)
            else:
                is_healthy = m.healthy

            if not is_healthy:
                logger.warning(f"[stream] {model_name} 离线，降级")
                # 按需预热：后台异步拉起
                lifecycle_manager.ensure_started(model_name)
                continue

            # 尝试流式转发
            try:
                logger.info(f"[stream] 转发到 {model_name}")
                url = f"{m.base_url.rstrip('/')}/chat/completions"
                headers = {"Content-Type": "application/json"}
                if m.api_key and m.api_key != 'EMPTY':
                    headers['Authorization'] = f'Bearer {m.api_key}'

                payload = {
                    "model": m.model_name,
                    "messages": messages,
                    "max_tokens": min(req.max_tokens, m.max_tokens),
                    "temperature": req.temperature,
                    "top_p": req.top_p,
                    "stream": True,
                }

                start_time = time.time()
                total_tokens = 0

                async with httpx.AsyncClient(timeout=200) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if line:
                                # 透传 SSE 行
                                # 在最后一行注入路由信息
                                if line.startswith("data: [DONE]"):
                                    # 在 DONE 之前注入元数据
                                    meta_chunk = {
                                        "id": "router-meta",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": m.model_name,
                                        "choices": [],
                                        "_router": {
                                            "rule": rule_name,
                                            "model": model_name,
                                            "latency": round(time.time() - start_time, 3),
                                        }
                                    }
                                    yield f"data: {json.dumps(meta_chunk)}\n\n"
                                    yield "data: [DONE]\n\n"
                                else:
                                    yield f"{line}\n\n"

                # 记录统计
                engine._record_stats(
                    rule=rule_name, model=model_name, backend=m.model_name,
                    input_tokens=0, output_tokens=0,
                    latency=time.time() - start_time, success=True
                )
                logger.info(f"[stream] ✅ {model_name} 流式完成")
                # 生命周期：流式成功也更新活跃时间
                lifecycle_manager.on_request_success(model_name)
                return  # 成功则不再降级

            except Exception as e:
                logger.error(f"[stream] ❌ {model_name} 失败: {e}")
                engine._record_stats(
                    rule=rule_name, model=model_name, backend=m.model_name,
                    input_tokens=0, output_tokens=0, latency=0,
                    success=False, error=str(e)[:100]
                )
                continue

        # 所有模型失败
        error_chunk = {
            "error": {
                "message": "所有模型均不可用",
                "tried": try_order,
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/v1/models")
async def list_models():
    """OpenAI 兼容的模型列表"""
    return {"object": "list", "data": engine.get_model_list()}


@app.get("/v1/models/{model_id}")
async def get_model(model_id: str):
    """单个模型详情"""
    m = engine.models.get(model_id)
    if not m:
        raise HTTPException(status_code=404, detail=f"模型 '{model_id}' 不存在")

    # 实时健康检查
    healthy = engine.check_model_health(model_id)

    return {
        "id": model_id,
        "object": "model",
        "created": 1700000000,
        "owned_by": "local-router",
        "healthy": healthy,
        "config": {
            "api_type": m.api_type,
            "base_url": m.base_url,
            "backend": m.model_name,
            "priority": m.priority,
            "max_tokens": m.max_tokens,
            "context_window": m.context_window,
            "speed": m.speed,
            "quality": m.quality,
            "cost": m.cost,
            "tags": m.tags,
            "description": m.description,
            "avg_latency": round(m.avg_latency, 2),
        }
    }


# ==================== 健康检查 ====================
@app.get("/health")
async def health():
    """服务健康检查"""
    return {"status": "ok", "service": "model-router", "port": 8606}


# ==================== 管理接口 ====================
@app.get("/admin/stats")
async def get_stats():
    """路由统计"""
    summary = engine.get_stats_summary()
    # 补充最近20条明细
    recent = []
    with engine.stats_lock:
        for s in engine.stats[-20:]:
            recent.append({
                "timestamp": s.timestamp,
                "rule": s.rule,
                "model": s.model,
                "success": s.success,
                "latency": s.latency,
                "tokens": s.input_tokens + s.output_tokens,
                "error": s.error,
            })
    return {"summary": summary, "recent": recent}


@app.get("/admin/models")
async def admin_models():
    """所有模型详细信息（含健康状态、配置参数）"""
    results = {}
    for name, m in engine.models.items():
        results[name] = {
            "name": name,
            "healthy": m.healthy,
            "model_name": m.model_name,
            "api_type": m.api_type,
            "base_url": m.base_url,
            "api_key": m.api_key,
            "priority": m.priority,
            "max_tokens": m.max_tokens,
            "context_window": m.context_window,
            "tags": m.tags,
            "speed": m.speed,
            "quality": m.quality,
            "cost": m.cost,
            "enabled": m.enabled,
            "description": m.description,
            "avg_latency": round(m.avg_latency, 2),
        }
    return {"models": results}


@app.get("/admin/rules")
async def admin_rules():
    """路由规则列表"""
    return {"rules": engine.rules, "count": len(engine.rules)}


@app.post("/admin/reload")
async def reload_config():
    """热重载配置"""
    try:
        engine.reload_config()
        return {"status": "ok", "message": f"配置已重载: {len(engine.models)} 模型, {len(engine.rules)} 规则"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/health-check")
async def trigger_health_check():
    """手动触发健康检查"""
    results = engine.check_all_health()
    return {"results": results}


# ============================================================
# 模型动态管理接口（增删改，运行时生效 + 持久化）
# ============================================================
class ModelConfigRequest(PydanticModel):
    api_type: str = "openai"           # openai / ollama
    base_url: str
    model_name: str = ""
    api_key: str = ""
    priority: int = 99
    max_tokens: int = 4096
    context_window: int = 8192
    tags: list[str] = []
    speed: str = "medium"              # fast / medium / slow / fastest
    quality: str = "medium"            # high / medium / low / highest
    cost: str = "free"                 # free / local
    description: str = ""
    enabled: bool = True


@app.post("/models")
async def add_model(name: str, config: ModelConfigRequest):
    """添加新模型"""
    ok, msg = engine.add_model(name, config.model_dump())
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg, "model_count": len(engine.models)}


@app.put("/models/{name}")
async def update_model(name: str, config: ModelConfigRequest):
    """修改模型配置"""
    ok, msg = engine.update_model(name, config.model_dump())
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.delete("/models/{name}")
async def delete_model(name: str):
    """删除模型"""
    ok, msg = engine.remove_model(name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg, "model_count": len(engine.models)}


# ============================================================
# 评测接口
# ============================================================

class EvalRequest(PydanticModel):
    models: list[str]                          # 要评测的模型名
    question_ids: Optional[list[str]] = None   # 指定题目ID，None=全部
    timeout: Optional[int] = 120


@app.get("/eval/dataset")
async def eval_get_dataset():
    """获取题库概览"""
    questions = []
    for q in eval_engine.questions:
        questions.append({
            'id': q.id,
            'category': q.category,
            'difficulty': q.difficulty,
            'prompt': q.prompt[:100] + '...' if len(q.prompt) > 100 else q.prompt,
            'max_tokens': q.max_tokens,
            'judge_type': q.judge.get('type', 'keyword'),
        })
    categories = {}
    for q in questions:
        categories[q['category']] = categories.get(q['category'], 0) + 1
    return {
        "total": len(questions),
        "categories": categories,
        "questions": questions,
    }


@app.post("/eval/run")
async def eval_run(req: EvalRequest, background_tasks=None):
    """
    触发评测（同步执行，等待返回）
    注意：题目多时可能耗时较长，建议用 /eval/run-async
    """
    try:
        session = eval_engine.run_eval(
            model_names=req.models,
            question_ids=req.question_ids,
            timeout=req.timeout,
        )
        leaderboard = eval_engine.get_leaderboard(session=session)
        return {
            "session_id": session.session_id,
            "duration": round(session.duration, 1),
            "total_questions": len(req.question_ids) if req.question_ids else len(eval_engine.questions),
            "leaderboard": leaderboard,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/eval/sessions")
async def eval_sessions():
    """列出所有历史评测会话"""
    return {"sessions": eval_engine.list_sessions()}


@app.get("/eval/sessions/{session_id}")
async def eval_session_detail(session_id: str):
    """获取某个评测会话的完整结果"""
    data = eval_engine.load_session(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="评测会话不存在")
    return data


@app.get("/eval/leaderboard")
async def eval_leaderboard(session_id: Optional[str] = None):
    """获取排行榜（默认最新一次评测）"""
    board = eval_engine.get_leaderboard(session_id=session_id)
    if not board:
        return {"message": "暂无评测数据，请先 POST /eval/run"}
    return board


@app.post("/eval/reload")
async def eval_reload():
    """热重载题库"""
    try:
        eval_engine.reload_dataset()
        return {"status": "ok", "message": f"题库已重载: {len(eval_engine.questions)} 道题"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 智能路由接口
# ============================================================

@app.post("/smart/learn")
async def smart_learn():
    """触发智能路由学习"""
    report = smart_router.learn()
    return report


@app.get("/smart/status")
async def smart_status():
    """获取智能路由状态"""
    return smart_router.get_status()


@app.post("/smart/enable")
async def smart_enable():
    """启用智能路由"""
    ok, msg = smart_router.enable()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.post("/smart/disable")
async def smart_disable():
    """禁用智能路由"""
    ok, msg = smart_router.disable()
    return {"status": "ok", "message": msg}


@app.get("/smart/strategy")
async def smart_strategy():
    """查看当前优化策略"""
    return smart_router.get_status()


# ============================================================
# 模型生命周期接口（按需启动 + 空闲卸载）
# ============================================================

@app.get("/lifecycle/status")
async def lifecycle_status():
    """获取所有模型生命周期状态"""
    return lifecycle_manager.get_status()


@app.post("/lifecycle/start/{model_name}")
async def lifecycle_start(model_name: str):
    """手动拉起指定模型"""
    ok, msg = lifecycle_manager.start_model(model_name)
    if not ok and "已在线" not in msg:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok" if ok else "skip", "message": msg}


@app.post("/lifecycle/stop/{model_name}")
async def lifecycle_stop(model_name: str):
    """手动卸载指定模型释放内存"""
    ok, msg = lifecycle_manager.stop_model(model_name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "message": msg}


@app.get("/lifecycle/config")
async def lifecycle_config():
    """获取生命周期配置"""
    return {
        "lifecycle_enabled": lifecycle_manager.enabled,
        "idle_check_interval": lifecycle_manager.idle_check_interval,
        "default_idle_timeout": lifecycle_manager.default_idle_timeout,
        "startup_grace": lifecycle_manager.startup_grace,
        "startup_timeout": lifecycle_manager.startup_timeout,
        "daemon_alive": lifecycle_manager._thread is not None and lifecycle_manager._thread.is_alive(),
    }


@app.get("/")
async def root():
    """根路径 - 服务信息"""
    return {
        "service": "本地多模型路由+评测平台",
        "version": "4.0.0",
        "port": 8606,
        "smart_routing": smart_router.enabled,
        "lifecycle_enabled": lifecycle_manager.enabled,
        "endpoints": {
            # 路由
            "chat": "POST /v1/chat/completions",
            "models": "GET /v1/models",
            "health": "GET /health",
            "stats": "GET /admin/stats",
            "admin_models": "GET /admin/models",
            "rules": "GET /admin/rules",
            "reload": "POST /admin/reload",
            # 评测
            "eval_dataset": "GET /eval/dataset",
            "eval_run": "POST /eval/run",
            "eval_sessions": "GET /eval/sessions",
            "eval_leaderboard": "GET /eval/leaderboard",
            "eval_reload": "POST /eval/reload",
            # 智能路由
            "smart_learn": "POST /smart/learn",
            "smart_status": "GET /smart/status",
            "smart_enable": "POST /smart/enable",
            "smart_disable": "POST /smart/disable",
            "smart_strategy": "GET /smart/strategy",
            # 模型生命周期
            "lifecycle_status": "GET /lifecycle/status",
            "lifecycle_start": "POST /lifecycle/start/{model}",
            "lifecycle_stop": "POST /lifecycle/stop/{model}",
            "lifecycle_config": "GET /lifecycle/config",
        },
        "models_count": len(engine.models),
        "rules_count": len(engine.rules),
        "eval_questions": len(eval_engine.questions),
        "smart_learn_count": smart_router.learn_count,
    }


# ==================== 启动 ====================
@app.on_event("startup")
async def startup_event():
    logger.info("=" * 60)
    logger.info("本地多模型路由+评测+智能路由+生命周期平台 启动中...")
    logger.info(f"端口: {engine.server_config.get('port', 8606)}")
    logger.info(f"模型: {len(engine.models)} 个")
    logger.info(f"路由规则: {len(engine.rules)} 条")
    logger.info(f"评测题库: {len(eval_engine.questions)} 道题")
    logger.info(f"智能路由: {'启用' if smart_router.enabled else '禁用'}, "
                f"策略 {len(smart_router.strategies)} 条, 学习 {smart_router.learn_count} 次")
    logger.info("启动健康检查...")
    engine.check_all_health()
    # 启动生命周期守护线程（空闲扫描卸载）
    lifecycle_manager.start_daemon()
    lc = engine.lifecycle_config
    logger.info(f"生命周期: {'启用' if lifecycle_manager.enabled else '禁用'}, "
                f"扫描间隔={lifecycle_manager.idle_check_interval}s, "
                f"默认空闲超时={lifecycle_manager.default_idle_timeout}s, "
                f"宽限期={lifecycle_manager.startup_grace}s")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    lifecycle_manager.stop_daemon()
    engine._save_stats()
    logger.info("生命周期守护已停止，路由统计已保存，服务关闭")


if __name__ == "__main__":
    uvicorn.run(
        "router-server:app",
        host="0.0.0.0",
        port=8606,
        reload=False,
        log_level="info",
    )
