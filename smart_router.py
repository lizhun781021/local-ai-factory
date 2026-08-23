#!/usr/bin/env python3
"""
本地多模型路由平台 - 智能路由引擎
====================================
职责：
  1. 从历史请求(router_stats.json)学习各模型在不同规则下的实战表现
  2. 从评测结果(eval_results/)获取各模型在不同类别的质量得分
  3. 综合计算 → 自动生成优化后的路由策略(覆盖静态规则)
  4. 支持定时学习(积累够数据才启用) + 手动触发
  5. 冷启动保护：数据不足时回退静态规则

评分模型：
  对每个 (规则, 模型) 对，计算综合表现分 S：
    S = 质量分×0.4 + 效率分×0.3 + 成本分×0.2 + 稳定性分×0.1
  
  - 质量分：来自评测，该模型在该规则对应类别的平均质量分(0-100)
  - 效率分：历史请求中该模型的平均速度分(按tokens/s)(0-100)
  - 成本分：模型配置的cost映射(0-100)
  - 稳定性分：历史请求中该模型的成功率(0-100)

策略生成：
  对每条路由规则，按综合分 S 对可用模型排序 → 新的 target + fallback
"""

import json
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import defaultdict
import yaml

from router import RouterEngine, logger

# ==================== 数据结构 ====================
@dataclass
class ModelPerformance:
    """单个模型在某个规则下的表现"""
    model: str
    rule: str
    request_count: int = 0
    success_count: int = 0
    avg_latency: float = 0
    avg_tokens: float = 0
    avg_tps: float = 0             # tokens/s
    quality_score: float = 0       # 来自评测
    cost_score: float = 0          # 来自配置
    # 计算得出的综合分
    efficiency_score: float = 0    # 效率分(0-100)
    stability_score: float = 0     # 稳定性分(0-100)
    total_score: float = 0         # 综合表现分(0-100)


@dataclass 
class OptimizedStrategy:
    """优化后的路由策略"""
    rule_name: str
    original_target: str           # 原始配置的目标
    original_fallback: list        # 原始配置的降级链
    optimized_target: str          # 优化后的目标
    optimized_fallback: list       # 优化后的降级链
    changed: bool                  # 是否发生了变化
    model_scores: dict = field(default_factory=dict)  # 各模型得分详情
    learn_time: str = ""
    data_points: int = 0           # 学习样本数


