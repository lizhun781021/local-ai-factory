#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本体引擎 (Ontology Engine)
==========================
为 AI 工厂智能问答模块提供"本体即工具"能力，参考 Palantir Ontology 架构思想：
  显式领域建模（类/属性/关系/规则） + 推理机 + 结构化查询 + LLM 调度

核心链路：加载 TTL → OWL RL 推理 → 匹配规则模板 → SPARQL 查询 → 结构化结果
规则类问题走本体（确定性、可溯源），开放类问题仍走 RAGFlow（在 webui.py 路由）。

作者：李准的星小辰 · 2026-09-26
"""
import os
from functools import lru_cache
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, OWL
from owlrl import DeductiveClosure, OWLRL_Semantics

# ==================== 命名空间 ====================
NS_T = "http://www.example.org/tele#"      # 套餐与资格
NS_I = "http://www.example.org/income#"    # 收入指标
NS_M = "http://www.example.org/mkt#"       # 营销话术
NS_MAP = {"t": NS_T, "i": NS_I, "m": NS_M}

# ==================== 本体注册表 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ONTOLOGIES = {
    "package": {
        "label": "套餐与资格判定",
        "path": os.path.join(BASE_DIR, "ontologies", "package-eligibility.ttl"),
        "ns": NS_T,
        "desc": "双底座融合礼包5档 + 云商宽4档 + FTTR资格判定规则",
    },
    "income": {
        "label": "收入指标体系",
        "path": os.path.join(BASE_DIR, "ontologies", "income-indicators.ttl"),
        "ns": NS_I,
        "desc": "收入指标口径、18地市层级、同比/环比/累计计算规则",
    },
    "marketing": {
        "label": "营销话术体系",
        "path": os.path.join(BASE_DIR, "ontologies", "marketing-scripts.ttl"),
        "ns": NS_M,
        "desc": "客户类型→营销场景→产品→话术 五步法匹配规则",
    },
}

# ==================== 规则模板注册表 ====================
RULES = [
    # ---------- ① 套餐与资格判定 ----------
    {
        "id": "pkg_fttr_eligibility",
        "ontology": "package",
        "keywords": ["fttr", "全光", "资格", "可升", "升级", "升fttr", "升全光"],
        "desc": "FTTR升级资格判定：客户月费>=120可升FTTR，>=199可升全光FTTR+智屏",
        "sparql": """
SELECT ?name ?fee ?fttr ?full WHERE {
  ?c t:hasName ?name . ?c t:hasPackage ?p . ?p t:monthlyFee ?fee .
  BIND(IF(?fee >= 120, "可升FTTR", "不可升FTTR") AS ?fttr)
  BIND(IF(?fee >= 199, "可升全光FTTR+智屏", "—") AS ?full)
}""",
    },
    {
        "id": "pkg_all_tiers",
        "ontology": "package",
        "keywords": ["档位", "套餐", "礼包", "双底座", "资费表", "速查"],
        "desc": "双底座融合礼包全档位核心参数速查",
        "sparql": """
SELECT ?fee ?voice ?data ?broad ?card ?scene WHERE {
  ?p a t:融合套餐 . ?p t:monthlyFee ?fee . ?p t:voiceMin ?voice .
  ?p t:dataGB ?data . ?p t:broadband ?broad . ?p t:familyCard ?card .
  OPTIONAL { ?p t:hasScene ?scene }
} ORDER BY ?fee""",
    },
    {
        "id": "pkg_recommend",
        "ontology": "package",
        "keywords": ["配餐", "推荐", "比算", "月消费"],
        "desc": "双底座融合套餐档位速查（配餐比算用）",
        "sparql": """
SELECT ?fee ?data ?voice ?broad ?card WHERE {
  ?t a t:融合套餐 . ?t t:monthlyFee ?fee . ?t t:dataGB ?data .
  ?t t:voiceMin ?voice . ?t t:broadband ?broad . ?t t:familyCard ?card
} ORDER BY ?fee""",
    },
    {
        "id": "pkg_scene",
        "ontology": "package",
        "keywords": ["场景", "礼包", "权益", "智屏", "视联"],
        "desc": "查询各档位包含的礼包场景/权益",
        "sparql": """
