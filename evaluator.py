#!/usr/bin/env python3
"""
本地多模型评测平台 - 评测引擎核心
====================================
职责：
  1. 加载标准题库
  2. 逐题调用模型 → 采集响应
  3. 自动打分（关键词/格式/长度/代码可运行性）
  4. 三维评分：质量分 + 速度分 + 综合分
  5. 结果持久化 → 生成排行榜

评分体系：
  - 质量分（0-100）：关键词命中 + 格式正确 + 长度合规 + 代码可运行
  - 速度分（0-100）：按 tokens/s 归一化
  - 综合分 = 质量分*0.6 + 速度分*0.3 + 成本分*0.1

使用方式：
  from evaluator import EvalEngine
  engine = EvalEngine()
  results = engine.run_eval(model_names=["qwen3.6-27b-remote", "gemma4-12b-ollama"])
  leaderboard = engine.get_leaderboard()
"""

import json
import time
import re
import os
import subprocess
import tempfile
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
import yaml
import httpx

from router import RouterEngine, logger

# ==================== 数据结构 ====================
@dataclass
class EvalQuestion:
    """单道评测题"""
    id: str
    category: str
    difficulty: str
    prompt: str
    max_tokens: int
    judge: dict


@dataclass
class EvalResult:
    """单道题的评测结果"""
    question_id: str
    category: str
    difficulty: str
    model: str
    response: str = ""
    latency: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    quality_score: float = 0        # 0-100
    speed_score: float = 0          # 0-100
    cost_score: float = 0           # 0-100
    total_score: float = 0          # 加权综合分
    error: str = ""
    judge_details: dict = field(default_factory=dict)


@dataclass
class EvalSession:
    """一次完整评测会话"""
    session_id: str
    timestamp: str
    models: list
    questions: list
    results: list[EvalResult] = field(default_factory=list)
    duration: float = 0


