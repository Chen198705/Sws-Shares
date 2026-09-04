"""
A 股 AI 交易系统 Web UI
"""
import sys, datetime
import streamlit as st
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from market_data import get_all_indices, get_stock_realtime, get_stock_history, calc_indicators
from ai_client import OllamaClient
from rule_engine import analyze as rule_analyze
from broker_adapter import get_broker
from trader import get_trading_status
from config import OLLAMA_BASE_URL, OLLAMA_MODEL

st.set_page_config(page_title="A股 AI 交易", page_icon="", layout="wide", initial_sidebar_state="expanded")

# ─── 状态初始化 ──────────────────────────────────────────────
if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None
if "trade_msg" not in st.session_state:
    st.session_state.trade_msg = None

# ─── 侧边栏 ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### A股 AI 交易系统")
    client = OllamaClient()
    ai_ok = client.is_alive()
    st.markdown(f"**AI:** {'✅ 在线' if ai_ok else '⚠️ 离线'}  `{OLLAMA_MODEL}`")
    st.markdown(f"**地址:** `{OLLAMA_BASE_URL}`")
    st.divider()

    code_input = st.text_input("股票代码", value="600519", placeholder="6位代码，如 600519").strip()
    st.divider()

    col_a, col_b = st.columns(2)
    analyze_btn = col_a.button("🔍 分析", use_container_width=True)
    trade_btn = col_b.button("📊 信号", use_container_width=True)

    st.divider()
    st.markdown("**手动下单**")
    t_code = st.text_input("代码", value=code_input, key="tcode").strip()
    t_action = st.selectbox("方向", ["买入", "卖出"], key="taction")
    t_vol = st.number_input("数量（手）", 1, 1000, 10, key="tvol")
    exec_btn = st.button("▶ 执行", type="primary", use_container_width=True)
    st.divider()
    st.caption(f"刷新: {datetime.datetime.now().strftime('%H:%M:%S')}")
    st.caption("仅供辅助决策，不构成投资建议")

# ─── 手动下单处理 ─────────────────────────────────────────────
if exec_btn and t_code:
    broker = get_broker()
    vol = int(t_vol) * 100
    try:
        stock = get_stock_realtime(t_code)
        price = stock.get("最新价", 0)
        if t_action == "买入":
            order = broker.buy(t_code, vol, price)
            if order.status == "filled":
                st.session_state.trade_msg = ("success", f"✅ 买入成功：{t_code} × {vol}股 @{order.filled_price:.2f}")
            else:
                st.session_state.trade_msg = ("error", f"❌ 买入失败：{order.status}")
        else:
            if broker.sellable_volume(t_code) <= 0:
                st.session_state.trade_msg = ("error", f"🔒 {t_code} 当日买入（T+0），按 A股规则次日才能卖")
            else:
                order = broker.sell(t_code, vol, price)
                if order is not None and order.status == "filled":
                    st.session_state.trade_msg = ("warning", f"⚠️ 卖出成功：{t_code} × {vol}股 @{order.filled_price:.2f}")
                else:
                    st.session_state.trade_msg = ("error", f"❌ 卖出失败")
    except Exception as e:
        st.session_state.trade_msg = ("error", f"❌ 下单失败: {e}")

if st.session_state.trade_msg:
    lvl, msg = st.session_state.trade_msg
    if lvl == "success": st.success(msg)
    elif lvl == "warning": st.warning(msg)
    else: st.error(msg)

# ─── 主内容区 ─────────────────────────────────────────────────
tab_main, tab_pos, tab_orders = st.tabs(["📈 分析", "💼 持仓", "📋 订单"])