SELECT ?tier ?scene WHERE {
  ?p t:packageTier ?tier . ?p t:hasScene ?scene
} ORDER BY ?tier""",
    },

    # ---------- ② 收入指标体系 ----------
    {
        "id": "inc_city_rank",
        "ontology": "income",
        "keywords": ["排名", "排第", "名次", "同比排名"],
        "demo": True,
        "desc": "地市基础类收入同比排名（按同比降序）【示例数据演示】",
        "sparql": """
SELECT ?city ?val ?yoy WHERE {
  ?c a i:地市 ; i:cityName ?city ; i:基础类收入 ?val ; i:基础类同比 ?yoy
} ORDER BY DESC(?yoy)""",
    },
    {
        "id": "inc_city_detail",
        "ontology": "income",
        "keywords": ["收入构成", "完成情况", "拉动占比"],
        "demo": True,
        "desc": "全省地市基础类收入构成明细【示例数据演示，真实数据请走RAG】",
        "sparql": """
SELECT ?city ?val ?yoy ?share WHERE {
  ?c a i:地市 ; i:cityName ?city ; i:基础类收入 ?val ; i:基础类同比 ?yoy ;
     i:基础类拉动占比 ?share
}""",
    },
    {
        "id": "inc_share",
        "ontology": "income",
        "keywords": ["占比多少", "构成如何", "结构情况"],
        "demo": True,
        "desc": "核心平台收入构成口径（基础类 vs 政企类）【示例数据】",
        "sparql": """
SELECT ?type ?val ?share WHERE {
  ?x a i:指标口径 ; rdfs:label ?type ; i:指标值 ?val ; i:占比 ?share
}""",
    },
    {
        "id": "inc_rule",
        "ontology": "income",
        "keywords": ["口径", "铁律", "账期", "累计", "当月", "自然日", "不回退"],
        "desc": "账期口径铁律（累计-当月差值固定、人数固定、数据不回退）",
        "sparql": """
SELECT ?rule ?content WHERE {
  ?r a i:铁律 ; rdfs:label ?rule ; i:规则内容 ?content
}""",
    },

    # ---------- ③ 营销话术体系 ----------
    {
        "id": "mkt_objection",
        "ontology": "marketing",
        "keywords": ["异议", "没时间", "不需要", "价格贵", "竞品", "拒绝", "怎么回"],
        "desc": "按客户异议类型返回应对话术",
        "sparql": """
SELECT ?type ?reply WHERE {
  ?o a m:异议类型 ; m:typeName ?type ; m:应对话术 ?reply
}""",
    },
    {
        "id": "mkt_customer_product",
        "ontology": "marketing",
        "keywords": ["客户类型", "推荐产品", "配什么", "适合", "存量", "新装", "政企"],
        "desc": "按客户类型匹配主推产品线",
        "sparql": """
SELECT ?cust ?scene ?product WHERE {
  ?c a m:客户类型 ; m:typeName ?cust ; m:营销场景 ?scene ; m:主推产品 ?product
}""",
    },
    {
        "id": "mkt_fivesteps",
        "ontology": "marketing",
        "keywords": ["五步法", "外呼", "服务", "比算", "步骤"],
        "desc": "五步法营销全流程关键动作",
        "sparql": """
SELECT ?step ?action WHERE {
  ?s a m:五步法步骤 ; rdfs:label ?step ; m:关键动作 ?action
} ORDER BY ?step""",
    },
    {
        "id": "mkt_city_diff",
        "ontology": "marketing",
        "keywords": ["地市差异", "531", "考核标准", "岗位名", "橙分期申诉"],
        "desc": "地市差异化配置要点",
        "sparql": """
