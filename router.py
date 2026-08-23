#!/usr/bin/env python3
"""
本地多模型路由平台 - 路由引擎核心
====================================
职责：
  1. 加载并管理模型配置
  2. 意图识别 + 规则匹配 → 选择最优模型
  3. 健康检查 + 降级策略
  4. 请求转发（OpenAI 兼容 / Ollama 适配）
  5. 路由统计

使用方式：
  from router import RouterEngine
  engine = RouterEngine()
  result = engine.route_and_forward(messages, model=None, stream=False)
"""

import json
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
import yaml
import httpx

# ==================== 日志配置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("router")


# ==================== 数据结构 ====================
@dataclass
class ModelConfig:
    """单个模型的配置"""
    name: str
    api_type: str          # openai / ollama
    base_url: str
    model_name: str
    api_key: str
    priority: int
    max_tokens: int
    context_window: int
    tags: list
    speed: str
    quality: str
    cost: str
    description: str
    startup_cmd: str = ""
    auto_start: bool = False
    # 生命周期管理字段
    enabled: bool = True             # 是否参与路由候选
    idle_timeout: int = 0            # 空闲秒数后卸载（0=常驻不卸载）
    shutdown_cmd: str = ""           # 卸载命令
    # 运行时状态
    healthy: bool = True
    last_check: float = 0
    avg_latency: float = 0


@dataclass
class RouteResult:
    """路由决策结果"""
    selected_model: str           # 最终选中的模型名
    selected_backend: str         # 后端 model_name
    matched_rule: str             # 命中的规则名
    tried_models: list = field(default_factory=list)  # 尝试过的模型
    response: dict = None         # 最终响应
    error: str = ""
    latency: float = 0


@dataclass
class RequestStats:
    """单次请求统计"""
    timestamp: str
    rule: str
    model: str
    backend: str
    input_tokens: int
    output_tokens: int
    latency: float
    success: bool
    error: str = ""