with tab_main:
    # 指数行情条
    try:
        indices = get_all_indices()
        cols = st.columns(len(indices))
        for i, (name, d) in enumerate(indices.items()):
            with cols[i]:
                if "错误" not in d:
                    pct = d["涨跌幅"]
                    delta_color = "normal" if pct >= 0 else "inverse"
                    st.metric(name, f"{d['最新价']:.2f}", f"{pct:+.2f}%", delta_color=delta_color)
                else:
                    st.metric(name, "—", "获取失败")
    except Exception as e:
        st.warning(f"指数数据获取失败: {e}")

    st.divider()

    # 个股分析
    if (analyze_btn or trade_btn) and code_input:
        stock = get_stock_realtime(code_input)
        if "错误" in stock:
            st.error(stock["错误"])
        else:
            df, ind = calc_indicators(get_stock_history(code_input)) if get_stock_history(code_input) is not None else (None, None)

            # 行情卡片
            pct = stock["涨跌幅"]
            trend = "📈 上涨" if pct > 0 else "📉 下跌" if pct < 0 else "➖ 平盘"
            trend_color = "#00c896" if pct > 0 else "#ff4d6d" if pct < 0 else "#888"
            st.markdown(f"### {stock['股票名']}（{code_input}）  <span style='color:{trend_color};font-size:0.8em'>{trend}</span>", unsafe_allow_html=True)

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("最新价", f"{stock['最新价']:.2f}", f"{pct:+.2f}%")
            m2.metric("今开", f"{stock['今开']:.2f}", f"昨收 {stock['昨收']:.2f}")
            m3.metric("最高", f"{stock['最高']:.2f}", f"最低 {stock['最低']:.2f}")
            m4.metric("成交额", f"{stock['成交额']/1e8:.2f}亿", "")
            if ind:
                rsi_val = ind["RSI(14)"]
                rsi_status = ind["RSI状态"]
                m5.metric("RSI(14)", f"{rsi_val:.1f}", rsi_status)
            else:
                m5.metric("RSI(14)", "—", "数据不足")

            # 技术指标行
            if ind:
                si1, si2, si3, si4, si5 = st.columns(5)
                si1.metric("MA5", f"{ind['MA5']:.2f}", "")
                si2.metric("MA20", f"{ind['MA20']:.2f}", "")
                si3.metric("MACD", f"{ind['MACD']:.4f}", "金叉✅" if ind["MACD金叉"] else "死叉❌")
                si4.metric("量比", f"{ind.get('量比', '—')}", "")
                si5.metric("均线", ind['均线多头'], "")

            st.divider()

            # AI / 规则分析
            if ai_ok:
                try:
                    avg_pct = sum(d.get("涨跌幅", 0) for d in indices.values() if "错误" not in d) / max(len([d for d in indices.values() if "错误" not in d]), 1)
                    prompt = f"""股票：{stock['股票名']}（{code_input}）
最新价：{stock['最新价']} 涨跌幅：{stock['涨跌幅']:+.2f}%
今开={stock['今开']} 最高={stock['最高']} 最低={stock['最低']} 昨收={stock['昨收']}
MA5={ind['MA5']:.2f} MA20={ind['MA20']:.2f} RSI={ind['RSI(14)']:.1f} MACD金叉={ind['MACD金叉']} 均线多头={ind['均线多头']}
大盘平均涨跌：{avg_pct:+.2f}%

请给出：1.技术面简析 2.操作建议（买入/观望/卖出+仓位建议+止损位） 3.风险提示。简洁专业。"""
                    analysis = client.chat([
                        {"role": "system", "content": "你是专业A股投资顾问，简洁专业，禁止废话。"},
                        {"role": "user", "content": prompt},
                    ], temperature=0.3)
                    st.markdown(analysis)
                except Exception as e:
                    st.error(f"AI 分析失败: {e}，切换规则引擎...")
                    analysis, action = rule_analyze(stock, ind, 0)
                    st.markdown(analysis)
            else:
                analysis, action = rule_analyze(stock, ind, 0)
                st.markdown(analysis)
                st.caption("⚠️ AI 离线，以上由规则引擎生成")

            # 交易信号
            if trade_btn:
                text_lower = analysis.lower()
                if "买入" in text_lower and "不买" not in text_lower:
                    sig, color = "买入", "green"
                elif "卖出" in text_lower or "减仓" in text_lower:
                    sig, color = "卖出/减仓", "red"
                else:
                    sig, color = "观望", "blue"
                st.markdown(f"**交易信号：** :{color}[{sig}]")

    else:
        st.info("👈 在左侧输入股票代码，点击「分析」或「信号」开始")

# ─── 持仓 ──────────────────────────────────────────────────────
with tab_pos:
    try:
        status = get_trading_status()
        bal = status["balance"]
        b1, b2, b3 = st.columns(3)
        b1.metric("总资产", f"¥{bal['total_assets']:,.2f}")
        b2.metric("现金", f"¥{bal['cash']:,.2f}")
        b3.metric("持仓市值", f"¥{bal.get('market_value', 0):,.2f}")

        positions = status["positions"]
        if positions:
            for p in positions:
                pnl_pct = p.unrealized_pnl / (p.cost * p.volume) * 100 if p.cost > 0 else 0
                color = "green" if p.unrealized_pnl >= 0 else "red"
                st.markdown(f"""| {p.stock_name}（{p.stock_code}） | {p.volume}股 | 成本 ¥{p.cost:.2f} | 现价 ¥{p.current_price:.2f} | 盈亏 :{color}[¥{p.unrealized_pnl:+.2f} ({pnl_pct:+.2f}%)] |""")
        else:
            st.info("暂无持仓")
    except Exception as e:
        st.error(f"获取持仓失败: {e}")

# ─── 订单 ──────────────────────────────────────────────────────
with tab_orders:
    try:
        broker = get_broker()
        orders = broker.get_orders(20)
        if orders:
            for o in reversed(orders):
                d = "买" if o.direction == "buy" else "卖"
                st.markdown(f"**{d}** {o.stock_code} × {o.volume} @{o.price:.2f}  [{o.status}]  {o.created_at.strftime('%m-%d %H:%M')}")
        else:
            st.info("暂无订单记录")
    except Exception as e:
        st.error(f"获取订单失败: {e}")
