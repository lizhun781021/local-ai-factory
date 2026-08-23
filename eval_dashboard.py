#!/usr/bin/env python3
"""
本地多模型评测平台 - 对比看板
====================================
端口: 8607
框架: Streamlit

页面模块:
  1. 排行榜  — 模型综合排名 + 雷达图 + 分类得分热力图
  2. 评测中心 — 选择模型+题目 → 一键跑分
  3. 题库管理 — 查看所有评测题
  4. 历史趋势 — 多次评测的分数变化趋势
  5. 详细对比 — 逐题对比模型回复内容

启动:
  streamlit run eval_dashboard.py --server.port 8607
"""

import streamlit as st
import requests
import json
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
import time

# ==================== 配置 ====================
ROUTER_API = "http://localhost:8606"
st.set_page_config(
    page_title="多模型评测平台",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义样式
st.markdown("""
<style>
    .main { padding-top: 1rem; }
    .stMetric { background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 4px solid #0066cc; }
    .rank-1 { background: linear-gradient(90deg, #ffd70022, transparent); padding: 10px; border-radius: 8px; border-left: 4px solid #ffd700; }
    .rank-2 { background: linear-gradient(90deg, #c0c0c022, transparent); padding: 10px; border-radius: 8px; border-left: 4px solid #c0c0c0; }
    .rank-3 { background: linear-gradient(90deg, #cd7f3222, transparent); padding: 10px; border-radius: 8px; border-left: 4px solid #cd7f32; }
</style>
""", unsafe_allow_html=True)


# ==================== API 工具函数 ====================
# macOS 系统代理会劫持 localhost 请求（经 SystemConfiguration 读取）
# 用独立 Session + trust_env=False 彻底绕过
_session = requests.Session()
_session.trust_env = False  # 不读系统代理/环境变量

def api_get(path):
    try:
        resp = _session.get(f"http://127.0.0.1:8606{path}", timeout=15)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        print(f"[api_get ERROR] {path}: {e}", flush=True)
        return None

def api_post(path, data, timeout=600):
    try:
        resp = _session.post(f"http://127.0.0.1:8606{path}", json=data, timeout=timeout)
        return resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as e:
        return {"error": str(e)}


# ==================== 侧边栏 ====================
st.sidebar.title("📊 多模型评测平台")
st.sidebar.caption(f"路由服务: {ROUTER_API}")

# 服务健康检查
health = api_get("/health")
if health:
    st.sidebar.success("✅ 路由服务在线")
else:
    st.sidebar.error("❌ 路由服务离线")

page = st.sidebar.radio("功能模块", [
    "🏆 排行榜",
    "🔬 评测中心",
    "📚 题库管理",
    "📈 历史趋势",
    "📝 详细对比",
    "🧠 智能路由",
    "⚙️ 模型管理",
])

st.sidebar.divider()
st.sidebar.caption("v3.0 | 本地多模型评测+智能路由平台")


# ==================== 页面1: 排行榜 ====================
if page == "🏆 排行榜":
    st.title("🏆 模型排行榜")

    # 获取历史会话列表
    sessions_data = api_get("/eval/sessions")
    sessions = sessions_data.get("sessions", []) if sessions_data else []

    if not sessions:
        st.warning("暂无评测数据，请先到「评测中心」运行一次评测")
        st.stop()

    # 选择会话
    session_options = {s['session_id']: f"{s['session_id']} ({len(s['models'])}模型, {s['duration']}s)" for s in sessions}
    selected_sid = st.selectbox("选择评测会话", list(session_options.keys()),
                                format_func=lambda x: session_options[x])

    # 获取排行数据
    board = api_get(f"/eval/leaderboard?session_id={selected_sid}")
    if not board or 'ranking' not in board:
        st.error("获取排行榜失败")
        st.stop()

    ranking = board['ranking']
    weights = board.get('weights', {})

    # ---- 顶部指标 ----
    if ranking:
        top = ranking[0]
        cols = st.columns(4)
        cols[0].metric("冠军模型", top['model'][:20], f"综合 {top['total_avg']:.1f}")
        cols[1].metric("评测题数", top['question_count'])
        cols[2].metric("最高质量分", f"{max(r['quality_avg'] for r in ranking):.1f}")
        cols[3].metric("最快速度分", f"{max(r['speed_avg'] for r in ranking):.1f}")

    st.divider()

    # ---- 排行榜表格 ----
    col1, col2 = st.columns([3, 2])

    with col1:
        st.subheader("综合排名")
        for i, r in enumerate(ranking):
            rank_class = f"rank-{i+1}" if i < 3 else ""
            medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"#{i+1}"

            st.markdown(f"""
            <div class="{rank_class}">
                <h4>{medal} {r['model']} &nbsp;&nbsp; 综合 <b>{r['total_avg']:.1f}</b> 分</h4>
                <table style="width:100%">
                    <tr>
                        <td>质量分: <b>{r['quality_avg']:.1f}</b></td>
                        <td>速度分: <b>{r['speed_avg']:.1f}</b></td>
                        <td>成本分: <b>{r['cost_avg']:.1f}</b></td>
                        <td>平均延迟: <b>{r['avg_latency']:.1f}s</b></td>
                        <td>成功率: <b>{r['success_rate']:.0f}%</b></td>
                    </tr>
                </table>
            </div>
            """, unsafe_allow_html=True)
            st.caption("")

    # ---- 雷达图 ----
    with col2:
        st.subheader("能力雷达图")
        if len(ranking) >= 1:
            categories = ['质量', '速度', '成本']
            fig = go.Figure()
            colors = px.colors.qualitative.Set1
            for i, r in enumerate(ranking):
                fig.add_trace(go.Scatterpolar(
                    r=[r['quality_avg'], r['speed_avg'], r['cost_avg'], r['quality_avg']],
                    theta=categories + [categories[0]],
                    fill='toself',
                    name=r['model'][:20],
                    line_color=colors[i % len(colors)],
                    opacity=0.6,
                ))
            fig.update_layout(
                polar=dict(radialaxis=dict(range=[0, 100])),
                showlegend=True,
                height=350,
                margin=dict(l=40, r=40, t=30, b=30),
            )
            st.plotly_chart(fig, use_container_width=True)

    # ---- 分类得分热力图 ----
    st.subheader("分类能力热力图")
    all_categories = set()
    for r in ranking:
        all_categories.update(r.get('by_category', {}).keys())
    all_categories = sorted(all_categories)

    if all_categories:
        heat_data = []
        heat_labels = []
        for r in ranking:
            row = [r.get('by_category', {}).get(cat, 0) for cat in all_categories]
            heat_data.append(row)
            heat_labels.append(r['model'][:20])

        fig = go.Figure(data=go.Heatmap(
            z=heat_data,
            x=all_categories,
            y=heat_labels,
            colorscale='RdYlGn',
            text=[[f"{v:.0f}" for v in row] for row in heat_data],
            texttemplate="%{text}",
            textfont={"size": 14},
            zmin=0, zmax=100,
        ))
        fig.update_layout(height=max(250, len(ranking) * 80 + 100), margin=dict(l=20, r=20, t=20, b=60))
        st.plotly_chart(fig, use_container_width=True)

    # ---- 分数柱状图 ----
    col3, col4 = st.columns(2)
    with col3:
        st.subheader("三维分数对比")
        fig = go.Figure()
        for score_type, color in [('quality_avg', '#3498db'), ('speed_avg', '#2ecc71'), ('cost_avg', '#f39c12')]:
            fig.add_trace(go.Bar(
                name={'quality_avg': '质量', 'speed_avg': '速度', 'cost_avg': '成本'}[score_type],
                x=[r['model'][:15] for r in ranking],
                y=[r[score_type] for r in ranking],
                text=[f"{r[score_type]:.0f}" for r in ranking],
                textposition='inside',
                marker_color=color,
            ))
        fig.update_layout(barmode='group', yaxis=dict(range=[0, 100]), height=350)
        st.plotly_chart(fig, use_container_width=True)

    with col4:
        st.subheader("响应延迟对比")
        fig = go.Bar(
            x=[r['model'][:15] for r in ranking],
            y=[r['avg_latency'] for r in ranking],
            text=[f"{r['avg_latency']:.1f}s" for r in ranking],
            textposition='inside',
            marker_color='#e74c3c',
        )
        fig = go.Figure(data=[fig])
        fig.update_layout(yaxis_title="秒", height=350, margin=dict(l=40, r=20, t=20, b=60))
        st.plotly_chart(fig, use_container_width=True)

    # 评分权重说明
    with st.expander("评分权重说明"):
        st.write(f"- 综合分 = 质量分×{weights.get('quality',0.6)*100:.0f}% + 速度分×{weights.get('speed',0.3)*100:.0f}% + 成本分×{weights.get('cost',0.1)*100:.0f}%")
        st.write("- 质量分: 关键词命中 + 格式正确 + 长度合规 + 代码可运行(0-100)")
        st.write("- 速度分: 按tokens/s分档，≥30tok/s=100, ≥20=80, ≥10=60, ≥5=40(0-100)")
        st.write("- 成本分: free=100, local=80, low=60, medium=30, high=10")


# ==================== 页面2: 评测中心 ====================
elif page == "🔬 评测中心":
    st.title("🔬 评测中心")

    # 获取模型列表
    raw = api_get("/admin/models")
    if not raw:
        st.error("无法获取模型列表，请检查路由服务")
        st.stop()

    models_data = raw.get('models', raw)  # 兼容 {models:{...}} 和直接 dict
    online_models = [name for name, info in models_data.items() if info.get('healthy')]
    offline_models = [name for name, info in models_data.items() if not info.get('healthy')]

    col1, col2 = st.columns([2, 3])

    with col1:
        st.subheader("选择评测模型")
        selected_models = st.multiselect(
            "勾选要评测的模型",
            options=list(models_data.keys()),
            default=online_models,
            format_func=lambda x: f"{'✅' if models_data[x].get('healthy') else '❌'} {x}"
        )

        if offline_models:
            st.caption(f"⚠️ 离线模型: {', '.join(offline_models)}（将记为0分）")

    with col2:
        st.subheader("选择评测题目")
        dataset = api_get("/eval/dataset")
        if dataset:
            # 按分类筛选
            categories = ['全部'] + sorted(dataset['categories'].keys())
            selected_cat = st.selectbox("按分类筛选", categories)

            questions = dataset['questions']
            if selected_cat != '全部':
                questions = [q for q in questions if q['category'] == selected_cat]

            # 难度筛选
            difficulties = ['全部', 'easy', 'medium', 'hard']
            selected_diff = st.selectbox("按难度筛选", difficulties)
            if selected_diff != '全部':
                questions = [q for q in questions if q['difficulty'] == selected_diff]

            question_ids = st.multiselect(
                f"选择题目（共{len(questions)}道）",
                options=[q['id'] for q in questions],
                default=[q['id'] for q in questions][:4],  # 默认选前4题
                format_func=lambda qid: next((f"[{q['category']}] {q['prompt'][:40]}..." for q in questions if q['id'] == qid), qid)
            )

    st.divider()

    # 评测参数
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        timeout = st.slider("单题超时(秒)", 60, 300, 120, 10)
    with col_b:
        if st.button("🚀 开始评测", type="primary", disabled=not selected_models or not question_ids):
            st.session_state['eval_running'] = True
            st.session_state['eval_params'] = {
                'models': selected_models,
                'question_ids': question_ids,
                'timeout': timeout,
            }

    with col_c:
        total_rounds = len(selected_models) * len(question_ids)
        est_time = total_rounds * 20  # 粗估每轮20秒
        st.metric("预计耗时", f"~{est_time//60}分{est_time%60}秒", f"{total_rounds}轮")

    # 执行评测
    if st.session_state.get('eval_running'):
        params = st.session_state['eval_params']

        with st.spinner(f"评测中... {len(params['models'])}个模型 × {len(params['question_ids'])}道题"):
            progress = st.progress(0, text="正在评测...")
            result = api_post("/eval/run", params, timeout=params['timeout'] * total_rounds + 60)

            if 'error' in result:
                st.error(f"评测失败: {result['error']}")
            else:
                progress.progress(100, text="评测完成！")
                st.session_state['eval_running'] = False
                st.session_state['last_eval_result'] = result

                st.success(f"评测完成！耗时 {result['duration']}s")

                # 显示排行榜
                board = result.get('leaderboard', {})
                ranking = board.get('ranking', [])
                if ranking:
                    st.subheader("🏆 本次评测排行榜")
                    df = pd.DataFrame(ranking)
                    display_cols = ['model', 'quality_avg', 'speed_avg', 'cost_avg', 'total_avg', 'avg_latency', 'success_rate']
                    df_display = df[display_cols].copy()
                    df_display.columns = ['模型', '质量分', '速度分', '成本分', '综合分', '平均延迟(s)', '成功率(%)']
                    df_display = df_display.sort_values('综合分', ascending=False)
                    st.dataframe(df_display, use_container_width=True, hide_index=True)


# ==================== 页面3: 题库管理 ====================
elif page == "📚 题库管理":
    st.title("📚 题库管理")

    dataset = api_get("/eval/dataset")
    if not dataset:
        st.error("无法获取题库")
        st.stop()

    # 统计概览
    cols = st.columns(4)
    cols[0].metric("总题数", dataset['total'])
    cols[1].metric("分类数", len(dataset['categories']))
    cols[2].metric("代码题", dataset['categories'].get('代码生成', 0))
    cols[3].metric("工作场景题", sum(v for k, v in dataset['categories'].items() if k in ('电信业务', '公文写作', '工具使用')))

    st.divider()

    # 按分类展示
    st.subheader("题目列表")
    selected_cat = st.selectbox("筛选分类", ['全部'] + sorted(dataset['categories'].keys()))

    questions = dataset['questions']
    if selected_cat != '全部':
        questions = [q for q in questions if q['category'] == selected_cat]

    for q in questions:
        with st.expander(f"[{q['category']}] {q['difficulty'].upper()} | {q['id']} — {q['prompt'][:50]}..."):
            st.write(f"**题目**: {q['prompt']}")
            st.write(f"**分类**: {q['category']} | **难度**: {q['difficulty']} | **最大tokens**: {q['max_tokens']} | **评判类型**: {q['judge_type']}")


# ==================== 页面4: 历史趋势 ====================
elif page == "📈 历史趋势":
    st.title("📈 历史评测趋势")

    sessions_data = api_get("/eval/sessions")
    sessions = sessions_data.get("sessions", []) if sessions_data else []

    if len(sessions) < 2:
        st.info("需要至少2次评测才能显示趋势。当前评测次数不足，请多跑几次评测。")
        if sessions:
            st.write(f"当前评测次数: {len(sessions)}")
        st.stop()

    # 收集所有评测数据
    all_results = []
    for s in sessions:
        detail = api_get(f"/eval/sessions/{s['session_id']}")
        if detail and 'results' in detail:
            for r in detail['results']:
                r['session_id'] = s['session_id']
                r['timestamp'] = s['timestamp']
                all_results.append(r)

    if not all_results:
        st.warning("无法获取评测明细数据")
        st.stop()

    df = pd.DataFrame(all_results)

    # ---- 综合分趋势 ----
    st.subheader("综合分趋势")
    trend = df.groupby(['session_id', 'model'])['total_score'].mean().reset_index()
    fig = px.line(trend, x='session_id', y='total_score', color='model',
                  markers=True, title="各模型综合分变化趋势")
    fig.update_layout(yaxis=dict(range=[0, 100]), height=350)
    st.plotly_chart(fig, use_container_width=True)

    # ---- 质量分趋势 ----
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("质量分趋势")
        trend_q = df.groupby(['session_id', 'model'])['quality_score'].mean().reset_index()
        fig = px.line(trend_q, x='session_id', y='quality_score', color='model',
                      markers=True, title="质量分变化")
        fig.update_layout(yaxis=dict(range=[0, 100]), height=300)
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("速度分趋势")
        trend_s = df.groupby(['session_id', 'model'])['speed_score'].mean().reset_index()
        fig = px.line(trend_s, x='session_id', y='speed_score', color='model',
                      markers=True, title="速度分变化")
        fig.update_layout(yaxis=dict(range=[0, 100]), height=300)
        st.plotly_chart(fig, use_container_width=True)

    # ---- 评测历史列表 ----
    st.subheader("评测历史")
    hist_df = pd.DataFrame([{
        '会话ID': s['session_id'],
        '时间': s['timestamp'][:19],
        '模型': ', '.join(s['models']),
        '题数': s['question_count'],
        '耗时(s)': s['duration'],
    } for s in sessions])
    st.dataframe(hist_df, use_container_width=True, hide_index=True)


# ==================== 页面5: 详细对比 ====================
elif page == "📝 详细对比":
    st.title("📝 逐题详细对比")

    sessions_data = api_get("/eval/sessions")
    sessions = sessions_data.get("sessions", []) if sessions_data else []

    if not sessions:
        st.info("暂无评测数据")
        st.stop()

    # 选择会话
    session_options = {s['session_id']: f"{s['session_id']} ({len(s['models'])}模型)" for s in sessions}
    selected_sid = st.selectbox("选择评测会话", list(session_options.keys()),
                                format_func=lambda x: session_options[x])

    detail = api_get(f"/eval/sessions/{selected_sid}")
    if not detail or 'results' not in detail:
        st.error("获取评测详情失败")
        st.stop()

    results = detail['results']

    # 获取题目信息
    dataset = api_get("/eval/dataset")
    q_map = {q['id']: q for q in dataset['questions']} if dataset else {}

    # 按题目分组
    question_ids = list(set(r['question_id'] for r in results))

    for qid in question_ids:
        q_info = q_map.get(qid, {})
        q_results = [r for r in results if r['question_id'] == qid]
        q_results.sort(key=lambda x: x['total_score'], reverse=True)

        prompt_preview = q_info.get('prompt', qid)[:60]
        with st.expander(f"[{q_info.get('category','')}] {qid}: {prompt_preview}... ({len(q_results)}个模型)"):
            st.write(f"**题目**: {q_info.get('prompt', 'N/A')}")
            st.write(f"**难度**: {q_info.get('difficulty','')} | **评判**: {q_info.get('judge_type','')}")

            # 分数表格
            df = pd.DataFrame([{
                '模型': r['model'],
                '质量分': r['quality_score'],
                '速度分': r['speed_score'],
                '综合分': r['total_score'],
                '延迟(s)': r['latency'],
                'tokens': r['output_tokens'],
                '错误': r.get('error', ''),
            } for r in q_results])
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 逐模型回复
            st.write("**模型回复对比**:")
            for r in q_results:
                medal = "🥇" if r == q_results[0] else ""
                st.write(f"---")
                st.write(f"{medal} **{r['model']}** (综合 {r['total_score']:.1f})")
                if r.get('response'):
                    st.text(r['response'][:500] + ('...' if len(r['response']) > 500 else ''))
                else:
                    st.error(f"无回复: {r.get('error','')}")


# ==================== 页面6: 智能路由 ====================
elif page == "🧠 智能路由":
    st.title("🧠 智能路由管理")
    st.caption("基于历史路由统计 + 评测结果，自动优化各规则的目标模型与降级链")

    # 获取状态
    status = api_get("/smart/status")
    if not status:
        st.error("无法获取智能路由状态，请检查路由服务")
        st.stop()

    # ---- 顶部状态卡片 ----
    enabled = status.get('enabled', False)
    cols = st.columns(5)
    cols[0].metric("运行状态", "🟢 启用" if enabled else "🔴 禁用")
    cols[1].metric("策略数", status.get('strategy_count', 0))
    cols[2].metric("已优化规则", status.get('changed_count', 0))
    cols[3].metric("学习次数", status.get('learn_count', 0))
    last_learn = status.get('last_learn_time', '—')
    cols[4].metric("上次学习", last_learn[5:16] if last_learn and last_learn != '—' else '—')

    st.divider()

    # ---- 控制按钮 ----
    col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 3])
    with col_btn1:
        if enabled:
            if st.button("⏸️ 禁用智能路由", type="secondary"):
                r = api_post("/smart/disable", {})
                if r.get('status') == 'ok':
                    st.success("已禁用，回退静态规则")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error(r.get('message', '操作失败'))
        else:
            if st.button("▶️ 启用智能路由", type="primary"):
                r = api_post("/smart/enable", {})
                if r.get('status') == 'ok':
                    st.success("已启用智能路由")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error(r.get('message', '操作失败'))

    with col_btn2:
        if st.button("🔄 重新学习"):
            with st.spinner("学习历史数据中..."):
                r = api_post("/smart/learn", {})
            if r.get('status') == 'ok':
                changes = r.get('changes', [])
                st.success(f"学习完成！生成 {r.get('strategies_generated', 0)} 条策略，{len(changes)} 条变化")
                time.sleep(1)
                st.rerun()
            else:
                st.error(r.get('error', '学习失败'))

    with col_btn3:
        if enabled:
            st.info("💡 智能路由已启用，非显式规则将使用优化后的目标模型与降级链")
        else:
            st.warning("⚠️ 智能路由已禁用，当前使用静态规则路由")

    st.divider()

    # ---- 策略总览 ----
    strategies = status.get('strategies', {})
    if not strategies:
        st.info("暂无策略，请点击「重新学习」生成优化策略")
        st.stop()

    st.subheader("策略总览")

    # 构建策略表格
    strat_rows = []
    for rule_name, s in strategies.items():
        orig_target = s.get('original_target', '—') or '—'
        opt_target = s.get('optimized_target', '—') or '—'
        orig_fallback = ' → '.join(s.get('original_fallback', [])) or '—'
        opt_fallback = ' → '.join(s.get('optimized_fallback', [])) or '—'
        changed = s.get('changed', False)
        data_points = s.get('data_points', 0)
        strat_rows.append({
            '规则': rule_name,
            '原始目标': orig_target,
            '优化目标': opt_target,
            '原始降级链': orig_fallback,
            '优化降级链': opt_fallback,
            '已优化': '✅' if changed else '—',
            '数据样本': data_points,
        })

    df_strat = pd.DataFrame(strat_rows)
    st.dataframe(df_strat, use_container_width=True, hide_index=True)

    st.divider()

    # ---- 逐规则模型评分详情 ----
    st.subheader("模型评分详情（4维度评分）")

    rules_with_scores = [(name, s) for name, s in strategies.items() if s.get('model_scores')]
    if not rules_with_scores:
        st.info("暂无模型评分数据，请多运行评测和产生路由历史后再学习")

    for rule_name, s in rules_with_scores:
        scores = s.get('model_scores', {})
        with st.expander(f"📊 {rule_name} — {len(scores)}个模型评分 | 数据样本 {s.get('data_points', 0)}"):
            score_rows = []
            for model, sc in scores.items():
                if isinstance(sc, dict):
                    sr_val = sc.get('success_rate', 0)
                    if not isinstance(sr_val, (int, float)):
                        sr_val = 0
                    score_rows.append({
                        '模型': model,
                        '综合分': sc.get('total', 0),
                        '质量分': sc.get('quality', 0),
                        '效率分': sc.get('efficiency', 0),
                        '成本分': sc.get('cost', 0),
                        '稳定性分': sc.get('stability', 0),
                        '请求数': sc.get('requests', 0),
                        '成功率(%)': sr_val,
                    })
            if score_rows:
                df_scores = pd.DataFrame(score_rows)
                df_scores = df_scores.sort_values('综合分', ascending=False)
                st.dataframe(df_scores, use_container_width=True, hide_index=True)

                # 雷达图对比
                if len(df_scores) >= 1:
                    categories = ['质量', '效率', '成本', '稳定性']
                    fig = go.Figure()
                    colors = px.colors.qualitative.Set1
                    for i, (_, row) in enumerate(df_scores.iterrows()):
                        fig.add_trace(go.Scatterpolar(
                            r=[row['质量分'], row['效率分'], row['成本分'], row['稳定性分'], row['质量分']],
                            theta=categories + [categories[0]],
                            fill='toself',
                            name=row['模型'][:20],
                            line_color=colors[i % len(colors)],
                            opacity=0.6,
                        ))
                    fig.update_layout(
                        polar=dict(radialaxis=dict(range=[0, 100])),
                        showlegend=True,
                        height=300,
                        margin=dict(l=40, r=40, t=30, b=30),
                    )
                    st.plotly_chart(fig, use_container_width=True)

    # ---- 评分公式说明 ----
    with st.expander("📖 评分公式说明"):
        st.write("**综合评分 S = 质量分×0.4 + 效率分×0.3 + 成本分×0.2 + 稳定性分×0.1**")
        st.write("- **质量分**: 来自评测结果（关键词命中+格式+长度+代码可运行），0-100")
        st.write("- **效率分**: 来自历史路由的 tokens/s 分档，0-100")
        st.write("- **成本分**: 来自模型配置 free=100/local=80/low=60/medium=30/high=10")
        st.write("- **稳定性分**: 来自历史路由成功率，0-100")
        st.write("- **冷启动**: 数据不足（<3样本）时回退静态规则，避免误优化")
        st.write("- **降级链优化**: 离线/无数据的模型排到降级链后部，有成功记录的模型提前")


