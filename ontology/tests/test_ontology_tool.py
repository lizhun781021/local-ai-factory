#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本体引擎测试（端到端）
运行: python3 tests/test_ontology_tool.py
作者：李准的星小辰 · 2026-09-26
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ontology_tool import (
    ONTOLOGIES, RULES, answer, run_rule, query, classify_intent, ontology_summary,
)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    print("本体引擎测试")
    print("=" * 60)

    # 1. 本体可加载
    print("\n[1] 本体加载与推理")
    for oid, cfg in ONTOLOGIES.items():
        try:
            s = ontology_summary(oid)
            check(f"{oid} 加载推理 OK (三元组={s['triples']})", s["triples"] > 50, s)
        except Exception as e:
            check(f"{oid} 加载失败", False, str(e))

    # 2. 套餐资格判定
    print("\n[2] 套餐资格判定")
    r = run_rule("pkg_fttr_eligibility")
    rows = {row["name"]: row for row in r["rows"]}
    check("张三(199) 可升全光FTTR+智屏", rows.get("张三", {}).get("full") == "可升全光FTTR+智屏", rows)
    check("李四(99) 不可升FTTR", rows.get("李四", {}).get("fttr") == "不可升FTTR", rows)

    # 3. 档位速查
    print("\n[3] 双底座档位速查")
    r = run_rule("pkg_all_tiers")
    fees = sorted(int(row["fee"]) for row in r["rows"])
    check("5个档位齐全", fees == [99, 119, 139, 169, 199], fees)

    # 4. 地市排名（demo）
    print("\n[4] 地市排名")
    r = run_rule("inc_city_rank")
    check("返回18个地市", r["count"] == 18, r["count"])
    check("demo标记正确", r["demo"] is True)

    # 5. 铁律查询
    print("\n[5] 账期铁律")
    r = run_rule("inc_rule")
    labels = [row["rule"] for row in r["rows"]]
    check("含累计-当月差值铁律", "累计-当月差值不变" in labels, labels)

    # 6. 营销话术
    print("\n[6] 营销话术")
    r = run_rule("mkt_objection")
    check("含'没时间'话术", any("微信" in row["reply"] for row in r["rows"]), r["rows"])
    r = run_rule("mkt_fivesteps")
    check("五步法5步", r["count"] == 5, r["count"])

    # 7. 意图分类
    print("\n[7] 意图分类")
    check("FTTR问题→资格规则", [h["id"] for h in classify_intent("张三能升FTTR吗")] == ["pkg_fttr_eligibility"])
    check("开放问题→无命中", classify_intent("鹤壁7月收入同比是多少") == [])
    check("排名→inc_city_rank", "inc_city_rank" in [h["id"] for h in classify_intent("各地市收入排名")])

    # 8. answer 统一入口
    print("\n[8] answer 统一入口")
    a = answer("双底座套餐有哪些档？")
    check("命中且非fallback", a["matched"] and not a["fallback"])
    a = answer("今天天气怎么样")
    check("未命中→fallback", not a["matched"] and a["fallback"] and a["hint"], a)

    print("\n" + "=" * 60)
    print(f"结果: {PASS} 通过, {FAIL} 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())