SELECT ?city ?point WHERE {
  ?d a m:地市差异 ; m:cityName ?city ; m:差异要点 ?point
}""",
    },
]

# ==================== 核心函数 ====================

def load(ontology_id: str) -> Graph:
    """加载本体 TTL"""
    cfg = ONTOLOGIES.get(ontology_id)
    if not cfg:
        raise ValueError(f"未知本体: {ontology_id}，可用: {list(ONTOLOGIES)}")
    g = Graph()
    g.parse(cfg["path"], format="turtle")
    return g


@lru_cache(maxsize=8)
def load_cached(ontology_id: str) -> Graph:
    """加载本体（带缓存）"""
    return load(ontology_id)


def infer(g: Graph) -> Graph:
    """执行 OWL RL 推理，返回推理后的副本（不污染原图）"""
    inferred = Graph()
    for s, p, o in g:
        inferred.add((s, p, o))
    DeductiveClosure(OWLRL_Semantics).expand(inferred)
    return inferred


@lru_cache(maxsize=8)
def get_inferred(ontology_id: str) -> Graph:
    """加载 + 推理一体（带缓存）"""
    return infer(load_cached(ontology_id))


def query(ontology_id: str, sparql: str, init_ns: dict = None) -> list:
    """执行 SPARQL 查询，返回 list[dict]"""
    g = get_inferred(ontology_id)
    ns = {prefix: Namespace(uri) for prefix, uri in NS_MAP.items()}
    if init_ns:
        ns.update(init_ns)
    try:
        results = g.query(sparql, initNs=ns)
    except Exception as e:
        return [{"__error__": str(e)}]
    rows = []
    for row in results:
        d = {}
        for var in results.vars:
            v = row[var]
            if v is None:
                d[str(var)] = None
            elif isinstance(v, Literal):
                d[str(var)] = str(v)
            else:
                d[str(var)] = str(v).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        rows.append(d)
    return rows


def classify_intent(question: str, ontology_id: str = None) -> list:
    """轻量意图分类：按关键词匹配规则"""
    q = question.lower()
    hits = []
    for rule in RULES:
        if ontology_id and rule["ontology"] != ontology_id:
            continue
        if any(k.lower() in q for k in rule["keywords"]):
            hits.append(rule)
    return hits


def run_rule(rule_id: str) -> dict:
    """按规则 ID 执行查询，返回结构化结果"""
    rule = next((r for r in RULES if r["id"] == rule_id), None)
    if not rule:
        return {"ok": False, "error": f"未知规则: {rule_id}"}
    rows = query(rule["ontology"], rule["sparql"])
    return {
        "ok": True,
        "rule_id": rule["id"],
        "ontology": rule["ontology"],
        "ontology_label": ONTOLOGIES[rule["ontology"]]["label"],
        "desc": rule["desc"],
        "demo": rule.get("demo", False),
        "rows": rows,
        "count": len(rows),
    }


def answer(question: str) -> dict:
    """统一入口：对自然语言问题做本体推理回答
    返回 {matched, answers, fallback, hint}
    """
    matched = classify_intent(question)
    if not matched:
        return {
            "matched": False,
            "answers": [],
            "fallback": True,
            "hint": "未命中本体规则，建议走 RAGFlow 向量检索（开放类问题）",
        }
    answers = []
    for rule in matched[:3]:
        res = run_rule(rule["id"])
        if res["ok"] and not any("__error__" in r for r in res["rows"]):
            answers.append(res)
    return {"matched": True, "answers": answers, "fallback": False, "hint": ""}


def ontology_summary(ontology_id: str) -> dict:
    """本体概览：类/属性/实例/三元组统计"""
    g = get_inferred(ontology_id)
    ns = Namespace(ONTOLOGIES[ontology_id]["ns"])
    classes, props, individuals = set(), set(), set()
    for s, p, o in g:
        if p == RDF.type:
            if o == OWL.Class:
                classes.add(s)
            elif o in (OWL.ObjectProperty, OWL.DatatypeProperty):
                props.add(s)
            elif isinstance(s, URIRef) and s.startswith(ns):
                individuals.add(s)
    return {
        "triples": len(g),
        "classes": sorted(str(c).rsplit("#", 1)[-1] for c in classes),
        "properties": sorted(str(p).rsplit("#", 1)[-1] for p in props),
        "individual_count": len(individuals),
        "individuals": sorted(str(i).rsplit("#", 1)[-1] for i in individuals)[:60],
    }


if __name__ == "__main__":
    print("=" * 60)
    print("本体引擎自测")
    print("=" * 60)
    for oid in ONTOLOGIES:
        try:
            s = ontology_summary(oid)
            print(f"\n[{oid}] {ONTOLOGIES[oid]['label']}: 三元组={s['triples']} 类={len(s['classes'])} 属性={len(s['properties'])} 实例={s['individual_count']}")
        except Exception as e:
            print(f"\n[{oid}] 加载失败: {type(e).__name__}: {e}")

    print("\n--- 规则自测 ---")
    tests = [
        "张三能不能升FTTR？",
        "双底座套餐有哪些档？",
        "各地市基础收入同比排名",
        "客户说没时间怎么应对？",
        "今天天气怎么样",
    ]
    for q in tests:
        hits = classify_intent(q)
        print(f"问题: {q} → 命中: {[h['id'] for h in hits]}")