# ==================== 智能路由引擎 ====================
class SmartRouter:
    """智能路由引擎"""

    # 评分权重
    WEIGHT_QUALITY = 0.4
    WEIGHT_EFFICIENCY = 0.3
    WEIGHT_COST = 0.2
    WEIGHT_STABILITY = 0.1

    # 冷启动阈值：某规则下需要多少条历史数据才启用优化
    MIN_DATA_POINTS = 5

    # 成本分映射
    COST_SCORE = {
        "free": 100,
        "local": 80,
        "low": 60,
        "medium": 30,
        "high": 10,
    }

    # 路由规则 → 评测类别 的映射关系
    RULE_TO_CATEGORY = {
        "code-task": ["代码生成"],
        "reasoning-task": ["逻辑推理", "数据分析"],
        "short-chat": ["知识问答", "翻译", "创意写作"],
        "long-context": ["文本摘要", "数据分析"],
        "default": ["知识问答", "电信业务", "公文写作", "工具使用", "指令遵循"],
    }

    def __init__(self, router_engine: RouterEngine = None):
        self.router = router_engine or RouterEngine()
        self.strategies: dict[str, OptimizedStrategy] = {}  # rule_name → strategy
        self.enabled: bool = False
        self.last_learn_time: str = ""
        self.learn_count: int = 0
        self.strategy_file = Path(__file__).parent / "smart_strategy.json"
        self._load_strategy()

    # -------------------- 策略持久化 --------------------
    def _load_strategy(self):
        """从文件加载已保存的策略"""
        if not self.strategy_file.exists():
            return

        try:
            with open(self.strategy_file, 'r') as f:
                data = json.load(f)
            self.enabled = data.get('enabled', False)
            self.last_learn_time = data.get('last_learn_time', '')
            self.learn_count = data.get('learn_count', 0)

            for rule_name, s in data.get('strategies', {}).items():
                self.strategies[rule_name] = OptimizedStrategy(
                    rule_name=s['rule_name'],
                    original_target=s['original_target'],
                    original_fallback=s.get('original_fallback', []),
                    optimized_target=s['optimized_target'],
                    optimized_fallback=s.get('optimized_fallback', []),
                    changed=s.get('changed', False),
                    model_scores=s.get('model_scores', {}),
                    learn_time=s.get('learn_time', ''),
                    data_points=s.get('data_points', 0),
                )
            logger.info(f"智能策略加载: {len(self.strategies)} 条策略, 启用={self.enabled}, 学习次数={self.learn_count}")
        except Exception as e:
            logger.warning(f"策略加载失败: {e}")

    def _save_strategy(self):
        """保存策略到文件"""
        data = {
            'enabled': self.enabled,
            'last_learn_time': self.last_learn_time,
            'learn_count': self.learn_count,
            'strategies': {name: asdict(s) for name, s in self.strategies.items()},
        }
        with open(self.strategy_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # -------------------- 数据收集 --------------------
    def _load_history_stats(self) -> list[dict]:
        """从 router_stats.json 加载历史请求"""
        stats_file = Path(__file__).parent / self.router.server_config.get('stats_file', 'router_stats.json')
        if not stats_file.exists():
            return []
        try:
            with open(stats_file, 'r') as f:
                return json.load(f)
        except:
            return []

    def _load_eval_results(self) -> list[dict]:
        """从 eval_results/ 加载最新一次评测结果"""
        eval_dir = Path(__file__).parent / "eval_results"
        if not eval_dir.exists():
            return []

        sessions = sorted(eval_dir.glob("eval_*.json"), reverse=True)
        if not sessions:
            return []

        # 合并最近3次评测结果（取平均更稳定）
        all_results = []
        for sf in sessions[:3]:
            try:
                with open(sf, 'r') as f:
                    data = json.load(f)
                all_results.extend(data.get('results', []))
            except:
                continue

        return all_results

    # -------------------- 核心学习 --------------------
    def learn(self) -> dict:
        """
        学习入口：分析历史数据 + 评测结果 → 生成优化策略
        返回学习报告
        """
        logger.info("=" * 50)
        logger.info("智能路由学习开始...")
        learn_start = time.time()

        # 1. 收集数据
        history = self._load_history_stats()
        eval_results = self._load_eval_results()
        logger.info(f"数据源: 历史请求 {len(history)} 条, 评测结果 {len(eval_results)} 条")

        if len(history) < 3 and len(eval_results) < 3:
            logger.warning("数据不足，跳过学习")
            return {
                "status": "insufficient_data",
                "message": f"数据不足({len(history)}历史+{len(eval_results)}评测)，需要更多请求积累",
                "learned": False,
            }

        # 2. 聚合历史数据：按 (规则, 模型) 分组
        perf_map = self._aggregate_history(history)

        # 3. 注入评测质量分
        self._inject_eval_scores(perf_map, eval_results)

        # 4. 注入成本分
        self._inject_cost_scores(perf_map)

        # 5. 计算综合分
        self._compute_scores(perf_map)

        # 6. 生成优化策略
        new_strategies = self._generate_strategies(perf_map)

        # 7. 对比变化
        changes = []
        for rule_name, strategy in new_strategies.items():
            old = self.strategies.get(rule_name)
            is_new = old is None
            target_changed = is_new or strategy.optimized_target != old.optimized_target
            fallback_changed = is_new or strategy.optimized_fallback != old.optimized_fallback

            if target_changed or fallback_changed:
                changes.append({
                    'rule': rule_name,
                    'old_target': old.optimized_target if old else '(新)',
                    'new_target': strategy.optimized_target,
                    'old_fallback': old.optimized_fallback if old else [],
                    'new_fallback': strategy.optimized_fallback,
                    'data_points': strategy.data_points,
                })

        # 8. 保存策略
        self.strategies = new_strategies
        self.learn_count += 1
        self.last_learn_time = datetime.now().isoformat()
        self._save_strategy()

        duration = time.time() - learn_start
        logger.info(f"学习完成: {len(new_strategies)} 条策略, {len(changes)} 条变化, 耗时 {duration:.1f}s")
        logger.info("=" * 50)

        return {
            "status": "ok",
            "learned": True,
            "learn_count": self.learn_count,
            "duration": round(duration, 2),
            "history_count": len(history),
            "eval_count": len(eval_results),
            "strategies_generated": len(new_strategies),
            "changes": changes,
            "model_scores": self._get_score_summary(perf_map),
        }

    def _aggregate_history(self, history: list[dict]) -> dict[tuple[str, str], ModelPerformance]:
        """
        按规则×模型聚合历史请求
        返回: {(rule, model): ModelPerformance}
        """
        agg = {}

        for stat in history:
            rule = stat.get('rule', 'unknown')
            model = stat.get('model', 'unknown')
            success = stat.get('success', False)
            latency = stat.get('latency', 0)
            input_tokens = stat.get('input_tokens', 0)
            output_tokens = stat.get('output_tokens', 0)

            key = (rule, model)
            if key not in agg:
                agg[key] = ModelPerformance(
                    model=model, rule=rule,
                    cost_score=self.COST_SCORE.get(
                        self.router.models.get(model, type('', (), {'cost': 'local'})).cost,
                        50
                    )
                )

            p = agg[key]
            p.request_count += 1
            if success:
                p.success_count += 1
            p.avg_latency += latency
            p.avg_tokens += output_tokens

        # 计算平均值
        for p in agg.values():
            if p.success_count > 0:
                p.avg_latency = p.avg_latency / p.success_count
                p.avg_tokens = p.avg_tokens / p.success_count
                p.avg_tps = p.avg_tokens / p.avg_latency if p.avg_latency > 0 else 0
            else:
                p.avg_latency = p.avg_latency / max(p.request_count, 1)

        return agg

    def _inject_eval_scores(self, perf_map: dict, eval_results: list[dict]):
        """将评测的质量分注入到 perf_map"""
        # 按 (模型, 类别) 聚合评测质量分
        eval_scores = defaultdict(list)  # (model, category) → [quality_scores]

        for r in eval_results:
            model = r.get('model', '')
            category = r.get('category', '')
            quality = r.get('quality_score', 0)
            eval_scores[(model, category)].append(quality)

        # 对每个 (rule, model)，找到该规则关联的评测类别，取平均质量分
        for (rule, model), p in perf_map.items():
            categories = self.RULE_TO_CATEGORY.get(rule, [])
            scores = []
            for cat in categories:
                scores.extend(eval_scores.get((model, cat), []))

            if scores:
                p.quality_score = sum(scores) / len(scores)
            else:
                # 没有评测数据，给中性分
                p.quality_score = 50

    def _inject_cost_scores(self, perf_map: dict):
        """注入成本分"""
        for (rule, model), p in perf_map.items():
            m = self.router.models.get(model)
            if m:
                p.cost_score = self.COST_SCORE.get(m.cost, 50)
            else:
                p.cost_score = 50

    def _compute_scores(self, perf_map: dict):
        """计算效率分、稳定性分、综合分"""
        for p in perf_map.values():
            # 效率分 (0-100): 按 tokens/s 分档
            if p.avg_tps >= 30:
                p.efficiency_score = 100
            elif p.avg_tps >= 20:
                p.efficiency_score = 80
            elif p.avg_tps >= 10:
                p.efficiency_score = 60
            elif p.avg_tps >= 5:
                p.efficiency_score = 40
            elif p.avg_tps >= 1:
                p.efficiency_score = 20
            else:
                p.efficiency_score = 0

            # 如果没有实际速度数据但有延迟，用延迟反推
            if p.avg_tps == 0 and p.avg_latency > 0:
                if p.avg_latency <= 5:
                    p.efficiency_score = 80
                elif p.avg_latency <= 15:
                    p.efficiency_score = 60
                elif p.avg_latency <= 30:
                    p.efficiency_score = 40
                else:
                    p.efficiency_score = 20

            # 稳定性分 (0-100): 成功率
            if p.request_count > 0:
                p.stability_score = p.success_count / p.request_count * 100
            else:
                p.stability_score = 50

            # 综合分
            p.total_score = (
                p.quality_score * self.WEIGHT_QUALITY +
                p.efficiency_score * self.WEIGHT_EFFICIENCY +
                p.cost_score * self.WEIGHT_COST +
                p.stability_score * self.WEIGHT_STABILITY
            )

    def _generate_strategies(self, perf_map: dict) -> dict[str, OptimizedStrategy]:
        """为每条路由规则生成优化策略"""
        strategies = {}

        # 按规则分组
        rule_perfs = defaultdict(list)
        for (rule, model), p in perf_map.items():
            rule_perfs[rule].append(p)

        # 对每条规则，获取配置中的原始设定
        for rule_cfg in self.router.rules:
            rule_name = rule_cfg['name']
            original_target = rule_cfg.get('target', '')
            original_fallback = rule_cfg.get('fallback', [])

            # 跳过 explicit 和 vision（这两个有固定逻辑）
            if rule_cfg['match_type'] in ('explicit', 'vision'):
                strategies[rule_name] = OptimizedStrategy(
                    rule_name=rule_name,
                    original_target=original_target,
                    original_fallback=original_fallback,
                    optimized_target=original_target,
                    optimized_fallback=original_fallback,
                    changed=False,
                    data_points=0,
                )
                continue

            # 获取该规则下的模型表现
            perfs = rule_perfs.get(rule_name, [])

            # 如果历史数据不足，补充所有在线模型（给中性分）
            if len(perfs) < 2:
                # 数据不足，用配置原始策略
                strategies[rule_name] = OptimizedStrategy(
                    rule_name=rule_name,
                    original_target=original_target,
                    original_fallback=original_fallback,
                    optimized_target=original_target,
                    optimized_fallback=original_fallback,
                    changed=False,
                    data_points=sum(p.request_count for p in perfs),
                    model_scores={p.model: round(p.total_score, 1) for p in perfs},
                )
                continue

            # 数据充足，按综合分排序
            perfs_sorted = sorted(perfs, key=lambda p: p.total_score, reverse=True)

            # 选最优作为 target，其余作为 fallback
            optimized_target = perfs_sorted[0].model
            optimized_fallback = [p.model for p in perfs_sorted[1:]]

            # 也加入原始配置中但历史无数据的模型（排最后）
            all_configured = [original_target] + original_fallback
            for m in all_configured:
                if m not in [p.model for p in perfs_sorted] and m in self.router.models:
                    optimized_fallback.append(m)

            # 对比是否变化
            changed = (optimized_target != original_target or
                       optimized_fallback != original_fallback)

            data_points = sum(p.request_count for p in perfs)

            model_scores = {}
            for p in perfs_sorted:
                model_scores[p.model] = {
                    'total': round(p.total_score, 1),
                    'quality': round(p.quality_score, 1),
                    'efficiency': round(p.efficiency_score, 1),
                    'cost': round(p.cost_score, 1),
                    'stability': round(p.stability_score, 1),
                    'requests': p.request_count,
                    'success_rate': round(p.stability_score, 1),
                    'avg_latency': round(p.avg_latency, 2),
                }

            strategies[rule_name] = OptimizedStrategy(
                rule_name=rule_name,
                original_target=original_target,
                original_fallback=original_fallback,
                optimized_target=optimized_target,
                optimized_fallback=optimized_fallback,
                changed=changed,
                model_scores=model_scores,
                learn_time=datetime.now().isoformat(),
                data_points=data_points,
            )

            if changed:
                logger.info(f"  策略变化 [{rule_name}]: "
                           f"{original_target} → {optimized_target}")
                logger.info(f"    降级链: {original_fallback} → {optimized_fallback}")

        return strategies

    # -------------------- 路由查询 --------------------
    def get_strategy(self, rule_name: str) -> Optional[OptimizedStrategy]:
        """查询某条规则的优化策略"""
        if not self.enabled:
            return None
        return self.strategies.get(rule_name)

    def get_optimized_routing(self, rule_name: str) -> tuple[str, list]:
        """
        获取优化后的路由目标和降级链
        返回: (target_model, fallback_list)
        如果智能路由未启用或无策略，返回空（由调用方用原始规则）
        """
        if not self.enabled:
            return None, None

        strategy = self.strategies.get(rule_name)
        if strategy is None:
            return None, None

        return strategy.optimized_target, strategy.optimized_fallback

    # -------------------- 控制 --------------------
    def enable(self):
        """启用智能路由"""
        if not self.strategies:
            return False, "无策略数据，请先执行学习"
        self.enabled = True
        self._save_strategy()
        logger.info("智能路由已启用")
        return True, f"智能路由已启用，基于 {len(self.strategies)} 条优化策略"

    def disable(self):
        """禁用智能路由"""
        self.enabled = False
        self._save_strategy()
        logger.info("智能路由已禁用，回退静态规则")
        return True, "智能路由已禁用，回退静态规则"

    # -------------------- 状态报告 --------------------
    def get_status(self) -> dict:
        """获取智能路由状态报告"""
        return {
            'enabled': self.enabled,
            'learn_count': self.learn_count,
            'last_learn_time': self.last_learn_time,
            'strategy_count': len(self.strategies),
            'changed_count': sum(1 for s in self.strategies.values() if s.changed),
            'strategies': {
                name: {
                    'rule': s.rule_name,
                    'original_target': s.original_target,
                    'original_fallback': s.original_fallback,
                    'optimized_target': s.optimized_target,
                    'optimized_fallback': s.optimized_fallback,
                    'changed': s.changed,
                    'data_points': s.data_points,
                    'model_scores': s.model_scores,
                }
                for name, s in self.strategies.items()
            }
        }

    def _get_score_summary(self, perf_map: dict) -> dict:
        """生成得分摘要"""
        summary = {}
        for (rule, model), p in perf_map.items():
            if rule not in summary:
                summary[rule] = {}
            summary[rule][model] = {
                'total': round(p.total_score, 1),
                'quality': round(p.quality_score, 1),
                'efficiency': round(p.efficiency_score, 1),
                'cost': round(p.cost_score, 1),
                'stability': round(p.stability_score, 1),
                'requests': p.request_count,
            }
        return summary


# ==================== 模块测试 ====================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("智能路由引擎测试")
    print("=" * 60)

    sr = SmartRouter()

    print(f"\n当前状态:")
    print(f"  启用: {sr.enabled}")
    print(f"  学习次数: {sr.learn_count}")
    print(f"  策略数: {len(sr.strategies)}")

    # 执行学习
    print("\n--- 执行学习 ---")
    report = sr.learn()

    print(f"\n学习结果:")
    print(f"  状态: {report.get('status')}")
    print(f"  学习: {report.get('learned')}")
    print(f"  历史数据: {report.get('history_count')} 条")
    print(f"  评测数据: {report.get('eval_count')} 条")
    print(f"  生成策略: {report.get('strategies_generated')} 条")

    if report.get('changes'):
        print(f"\n策略变化 ({len(report['changes'])} 条):")
        for c in report['changes']:
            print(f"  [{c['rule']}] {c['old_target']} → {c['new_target']}")
            print(f"    样本数: {c['data_points']}")

    if report.get('model_scores'):
        print(f"\n模型得分:")
        for rule, scores in report['model_scores'].items():
            print(f"  [{rule}]")
            for model, s in sorted(scores.items(), key=lambda x: x[1]['total'], reverse=True):
                print(f"    {model:30s} 综合={s['total']:5.1f} "
                      f"质量={s['quality']:5.1f} 效率={s['efficiency']:5.1f} "
                      f"成本={s['cost']:5.1f} 稳定={s['stability']:5.1f} "
                      f"请求={s['requests']}")

    print("\n" + "=" * 60)
    print("智能路由引擎就绪")
    print("=" * 60)