# ==================== 页面7: 模型管理 ====================
elif page == "⚙️ 模型管理":
    st.title("⚙️ 模型生命周期管理")
    st.caption("按需启动 + 空闲自动卸载，路由平台统一接管本地模型生命周期")

    # 获取生命周期状态
    lc_status = api_get("/lifecycle/status")
    lc_config = api_get("/lifecycle/config")

    if not lc_status:
        st.error("无法获取生命周期状态，请检查路由服务")
        st.stop()

    lc_enabled = lc_status.get('lifecycle_enabled', False)
    daemon_alive = lc_status.get('daemon_alive', False)

    # ---- 顶部配置卡片 ----
    cols = st.columns(5)
    state_label = "🟢 启用" if lc_enabled else "🔴 禁用"
    if lc_enabled and not daemon_alive:
        state_label = "🟠 守护线程异常"
    cols[0].metric("生命周期管理", state_label)
    cols[1].metric("守护线程", "🟢 运行中" if daemon_alive else "🔴 未运行")
    cols[2].metric("扫描间隔", f"{lc_status.get('idle_check_interval', 60)}s")
    cols[3].metric("默认空闲超时", f"{lc_status.get('default_idle_timeout', 600)}s")
    cols[4].metric("启动宽限期", f"{lc_status.get('startup_grace', 300)}s")

    st.divider()

    # 状态图标映射
    state_map = {
        "running": ("🟢", "运行中"),
        "loading": ("🟡", "装载中(宽限期)"),
        "starting": ("🔄", "拉起中"),
        "stopped": ("⚪", "已停止"),
        "idle-warning": ("🟠", "空闲告警"),
        "remote": ("🔵", "远程(不管)"),
    }

    # ---- 模型卡片列表 ----
    st.subheader("模型状态与控制")
    models_status = lc_status.get('models', {})

    for name, info in models_status.items():
        state = info.get('state', 'unknown')
        icon, state_text = state_map.get(state, ("❓", state))
        healthy = info.get('healthy', False)
        managed = info.get('managed', False)
        enabled = info.get('enabled', True)
        auto_start = info.get('auto_start', False)
        idle_timeout = info.get('idle_timeout', 0)
        idle_sec = info.get('idle_seconds', -1)

        with st.container():
            col_n, col_s, col_c, col_o = st.columns([3, 2, 2, 2])

            with col_n:
                st.write(f"### {icon} {name}")
                st.caption(f"💡 {info.get('description', '')}")
                st.caption(f"📋 {info.get('cost','')} | {info.get('speed','')} | {info.get('quality','')}")

            with col_s:
                st.write(f"**状态**: {state_text}")
                st.write(f"**健康**: {'✅ 在线' if healthy else '❌ 离线'}")
                if managed and idle_timeout > 0:
                    if idle_sec >= 0:
                        ratio = idle_sec / idle_timeout if idle_timeout else 0
                        st.write(f"**空闲**: {idle_sec}s / {idle_timeout}s")
                        st.progress(min(ratio, 1.0))
                    else:
                        st.write(f"**空闲**: 从未活跃")
                elif managed:
                    st.write("**超时**: 常驻不卸载")

            with col_c:
                st.write(f"**启用**: {'✅' if enabled else '❌'}")
                st.write(f"**自动拉起**: {'✅' if auto_start else '❌'}")
                st.write(f"**纳入管理**: {'✅' if managed else '—'}")

            with col_o:
                # 启动/卸载按钮
                btn_cols = st.columns(2)
                with btn_cols[0]:
                    if st.button("🚀 拉起", key=f"start_{name}",
                                 disabled=(not info.get('startup_cmd')) or state in ('running','loading','starting','remote')):
                        r = api_post(f"/lifecycle/start/{name}", {})
                        if r and r.get('status') == 'ok':
                            st.success(r.get('message', '已拉起'))
                            time.sleep(1)
                            st.rerun()
                        elif r and r.get('status') == 'skip':
                            st.info(r.get('message', '无需拉起'))
                        else:
                            st.error(r.get('detail', '拉起失败') if r else '请求失败')
                with btn_cols[1]:
                    if st.button("🛑 卸载", key=f"stop_{name}",
                                 disabled=(not info.get('shutdown_cmd')) or state in ('stopped','remote')):
                        r = api_post(f"/lifecycle/stop/{name}", {})
                        if r and r.get('status') == 'ok':
                            st.success(r.get('message', '已卸载'))
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(r.get('detail', '卸载失败') if r else '请求失败')

            # 命令展示
            with st.expander(f"命令配置 — {name}", expanded=False):
                st.write(f"**启动命令**: `{info.get('startup_cmd') or '（无）'}`")
                st.write(f"**卸载命令**: `{info.get('shutdown_cmd') or '（无）'}`")
                st.write(f"**空闲超时**: {idle_timeout}s（0=常驻不卸载）")
                st.write(f"**最近拉起**: {info.get('started_at') or '—'}")
                st.write(f"**最近活跃**: {info.get('last_active') or '—'}")
                st.write(f"**最近卸载**: {info.get('last_unload') or '—'}")

            st.divider()

    # ---- 工作机制说明 ----
    with st.expander("📖 生命周期工作机制"):
        st.write("**按需启动（后台预热）**")
        st.write("- 请求命中离线模型且 `auto_start=true` → 后台异步拉起，本次走降级链立即返回")
        st.write("- 模型加载需要时间（MLX 约1-3分钟），首次请求用降级模型，下次即可命中目标")
        st.write("- 拉起后进入宽限期（默认300s），期间不判定空闲，等模型装载稳定")
        st.write("")
        st.write("**空闲自动卸载**")
        st.write("- 守护线程每 60s 扫描一次，模型空闲超过 `idle_timeout` 自动卸载释放内存")
        st.write("- 远程 vLLM 不占本地资源，不纳入管理")
        st.write("- Ollama 服务常驻，仅卸载模型释放显存/内存（`ollama stop`）")
        st.write("- MLX 模型卸载释放约 38GB 内存（`pkill mlx_lm.server`）")
        st.write("")
        st.write("**配置位置**: `router_config.yaml` 的 `lifecycle` 段和每个模型的 `enabled/auto_start/idle_timeout/startup_cmd/shutdown_cmd` 字段")
        st.write("**取代了** `ai-idle-watchdog.py`，路由平台统一接管")

    # ============================================================
    # 外部模型配置区域
    # ============================================================
    st.divider()
    st.subheader("🔗 外部模型配置")
    st.caption("随时添加 / 修改 / 删除外部 API 模型，配置即时生效并持久化到 router_config.yaml")

    # ---- 添加新模型表单 ----
    with st.expander("➕ 添加外部模型", expanded=False):
        with st.form("add_model_form"):
            col1, col2 = st.columns(2)
            with col1:
                m_name = st.text_input("模型名称（唯一标识）", placeholder="例: gpt-4o-remote")
                m_base_url = st.text_input("API 地址 (base_url)", placeholder="https://api.openai.com/v1")
                m_model_name = st.text_input("后端模型名 (model_name)", placeholder="gpt-4o")
                m_api_key = st.text_input("API Key", type="password", placeholder="sk-...")
                m_api_type = st.selectbox("API 类型", ["openai", "ollama"])
            with col2:
                m_priority = st.number_input("优先级（数字越小越优先）", min_value=1, max_value=99, value=10)
                m_max_tokens = st.number_input("最大输出 tokens", min_value=512, max_value=32768, value=4096, step=512)
                m_context = st.number_input("上下文窗口", min_value=2048, max_value=200000, value=32768, step=2048)
                m_speed = st.selectbox("速度", ["fast", "medium", "slow", "fastest"])
                m_quality = st.selectbox("质量", ["highest", "high", "medium", "low"])

            m_desc = st.text_input("描述", placeholder="例: GPT-4o 远程API，质量最高")
            m_tags = st.text_input("标签（逗号分隔）", placeholder="default, code, reasoning")
            m_cost = st.selectbox("类型", ["free", "local"], help="free=远程API不占本地资源, local=本地模型")

            submitted = st.form_submit_button("✅ 添加模型", type="primary")
            if submitted:
                if not m_name or not m_base_url:
                    st.error("模型名称和 API 地址为必填项")
                else:
                    config = {
                        "api_type": m_api_type,
                        "base_url": m_base_url,
                        "model_name": m_model_name or m_name,
                        "api_key": m_api_key,
                        "priority": m_priority,
                        "max_tokens": m_max_tokens,
                        "context_window": m_context,
                        "tags": [t.strip() for t in m_tags.split(",") if t.strip()] if m_tags else [],
                        "speed": m_speed,
                        "quality": m_quality,
                        "cost": m_cost,
                        "description": m_desc,
                        "enabled": True,
                    }
                    r = _session.post(f"http://127.0.0.1:8606/models?name={m_name}", json=config, timeout=10)
                    if r.status_code == 200:
                        m_info = api_get("/admin/models")
                        m_count = len(m_info.get('models', {})) if m_info else 0
                        st.success(f"✅ {m_name} 添加成功！当前共 {m_count} 个模型")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"添加失败: {r.json().get('detail', '未知错误')}")

    # ---- 已配置模型列表（远程/外部模型可编辑删除）----
    all_models = api_get("/admin/models")
    if all_models and all_models.get('models'):
        st.write("**已配置模型列表**")
        # 筛选外部模型（远程API，cost=free 且有 base_url）
        models_dict = all_models['models']
        remote_models = [v for v in models_dict.values() if v.get('cost') == 'free']
        if remote_models:
            for m in remote_models:
                mname = m.get('name', '')
                with st.container():
                    col_n, col_d, col_a = st.columns([3, 3, 1])
                    with col_n:
                        healthy_icon = "✅" if m.get('healthy') else "❌"
                        st.write(f"**{healthy_icon} {mname}**")
                        st.caption(f"📋 {m.get('description','')}")
                    with col_d:
                        st.write(f"**地址**: `{m.get('base_url','')}`")
                        st.write(f"**模型**: `{m.get('model_name','')}` | 优先级: {m.get('priority','')} | {m.get('speed','')}/{m.get('quality','')}")
                    with col_a:
                        # 删除按钮
                        if st.button("🗑️ 删除", key=f"del_{mname}"):
                            r = _session.delete(f"http://127.0.0.1:8606/models/{mname}", timeout=10)
                            if r.status_code == 200:
                                st.success(f"{mname} 已删除")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error(f"删除失败: {r.json().get('detail', '')}")

                    # 编辑展开
                    with st.expander(f"编辑 {mname}", expanded=False):
                        with st.form(f"edit_{mname}"):
                            e1, e2 = st.columns(2)
                            with e1:
                                e_url = st.text_input("API 地址", value=m.get('base_url',''), key=f"url_{mname}")
                                e_model = st.text_input("模型名", value=m.get('model_name',''), key=f"mn_{mname}")
                                e_key = st.text_input("API Key", value=m.get('api_key',''), type="password", key=f"key_{mname}")
                                e_priority = st.number_input("优先级", min_value=1, max_value=99, value=m.get('priority',10), key=f"pri_{mname}")
                            with e2:
                                e_max = st.number_input("最大tokens", min_value=512, max_value=32768, value=m.get('max_tokens',4096), step=512, key=f"max_{mname}")
                                e_speed = st.selectbox("速度", ["fast","medium","slow","fastest"], index=["fast","medium","slow","fastest"].index(m.get('speed','medium')) if m.get('speed') in ["fast","medium","slow","fastest"] else 1, key=f"sp_{mname}")
                                e_quality = st.selectbox("质量", ["highest","high","medium","low"], index=["highest","high","medium","low"].index(m.get('quality','medium')) if m.get('quality') in ["highest","high","medium","low"] else 2, key=f"qu_{mname}")
                                e_enabled = st.checkbox("启用", value=m.get('enabled', True), key=f"en_{mname}")

                            e_desc = st.text_input("描述", value=m.get('description',''), key=f"desc_{mname}")

                            if st.form_submit_button("💾 保存修改"):
                                config = {
                                    "api_type": m.get('api_type','openai'),
                                    "base_url": e_url,
                                    "model_name": e_model,
                                    "api_key": e_key,
                                    "priority": e_priority,
                                    "max_tokens": e_max,
                                    "context_window": m.get('context_window',8192),
                                    "tags": m.get('tags',[]),
                                    "speed": e_speed,
                                    "quality": e_quality,
                                    "cost": m.get('cost','free'),
                                    "description": e_desc,
                                    "enabled": e_enabled,
                                }
                                r = _session.put(f"http://127.0.0.1:8606/models/{mname}", json=config, timeout=10)
                                if r.status_code == 200:
                                    st.success("修改成功！")
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error(f"修改失败: {r.json().get('detail','')}")
                    st.divider()
        else:
            st.info("暂无外部模型配置，点击上方「➕ 添加外部模型」来配置吧")