# ==================== 评测引擎 ====================
class EvalEngine:
    """评测引擎核心"""

    # 评分权重
    WEIGHT_QUALITY = 0.6
    WEIGHT_SPEED = 0.3
    WEIGHT_COST = 0.1

    # 成本分映射
    COST_SCORE = {
        "free": 100,   # 远程免费
        "local": 80,   # 本地不花钱但占资源
        "low": 60,
        "medium": 30,
        "high": 10,
    }

    def __init__(self, dataset_path: str = None):
        if dataset_path is None:
            dataset_path = Path(__file__).parent / "eval_dataset.yaml"
        self.dataset_path = Path(dataset_path)
        self.questions: list[EvalQuestion] = []
        self.router = RouterEngine()
        self.results_dir = Path(__file__).parent / "eval_results"
        self.results_dir.mkdir(exist_ok=True)
        self._load_dataset()

    def _load_dataset(self):
        """加载题库"""
        with open(self.dataset_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        for q in data:
            self.questions.append(EvalQuestion(
                id=q['id'],
                category=q['category'],
                difficulty=q['difficulty'],
                prompt=q['prompt'],
                max_tokens=q.get('max_tokens', 800),
                judge=q.get('judge', {})
            ))
        logger.info(f"题库加载完成: {len(self.questions)} 道题")

    def reload_dataset(self):
        """热重载题库"""
        self.questions.clear()
        self._load_dataset()

    # -------------------- 评测执行 --------------------
    def run_eval(self, model_names: list[str], question_ids: list[str] = None,
                 timeout: int = 120) -> EvalSession:
        """
        执行评测
        model_names: 要评测的模型名列表
        question_ids: 指定题目ID列表，None=全部
        timeout: 每题超时秒数
        """
        session = EvalSession(
            session_id=datetime.now().strftime("%Y%m%d_%H%M%S"),
            timestamp=datetime.now().isoformat(),
            models=model_names,
            questions=[q.id for q in self.questions] if not question_ids else question_ids,
        )

        start_time = time.time()
        questions = self.questions
        if question_ids:
            questions = [q for q in self.questions if q.id in question_ids]

        total = len(model_names) * len(questions)
        current = 0
        logger.info(f"开始评测: {len(model_names)} 个模型 × {len(questions)} 道题 = {total} 轮")

        for model_name in model_names:
            if model_name not in self.router.models:
                logger.warning(f"模型 '{model_name}' 未注册，跳过")
                continue

            for q in questions:
                current += 1
                logger.info(f"[{current}/{total}] {model_name} ← {q.id} ({q.category}/{q.difficulty})")

                result = self._eval_single(model_name, q, timeout)
                session.results.append(result)

        session.duration = time.time() - start_time
        self._save_session(session)
        logger.info(f"评测完成: {total} 轮, 耗时 {session.duration:.1f}s, "
                     f"平均质量分 {self._avg_score(session.results, 'quality_score')}")

        return session

    def _eval_single(self, model_name: str, question: EvalQuestion,
                     timeout: int) -> EvalResult:
        """评测单道题"""
        result = EvalResult(
            question_id=question.id,
            category=question.category,
            difficulty=question.difficulty,
            model=model_name,
        )

        # 构造请求
        messages = [{"role": "user", "content": question.prompt}]
        m = self.router.models[model_name]

        # 健康检查
        if not self.router.check_model_health(model_name):
            result.error = "模型离线"
            result.quality_score = 0
            result.speed_score = 0
            result.cost_score = self.COST_SCORE.get(m.cost, 50)
            result.total_score = result.cost_score * self.WEIGHT_COST
            return result

        # 调用模型
        try:
            start = time.time()
            # 思维链模型需要更大 max_tokens（思考过程消耗大量 token）
            eval_max_tokens = question.max_tokens
            is_thinking = ('thinking' in (m.tags or []) or
                           'thinking' in model_name.lower())
            if is_thinking:
                eval_max_tokens = max(question.max_tokens * 5, 2048)
                logger.info(f"  思维链模型检测到，max_tokens {question.max_tokens} → {eval_max_tokens}")

            if m.api_type == 'openai':
                response, latency = self.router._forward_openai(
                    m, messages, eval_max_tokens, 0.7, 0.9, stream=False
                )
            elif m.api_type == 'ollama':
                response, latency = self.router._forward_ollama(
                    m, messages, eval_max_tokens, 0.7, 0.9, stream=False
                )
            else:
                raise ValueError(f"不支持的 api_type: {m.api_type}")

            result.latency = latency
            result.response = response.get('choices', [{}])[0].get('message', {}).get('content', '')
            usage = response.get('usage', {})
            result.input_tokens = usage.get('prompt_tokens', 0)
            result.output_tokens = usage.get('completion_tokens', 0)

            # 评分
            result.quality_score = self._score_quality(result.response, question.judge)
            result.speed_score = self._score_speed(result.output_tokens, latency)
            result.cost_score = self.COST_SCORE.get(m.cost, 50)
            result.total_score = (
                result.quality_score * self.WEIGHT_QUALITY +
                result.speed_score * self.WEIGHT_SPEED +
                result.cost_score * self.WEIGHT_COST
            )

            logger.info(f"  质量={result.quality_score:.0f} 速度={result.speed_score:.0f} "
                       f"成本={result.cost_score:.0f} 综合={result.total_score:.1f} "
                       f"({latency:.1f}s, {result.output_tokens}tok)")

        except Exception as e:
            result.error = str(e)[:200]
            result.cost_score = self.COST_SCORE.get(m.cost, 50)
            result.total_score = result.cost_score * self.WEIGHT_COST
            logger.error(f"  评测失败: {e}")

        return result

    # -------------------- 自动打分 --------------------
    def _score_quality(self, response: str, judge: dict) -> float:
        """
        质量打分（0-100）
        组成：关键词命中(50%) + 格式(20%) + 长度(20%) + 代码可运行(10%)
        """
        if not response or not response.strip():
            return 0

        score = 0.0
        details = {}

        judge_type = judge.get('type', 'keyword')

        # 1. 关键词评分（所有题型通用）
        must_include = judge.get('must_include', [])
        should_include = judge.get('should_include', [])

        response_lower = response.lower()
        must_hits = sum(1 for kw in must_include if kw.lower() in response_lower)
        should_hits = sum(1 for kw in should_include if kw.lower() in response_lower)

        must_ratio = must_hits / len(must_include) if must_include else 1.0
        should_ratio = should_hits / len(should_include) if should_include else 0

        keyword_score = must_ratio * 70 + should_ratio * 30  # 关键词满分100
        details['keyword'] = round(keyword_score, 1)
        details['must_hits'] = f"{must_hits}/{len(must_include)}"
        details['should_hits'] = f"{should_hits}/{len(should_include)}"

        # 2. 格式评分
        format_score = 100.0
        if judge_type == 'format' and judge.get('expected_format') == 'json':
            # JSON格式检查
            json_ok = self._check_json(response)
            format_score = 100 if json_ok else 30
            details['json_valid'] = json_ok
        elif judge_type == 'code' and judge.get('runnable'):
            # 代码可运行检查
            code_ok = self._check_code_runnable(response)
            format_score = 100 if code_ok else 50
            details['code_runnable'] = code_ok
        elif judge_type == 'math' or judge_type == 'logic':
            # 数学/逻辑题：检查期望答案
            expected = judge.get('expected_answer', '')
            if expected and expected.lower() in response_lower:
                format_score = 100
            else:
                format_score = 40
            details['expected_answer'] = expected

        details['format'] = round(format_score, 1)

        # 3. 长度评分
        length_score = 100.0
        max_len = judge.get('max_length')
        min_len = judge.get('min_length', 10)
        resp_len = len(response)

        if max_len and resp_len > max_len:
            # 超长扣分
            length_score = max(0, 100 - (resp_len - max_len) / max_len * 100)
        elif resp_len < min_len:
            # 太短扣分
            length_score = resp_len / min_len * 100

        details['length'] = round(length_score, 1)
        details['resp_length'] = resp_len

        # 4. 综合质量分
        if judge_type == 'code':
            # 代码题：关键词50% + 可运行30% + 长度20%
            score = keyword_score * 0.5 + format_score * 0.3 + length_score * 0.2
        elif judge_type in ('math', 'logic'):
            # 数学/逻辑题：关键词40% + 答案40% + 长度20%
            score = keyword_score * 0.4 + format_score * 0.4 + length_score * 0.2
        elif judge_type == 'format':
            # 格式题：关键词30% + 格式50% + 长度20%
            score = keyword_score * 0.3 + format_score * 0.5 + length_score * 0.2
        else:
            # 通用：关键词50% + 格式20% + 长度30%
            score = keyword_score * 0.5 + format_score * 0.2 + length_score * 0.3

        result_score = max(0, min(100, score))
        return round(result_score, 1)

    def _score_speed(self, output_tokens: int, latency: float) -> float:
        """
        速度打分（0-100）
        按照 tokens/s 分档：
          ≥30 tok/s → 100分
          ≥20 tok/s → 80分
          ≥10 tok/s → 60分
          ≥5 tok/s  → 40分
          ≥1 tok/s  → 20分
          <1 tok/s  → 10分
          0 tok     → 0分
        """
        if output_tokens == 0 or latency == 0:
            return 0

        tps = output_tokens / latency

        if tps >= 30:
            return 100
        elif tps >= 20:
            return 80
        elif tps >= 10:
            return 60
        elif tps >= 5:
            return 40
        elif tps >= 1:
            return 20
        else:
            return 10

    # -------------------- 格式检查工具 --------------------
    @staticmethod
    def _check_json(text: str) -> bool:
        """检查文本中是否包含有效JSON"""
        # 尝试直接解析
        try:
            json.loads(text.strip())
            return True
        except:
            pass

        # 尝试提取代码块中的JSON
        patterns = [
            r'```json\s*(.*?)\s*```',
            r'```\s*(.*?)\s*```',
            r'\{[^{}]*\}',
        ]
        for pat in patterns:
            matches = re.findall(pat, text, re.DOTALL)
            for m in matches:
                try:
                    json.loads(m.strip())
                    return True
                except:
                    continue

        return False

    @staticmethod
    def _check_code_runnable(response: str) -> bool:
        """检查代码是否可运行（提取代码块并执行）"""
        # 提取Python代码
        code = None

        # 优先提取代码块
        patterns = [
            r'```python\s*(.*?)\s*```',
            r'```\s*(.*?)\s*```',
        ]
        for pat in patterns:
            matches = re.findall(pat, response, re.DOTALL)
            if matches:
                code = matches[-1].strip()  # 取最后一个代码块
                break

        if not code:
            # 没有代码块，尝试检测是否有 def 开头的代码
            lines = [l for l in response.split('\n') if l.strip()]
            code_lines = [l for l in lines if l.startswith(('def ', 'import ', 'from ', 'class ', '    ', '\t'))]
            if code_lines:
                code = '\n'.join(code_lines)

        if not code:
            return False

        # 尝试执行
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(code)
                f.write('\n')  # 确保有换行
                temp_path = f.name

            result = subprocess.run(
                ['python3', '-c', f'exec(open("{temp_path}").read())'],
                capture_output=True, timeout=10
            )
            os.unlink(temp_path)
            return result.returncode == 0
        except Exception as e:
            try:
                os.unlink(temp_path)
            except:
                pass
            logger.debug(f"代码运行检查失败: {e}")
            return False

    # -------------------- 结果持久化 --------------------
    def _save_session(self, session: EvalSession):
        """保存评测会话结果"""
        filepath = self.results_dir / f"eval_{session.session_id}.json"

        data = {
            'session_id': session.session_id,
            'timestamp': session.timestamp,
            'models': session.models,
            'questions': session.questions,
            'duration': round(session.duration, 1),
            'results': [asdict(r) for r in session.results],
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"评测结果已保存: {filepath}")
        return filepath

    def list_sessions(self) -> list[dict]:
        """列出所有历史评测会话"""
        sessions = []
        for f in sorted(self.results_dir.glob("eval_*.json"), reverse=True):
            try:
                with open(f, 'r') as fp:
                    data = json.load(fp)
                sessions.append({
                    'file': str(f),
                    'session_id': data['session_id'],
                    'timestamp': data['timestamp'],
                    'models': data['models'],
                    'question_count': len(data['questions']),
                    'result_count': len(data['results']),
                    'duration': data['duration'],
                })
            except:
                continue
        return sessions

    def load_session(self, session_id: str) -> dict:
        """加载某个评测会话的完整数据"""
        filepath = self.results_dir / f"eval_{session_id}.json"
        if not filepath.exists():
            return {}
        with open(filepath, 'r') as f:
            return json.load(f)

    # -------------------- 排行榜 --------------------
    def get_leaderboard(self, session: EvalSession = None,
                        session_id: str = None) -> dict:
        """
        生成排行榜
        返回: {model: {quality_avg, speed_avg, cost_avg, total_avg, ...}}
        """
        results = []
        if session:
            results = session.results
        elif session_id:
            data = self.load_session(session_id)
            results = [EvalResult(**r) for r in data.get('results', [])]
        else:
            # 用最新一次评测
            sessions = self.list_sessions()
            if sessions:
                data = self.load_session(sessions[0]['session_id'])
                results = [EvalResult(**r) for r in data.get('results', [])]

        if not results:
            return {}

        # 按模型聚合
        board = {}
        for r in results:
            if r.model not in board:
                board[r.model] = {
                    'count': 0,
                    'quality_scores': [],
                    'speed_scores': [],
                    'cost_scores': [],
                    'total_scores': [],
                    'latencies': [],
                    'tokens': 0,
                    'errors': 0,
                    'by_category': {},
                }
            b = board[r.model]
            b['count'] += 1
            b['quality_scores'].append(r.quality_score)
            b['speed_scores'].append(r.speed_score)
            b['cost_scores'].append(r.cost_score)
            b['total_scores'].append(r.total_score)
            b['latencies'].append(r.latency)
            b['tokens'] += r.output_tokens
            if r.error:
                b['errors'] += 1

            # 按分类统计
            cat = r.category
            if cat not in b['by_category']:
                b['by_category'][cat] = []
            b['by_category'][cat].append(r.total_score)

        # 计算平均值并排序
        leaderboard = []
        for model, b in board.items():
            def avg(lst):
                return round(sum(lst) / len(lst), 1) if lst else 0

            # 分类平均
            cat_scores = {}
            for cat, scores in b['by_category'].items():
                cat_scores[cat] = avg(scores)

            leaderboard.append({
                'model': model,
                'quality_avg': avg(b['quality_scores']),
                'speed_avg': avg(b['speed_scores']),
                'cost_avg': avg(b['cost_scores']),
                'total_avg': avg(b['total_scores']),
                'avg_latency': round(avg(b['latencies']), 2),
                'total_tokens': b['tokens'],
                'success_rate': round((b['count'] - b['errors']) / b['count'] * 100, 1),
                'question_count': b['count'],
                'by_category': cat_scores,
            })

        # 按综合分排序
        leaderboard.sort(key=lambda x: x['total_avg'], reverse=True)

        return {
            'ranking': leaderboard,
            'weights': {
                'quality': self.WEIGHT_QUALITY,
                'speed': self.WEIGHT_SPEED,
                'cost': self.WEIGHT_COST,
            }
        }

    @staticmethod
    def _avg_score(results: list[EvalResult], field_name: str) -> float:
        if not results:
            return 0
        return round(sum(getattr(r, field_name) for r in results) / len(results), 1)


# ==================== 模块测试 ====================
if __name__ == "__main__":
    engine = EvalEngine()

    print("\n" + "=" * 60)
    print("评测引擎测试")
    print("=" * 60)

    # 题库概览
    print(f"\n题库: {len(engine.questions)} 道题")
    categories = {}
    for q in engine.questions:
        categories[q.category] = categories.get(q.category, 0) + 1
    print("分类:")
    for cat, cnt in sorted(categories.items()):
        print(f"  {cat}: {cnt}题")

    # 模型健康检查
    print("\n--- 模型状态 ---")
    health = engine.router.check_all_health()
    for name, ok in health.items():
        if ok:
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ {name}")

    # 评分逻辑自测
    print("\n--- 评分逻辑自测 ---")

    # 测试JSON格式检查
    test_cases = [
        ('{"name": "test"}', True),
        ('```json\n{"a": 1}\n```', True),
        ('这不是JSON', False),
    ]
    for text, expected in test_cases:
        result = engine._check_json(text)
        print(f"  JSON检查 '{text[:30]}': {'✅' if result == expected else '❌'} (期望{expected}, 实际{result})")

    # 测试质量评分
    test_response = "区块链是一种去中心化、不可篡改的分布式账本技术，通过密码学实现共识。"
    test_judge = {
        'type': 'keyword',
        'must_include': ["去中心化", "分布式"],
        'should_include': ["不可篡改", "密码学", "共识"],
        'min_length': 20
    }
    quality = engine._score_quality(test_response, test_judge)
    print(f"\n  质量评分测试: 响应='{test_response}'")
    print(f"  得分: {quality}/100")

    # 测试速度评分
    speed_tests = [(100, 5, "20tok/s"), (50, 50, "1tok/s"), (0, 0, "0tok")]
    for tokens, latency, label in speed_tests:
        score = engine._score_speed(tokens, latency)
        print(f"  速度评分 {label}: {score}/100")

    print("\n" + "=" * 60)
    print("评测引擎就绪")
    print("=" * 60)