# ==================== 路由引擎 ====================
class RouterEngine:
    """路由引擎核心"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = Path(__file__).parent / "router_config.yaml"
        self.config_path = Path(config_path)
        self.models: dict[str, ModelConfig] = {}
        self.rules: list[dict] = []
        self.server_config: dict = {}
        self.lifecycle_config: dict = {}
        self.stats: list[RequestStats] = []
        self.stats_lock = threading.Lock()
        self._load_config()
        self._load_stats()
        # 智能路由引擎（延迟导入避免循环依赖）
        self.smart_router = None  # 由 router-server 初始化后注入
        # 生命周期管理器（由 router-server 初始化后注入）
        self.lifecycle_manager = None

    # -------------------- 配置管理 --------------------
    def _load_config(self):
        """加载 YAML 配置"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        self.server_config = cfg.get('server', {})
        self.lifecycle_config = cfg.get('lifecycle', {})

        # 解析模型
        for name, m in cfg.get('models', {}).items():
            self.models[name] = ModelConfig(
                name=name,
                api_type=m['api_type'],
                base_url=m['base_url'],
                model_name=m['model_name'],
                api_key=m.get('api_key', ''),
                priority=m.get('priority', 99),
                max_tokens=m.get('max_tokens', 4096),
                context_window=m.get('context_window', 8192),
                tags=m.get('tags', []),
                speed=m.get('speed', 'medium'),
                quality=m.get('quality', 'medium'),
                cost=m.get('cost', 'local'),
                description=m.get('description', ''),
                startup_cmd=m.get('startup_cmd', ''),
                auto_start=m.get('auto_start', False),
                enabled=m.get('enabled', True),
                idle_timeout=m.get('idle_timeout', 0),
                shutdown_cmd=m.get('shutdown_cmd', ''),
            )

        # 解析规则
        self.rules = cfg.get('routing_rules', [])
        logger.info(f"配置加载完成: {len(self.models)} 个模型, {len(self.rules)} 条路由规则")

    def _load_stats(self):
        """从文件加载历史统计"""
        stats_file = Path(__file__).parent / self.server_config.get('stats_file', 'router_stats.json')
        if stats_file.exists():
            try:
                with open(stats_file, 'r') as f:
                    data = json.load(f)
                    self.stats = [RequestStats(**s) for s in data[-500:]]  # 保留最近500条
                    logger.info(f"加载 {len(self.stats)} 条历史统计")
            except Exception:
                self.stats = []

    def _save_stats(self):
        """持久化统计到文件"""
        stats_file = Path(__file__).parent / self.server_config.get('stats_file', 'router_stats.json')
        try:
            data = []
            with self.stats_lock:
                for s in self.stats[-500:]:
                    data.append({
                        'timestamp': s.timestamp,
                        'rule': s.rule,
                        'model': s.model,
                        'backend': s.backend,
                        'input_tokens': s.input_tokens,
                        'output_tokens': s.output_tokens,
                        'latency': s.latency,
                        'success': s.success,
                        'error': s.error,
                    })
            with open(stats_file, 'w') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"统计保存失败: {e}")

    def reload_config(self):
        """热重载配置（不中断服务）"""
        self.models.clear()
        self.rules.clear()
        self._load_config()
        logger.info("配置已热重载")

    # -------------------- 动态模型管理 --------------------
    def add_model(self, name: str, config: dict) -> tuple[bool, str]:
        """动态添加模型（运行时生效 + 持久化到yaml）"""
        if name in self.models:
            return False, f"模型 '{name}' 已存在"
        try:
            self.models[name] = ModelConfig(
                name=name,
                api_type=config.get('api_type', 'openai'),
                base_url=config['base_url'],
                model_name=config.get('model_name', config.get('base_url', '').split('//')[-1].split('/')[0]),
                api_key=config.get('api_key', ''),
                priority=config.get('priority', 99),
                max_tokens=config.get('max_tokens', 4096),
                context_window=config.get('context_window', 8192),
                tags=config.get('tags', []),
                speed=config.get('speed', 'medium'),
                quality=config.get('quality', 'medium'),
                cost=config.get('cost', 'free'),
                description=config.get('description', ''),
                startup_cmd=config.get('startup_cmd', ''),
                auto_start=config.get('auto_start', False),
                enabled=config.get('enabled', True),
                idle_timeout=config.get('idle_timeout', 0),
                shutdown_cmd=config.get('shutdown_cmd', ''),
            )
            self._save_model_to_yaml(name, config)
            logger.info(f"✅ 模型已添加: {name}")
            return True, f"模型 '{name}' 添加成功"
        except Exception as e:
            return False, f"添加失败: {e}"

    def update_model(self, name: str, config: dict) -> tuple[bool, str]:
        """动态修改模型配置"""
        if name not in self.models:
            return False, f"模型 '{name}' 不存在"
        m = self.models[name]
        for k, v in config.items():
            if hasattr(m, k):
                setattr(m, k, v)
        self._save_model_to_yaml(name, config, remove_first=True)
        logger.info(f"✏️ 模型已修改: {name}")
        return True, f"模型 '{name}' 修改成功"

    def remove_model(self, name: str) -> tuple[bool, str]:
        """动态删除模型"""
        if name not in self.models:
            return False, f"模型 '{name}' 不存在"
        del self.models[name]
        self._remove_model_from_yaml(name)
        logger.info(f"🗑️ 模型已删除: {name}")
        return True, f"模型 '{name}' 已删除"

    def _save_model_to_yaml(self, name: str, config: dict, remove_first: bool = False):
        """将模型配置持久化到 router_config.yaml"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        if 'models' not in cfg:
            cfg['models'] = {}
        if remove_first and name in cfg['models']:
            pass  # 覆盖即可
        # 构建 yaml 友好的配置（只保存有意义的字段）
        yaml_cfg = {
            'api_type': config.get('api_type', 'openai'),
            'base_url': config['base_url'],
            'model_name': config.get('model_name', ''),
            'api_key': config.get('api_key', ''),
            'priority': config.get('priority', 99),
            'max_tokens': config.get('max_tokens', 4096),
            'context_window': config.get('context_window', 8192),
            'tags': config.get('tags', []),
            'speed': config.get('speed', 'medium'),
            'quality': config.get('quality', 'medium'),
            'cost': config.get('cost', 'free'),
            'description': config.get('description', ''),
            'enabled': config.get('enabled', True),
        }
        # 本地模型才保存生命周期字段
        if config.get('cost') == 'local':
            yaml_cfg['auto_start'] = config.get('auto_start', False)
            yaml_cfg['idle_timeout'] = config.get('idle_timeout', 0)
            yaml_cfg['startup_cmd'] = config.get('startup_cmd', '')
            yaml_cfg['shutdown_cmd'] = config.get('shutdown_cmd', '')
        cfg['models'][name] = yaml_cfg
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def _remove_model_from_yaml(self, name: str):
        """从 yaml 配置中删除模型"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        if 'models' in cfg and name in cfg['models']:
            del cfg['models'][name]
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            logger.info(f"模型 {name} 已从配置文件删除")

    # -------------------- 意图识别 --------------------
    @staticmethod
    def _extract_text(messages: list) -> str:
        """从消息列表中提取纯文本（用于关键词匹配）"""
        texts = []
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                # OpenAI 多模态格式: [{"type":"text","text":"..."}, {"type":"image_url",...}]
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'text':
                        texts.append(part.get('text', ''))
        return ' '.join(texts)

    @staticmethod
    def _has_image(messages: list) -> bool:
        """检测消息中是否包含图片"""
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'image_url':
                        return True
        return False

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗略估算 token 数（中文≈1.5字/token, 英文≈1.3词/token）"""
        chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other = len(text) - chinese
        return int(chinese * 1.5 + other / 1.3)

    def _match_rule(self, messages: list, model: str = None) -> tuple[str, str, list]:
        """
        匹配路由规则
        返回: (规则名, 目标模型名, 降级列表)
        集成智能路由：如果 smart_router 启用且有优化策略，用优化后的 target/fallback
        """
        text = self._extract_text(messages)
        has_image = self._has_image(messages)
        token_count = self._estimate_tokens(text)

        logger.info(f"意图分析: 图片={'是' if has_image else '否'}, "
                     f"tokens≈{token_count}, 文本前80字='{text[:80]}'")

        for rule in self.rules:
            match_type = rule['match_type']

            # 规则1: 显式指定模型
            if match_type == 'explicit' and model:
                # 尝试精确匹配模型名
                if model in self.models:
                    return rule['name'], model, []
                # 模糊匹配: model 参数包含某个注册模型名
                for mname in self.models:
                    if mname in model or model in mname:
                        return rule['name'], mname, []

            # 规则2: 视觉请求
            elif match_type == 'vision' and has_image:
                return self._apply_smart_routing(rule['name'],
                                                  rule.get('target', ''),
                                                  rule.get('fallback', []))

            # 规则3: 关键词匹配
            elif match_type == 'keyword':
                keywords = rule.get('keywords', [])
                text_lower = text.lower()
                if any(kw.lower() in text_lower for kw in keywords):
                    return self._apply_smart_routing(rule['name'],
                                                      rule.get('target', ''),
                                                      rule.get('fallback', []))

            # 规则4: 长度匹配 - 短消息
            elif match_type == 'length':
                max_t = rule.get('max_tokens')
                min_t = rule.get('min_tokens', 0)
                if max_t is not None and token_count <= max_t:
                    return self._apply_smart_routing(rule['name'],
                                                      rule.get('target', ''),
                                                      rule.get('fallback', []))
                elif max_t is None and token_count >= min_t:
                    return self._apply_smart_routing(rule['name'],
                                                      rule.get('target', ''),
                                                      rule.get('fallback', []))

            # 规则5: 默认
            elif match_type == 'default':
                return self._apply_smart_routing(rule['name'],
                                                  rule.get('target', ''),
                                                  rule.get('fallback', []))

        # 兜底（理论上 default 规则会命中）
        return "fallback", "qwen3.6-27b-remote", ["qwen2.5-72b-local", "gemma4-12b-ollama"]

    def _apply_smart_routing(self, rule_name: str, default_target: str,
                             default_fallback: list) -> tuple[str, str, list]:
        """
        应用智能路由策略
        如果 smart_router 启用且有该规则的优化策略，用优化后的 target/fallback
        否则用原始配置
        """
        if self.smart_router is not None:
            smart_target, smart_fallback = self.smart_router.get_optimized_routing(rule_name)
            if smart_target is not None:
                logger.info(f"  🔹 智能路由命中 [{rule_name}]: "
                           f"{smart_target} (原始: {default_target})")
                return rule_name, smart_target, smart_fallback or []

        return rule_name, default_target, default_fallback

    # -------------------- 健康检查 --------------------
    def check_model_health(self, model_name: str) -> bool:
        """检查单个模型是否在线"""
        m = self.models.get(model_name)
        if not m:
            return False

        try:
            if m.api_type == 'openai':
                url = f"{m.base_url.rstrip('/')}/models"
                headers = {}
                if m.api_key and m.api_key != 'EMPTY':
                    headers['Authorization'] = f'Bearer {m.api_key}'
                resp = httpx.get(url, headers=headers, timeout=3)
                healthy = resp.status_code == 200
            elif m.api_type == 'ollama':
                resp = httpx.get(f"{m.base_url}/api/tags", timeout=3)
                healthy = resp.status_code == 200
            else:
                healthy = False

            m.healthy = healthy
            m.last_check = time.time()
            return healthy
        except Exception:
            m.healthy = False
            m.last_check = time.time()
            return False

    def check_all_health(self) -> dict:
        """检查所有模型健康状态"""
        results = {}
        for name in self.models:
            results[name] = self.check_model_health(name)
            m = self.models[name]
            status = "在线" if m.healthy else "离线"
            logger.info(f"  健康检查 {name}: {status}")
        return results

    # -------------------- 请求转发 --------------------
    def _forward_openai(self, model: ModelConfig, messages: list,
                        max_tokens: int, temperature: float, top_p: float,
                        stream: bool) -> tuple[dict, float]:
        """转发到 OpenAI 兼容后端"""
        url = f"{model.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if model.api_key and model.api_key != 'EMPTY':
            headers['Authorization'] = f'Bearer {model.api_key}'

        payload = {
            "model": model.model_name,
            "messages": messages,
            "max_tokens": min(max_tokens, model.max_tokens),
            "temperature": temperature,
            "top_p": top_p,
            "stream": stream,
        }

        start = time.time()
        with httpx.Client(timeout=self.server_config.get('request_timeout', 120)) as client:
            if stream:
                # 流式：返回生成器
                return self._stream_openai(client, url, headers, payload, start), 0
            else:
                resp = client.post(url, json=payload, headers=headers)
                latency = time.time() - start
                resp.raise_for_status()
                data = resp.json()

                # 处理 thinking 模型（Qwen3.6等）：content 为 null 时回退到 reasoning
                self._fix_thinking_response(data)

                return data, latency

    @staticmethod
    def _fix_thinking_response(data: dict):
        """
        处理 thinking 模型的响应格式
        Qwen3.6 / Ollama gemma4 等模型在推理模式下：
        - content 为 null 或空字符串
        - 实际内容在 reasoning / reasoning_content / thinking 字段中
        此方法将内容回填到 content 字段，确保 OpenAI 兼容
        """
        try:
            choices = data.get('choices', [])
            for choice in choices:
                msg = choice.get('message', {})
                content = msg.get('content')

                # content 为 null 或空
                if not content:
                    # 尝试多个可能的 reasoning 字段
                    reasoning = (msg.get('reasoning')
                                 or msg.get('reasoning_content')
                                 or msg.get('thinking')
                                 or '')
                    if reasoning:
                        msg['content'] = reasoning
                        logger.info(f"Thinking 模型回填: 从 reasoning 字段提取 ({len(reasoning)} 字)")
                    else:
                        # 完全没有内容，标记为空
                        msg['content'] = ""
        except Exception as e:
            logger.warning(f"thinking 回填异常: {e}")

    def _stream_openai(self, client, url, headers, payload, start_time):
        """处理 OpenAI 流式响应"""
        with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line:
                    yield line

    def _forward_ollama(self, model: ModelConfig, messages: list,
                        max_tokens: int, temperature: float, top_p: float,
                        stream: bool) -> tuple[dict, float]:
        """
        转发到 Ollama 后端，适配为 OpenAI 格式返回
        Ollama /api/chat 接口格式:
          请求: {"model":"xxx", "messages":[...], "stream":false, "options":{...}}
          响应: {"message":{"role":"assistant","content":"..."}, "eval_count":123, ...}
        """
        url = f"{model.base_url}/api/chat"
        payload = {
            "model": model.model_name,
            "messages": messages,
            "stream": False,  # Ollama 路由暂不支持流式，统一转非流式
            "options": {
                "num_predict": min(max_tokens, model.max_tokens),
                "temperature": temperature,
                "top_p": top_p,
            }
        }

        start = time.time()
        resp = httpx.post(url, json=payload, timeout=self.server_config.get('request_timeout', 120))
        latency = time.time() - start
        resp.raise_for_status()
        data = resp.json()

        # 转换为 OpenAI 兼容格式
        msg_data = data.get('message', {})
        content = msg_data.get('content', '')
        thinking = msg_data.get('thinking', '')

        # 处理 thinking 模型（如 gemma4）：content 为空时回退到 thinking
        if not content and thinking:
            content = thinking
            logger.info(f"Ollama thinking 模型: 从 thinking 字段提取内容 ({len(thinking)} 字)")

        eval_count = data.get('eval_count', 0)
        prompt_eval_count = data.get('prompt_eval_count', 0)

        openai_response = {
            "id": f"chatcmpl-ollama-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model.model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": data.get('done_reason', 'stop')
            }],
            "usage": {
                "prompt_tokens": prompt_eval_count,
                "completion_tokens": eval_count,
                "total_tokens": prompt_eval_count + eval_count
            }
        }
        return openai_response, latency

    # -------------------- 核心路由 --------------------
    def route_and_forward(self, messages: list, model: str = None,
                          max_tokens: int = 4096, temperature: float = 0.7,
                          top_p: float = 0.9, stream: bool = False) -> RouteResult:
        """
        核心路由方法：意图分析 → 规则匹配 → 健康检查 → 转发 → 降级
        """
        result = RouteResult(
            selected_model="",
            selected_backend="",
            matched_rule="",
        )
        start_time = time.time()

        # Step 1: 匹配路由规则
        rule_name, target_model, fallback_list = self._match_rule(messages, model)
        result.matched_rule = rule_name
        logger.info(f"路由决策: 规则={rule_name}, 目标={target_model}, 降级链={fallback_list}")

        # 构建尝试顺序: 目标 + 降级链，并过滤掉未启用的模型
        try_order = [m for m in ([target_model] + fallback_list)
                     if m in self.models and self.models[m].enabled]
        if not try_order:
            # 全被禁用，兜底用远程
            try_order = [name for name, m in self.models.items() if m.enabled]

        # Step 2: 依次尝试，遇到不在线的自动降级
        for model_name in try_order:
            if model_name not in self.models:
                logger.warning(f"模型 '{model_name}' 未在配置中注册，跳过")
                result.tried_models.append(f"{model_name}(未注册)")
                continue

            m = self.models[model_name]
            result.tried_models.append(model_name)

            # 健康检查（缓存5秒内的检查结果）
            if time.time() - m.last_check > 5:
                is_healthy = self.check_model_health(model_name)
            else:
                is_healthy = m.healthy

            if not is_healthy:
                logger.warning(f"模型 '{model_name}' 离线，尝试降级")
                # 按需预热：离线且 auto_start → 后台异步拉起（本次走降级链，不等待）
                if self.lifecycle_manager is not None:
                    triggered = self.lifecycle_manager.ensure_started(model_name)
                    if triggered:
                        logger.info(f"  🔹 已触发后台拉起 '{model_name}'（本次走降级链）")
                continue

            # Step 3: 转发请求
            try:
                logger.info(f"转发到 '{model_name}' ({m.api_type}) → {m.base_url}")

                if m.api_type == 'openai':
                    response, latency = self._forward_openai(
                        m, messages, max_tokens, temperature, top_p, stream
                    )
                elif m.api_type == 'ollama':
                    response, latency = self._forward_ollama(
                        m, messages, max_tokens, temperature, top_p, stream
                    )
                else:
                    raise ValueError(f"不支持的 api_type: {m.api_type}")

                result.selected_model = model_name
                result.selected_backend = m.model_name
                result.response = response
                result.latency = latency

                # 更新平均延迟
                if m.avg_latency == 0:
                    m.avg_latency = latency
                else:
                    m.avg_latency = m.avg_latency * 0.7 + latency * 0.3

                # 记录统计
                usage = response.get('usage', {}) if isinstance(response, dict) else {}
                self._record_stats(
                    rule=rule_name,
                    model=model_name,
                    backend=m.model_name,
                    input_tokens=usage.get('prompt_tokens', 0),
                    output_tokens=usage.get('completion_tokens', 0),
                    latency=latency,
                    success=True
                )

                logger.info(f"✅ 路由成功: {model_name}, "
                           f"延迟={latency:.2f}s, "
                           f"tokens={usage.get('total_tokens', 0)}")

                # 生命周期：转发成功 → 更新最后活跃时间（用于空闲卸载判定）
                if self.lifecycle_manager is not None:
                    self.lifecycle_manager.on_request_success(model_name)

                return result

            except Exception as e:
                logger.error(f"❌ 模型 '{model_name}' 请求失败: {e}")
                result.tried_models[-1] = f"{model_name}(失败: {str(e)[:50]})"
                m.healthy = False
                # 记录失败统计
                self._record_stats(
                    rule=rule_name,
                    model=model_name,
                    backend=m.model_name,
                    input_tokens=0,
                    output_tokens=0,
                    latency=0,
                    success=False,
                    error=str(e)[:100]
                )
                continue

        # 所有模型都失败
        result.error = f"所有模型均不可用，尝试顺序: {result.tried_models}"
        result.latency = time.time() - start_time
        logger.error(f"❌ 路由失败: {result.error}")
        return result

    def _record_stats(self, **kwargs):
        """记录请求统计"""
        stat = RequestStats(
            timestamp=datetime.now().isoformat(),
            **kwargs
        )
        with self.stats_lock:
            self.stats.append(stat)
        # 每10条保存一次
        if len(self.stats) % 10 == 0:
            self._save_stats()

    # -------------------- 统计查询 --------------------
    def get_stats_summary(self) -> dict:
        """获取统计摘要"""
        with self.stats_lock:
            total = len(self.stats)
            if total == 0:
                return {"total": 0}

            success = sum(1 for s in self.stats if s.success)
            total_input = sum(s.input_tokens for s in self.stats)
            total_output = sum(s.output_tokens for s in self.stats)
            total_latency = sum(s.latency for s in self.stats if s.success)

            # 按模型统计
            by_model = {}
            for s in self.stats:
                if s.model not in by_model:
                    by_model[s.model] = {
                        "count": 0, "success": 0, "tokens": 0, "latency": 0
                    }
                by_model[s.model]["count"] += 1
                if s.success:
                    by_model[s.model]["success"] += 1
                    by_model[s.model]["tokens"] += s.input_tokens + s.output_tokens
                    by_model[s.model]["latency"] += s.latency

            # 计算平均延迟
            for m in by_model:
                if by_model[m]["success"] > 0:
                    by_model[m]["avg_latency"] = round(
                        by_model[m]["latency"] / by_model[m]["success"], 2
                    )
                    by_model[m]["success_rate"] = round(
                        by_model[m]["success"] / by_model[m]["count"] * 100, 1
                    )

            # 按规则统计
            by_rule = {}
            for s in self.stats:
                r = s.rule
                if r not in by_rule:
                    by_rule[r] = 0
                by_rule[r] += 1

            return {
                "total": total,
                "success": success,
                "success_rate": round(success / total * 100, 1) if total else 0,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "avg_latency": round(total_latency / success, 2) if success else 0,
                "by_model": by_model,
                "by_rule": by_rule,
            }

    def get_model_list(self) -> list[dict]:
        """获取模型列表（OpenAI /v1/models 格式）"""
        models = []
        for name, m in self.models.items():
            models.append({
                "id": name,
                "object": "model",
                "created": 1700000000,
                "owned_by": "local-router",
                "meta": {
                    "backend": m.model_name,
                    "api_type": m.api_type,
                    "base_url": m.base_url,
                    "priority": m.priority,
                    "speed": m.speed,
                    "quality": m.quality,
                    "cost": m.cost,
                    "healthy": m.healthy,
                    "tags": m.tags,
                    "description": m.description,
                    "avg_latency": round(m.avg_latency, 2),
                }
            })
        return models


# ==================== 模块测试 ====================
if __name__ == "__main__":
    engine = RouterEngine()

    print("\n" + "=" * 60)
    print("路由引擎测试")
    print("=" * 60)

    # 健康检查
    print("\n--- 健康检查 ---")
    health = engine.check_all_health()
    for name, ok in health.items():
        print(f"  {name}: {'✅ 在线' if ok else '❌ 离线'}")

    # 统计摘要
    print("\n--- 统计摘要 ---")
    summary = engine.get_stats_summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    # 模型列表
    print("\n--- 可用模型 ---")
    for m in engine.get_model_list():
        meta = m['meta']
        print(f"  {m['id']:30s} | {meta['speed']:8s} | {meta['quality']:8s} | "
              f"{'✅' if meta['healthy'] else '❌'} | {meta['description']}")

    print("\n" + "=" * 60)
    print("路由引擎就绪，等待 API 服务调用")
    print("=" * 60)
