import { useEffect, useState } from 'react';
import { getPortfolio, getOrders, getOrderStats } from '../api';

function fmt(v, dec = 2) {
  return v != null ? Number(v).toLocaleString('zh-CN', { minimumFractionDigits: dec, maximumFractionDigits: dec }) : '—';
}

const HORIZON_MAP = {
  short: { label: '短线', color: '#f43f5e' },
  medium: { label: '中线', color: '#3b82f6' },
  long:   { label: '长线', color: '#10b981' },
};

function HorizonBadge({ h, code, onClick }) {
  const m = HORIZON_MAP[h] || HORIZON_MAP.medium;
  return (
    <span onClick={e => { e.stopPropagation(); console.log('[DEBUG] HorizonBadge clicked, code:', code); onClick && onClick(code); }}
      style={{ fontSize: '9px', fontWeight: 600, padding: '2px 5px', borderRadius: '8px',
        border: `1px solid ${m.color}55`, color: m.color, background: `${m.color}28`,
        cursor: 'pointer', userSelect: 'none', display: 'inline-block', flexShrink: 0, whiteSpace: 'nowrap' }}>
      {m.label}
    </span>
  );
}

function InlineSpinner({ text = '加载中...' }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '10px', padding: '24px', color: 'var(--text-muted)', fontSize: '12px' }}>
      <div style={{ width: '28px', height: '28px', border: '2px solid var(--border)', borderTopColor: 'var(--blue)', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
      {text}
    </div>
  );
}

function ResearchPanel() {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    fetch('/api/research/status')
      .then(r => r.json())
      .then(d => setData(d))
      .catch(() => {});
  }, []);
  const contract = data?.contract || {};
  const regime = contract.regime?.state || '—';
  const factors = contract.factor_constraints || [];
  const attr = data?.attribution?.total;
  if (!data) return null;
  return (
    <div className="card" style={{ marginTop: '10px' }}>
      <div className="card-header" style={{ cursor: 'pointer' }} onClick={() => setOpen(o => !o)}>
        <span className="card-title">研究层</span>
        <span className="text-sm text-muted">{contract.confidence || 'L0'} · {regime}{open ? ' ▴' : ' ▾'}</span>
      </div>
      {open && (
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: '6px', padding: '8px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {factors.map(f => (
              <span key={f.id} title={f.note || f.id}
                style={{ fontSize: '10px', padding: '2px 6px', borderRadius: '6px', border: '1px solid var(--border)',
                  color: f.status.includes('❌') ? 'var(--red)' : (f.status.includes('⚠️') ? '#f59e0b' : 'var(--text-muted)'),
                  background: f.status.includes('❌') ? '#f43f5e1a' : (f.status.includes('⚠️') ? '#f59e0b1a' : 'transparent') }}>
                {f.id}
              </span>
            ))}
          </div>
          {contract.crowding && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
              {Object.entries(contract.crowding).map(([id, c]) => (
                <span key={id} title={(c.flags || []).join('，') || c.note || id}
                  style={{ fontSize: '10px', padding: '2px 6px', borderRadius: '6px', border: '1px solid var(--border)',
                    color: c.crowded ? 'var(--red)' : 'var(--text-muted)',
                    background: c.crowded ? '#f43f5e1a' : 'transparent' }}>
                  {id}{c.crowded ? ' ⚠️' : ''}
                </span>
              ))}
            </div>
          )}
          {contract.regime?.history?.months?.length > 0 && (
            <div style={{ fontSize: '10px', color: 'var(--text-muted)', display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {contract.regime.history.months.slice(-6).map(m => {
                const rule = contract.regime.history.rule?.[m] || '';
                return <span key={m}>{m.slice(2)} {rule.split(' ')[0]}</span>;
              })}
            </div>
          )}
          {attr && (
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
              已平仓 {attr.count} 笔 · 胜率 {Math.round(attr.win_rate * 100)}% · 净盈亏
              <span style={{ color: attr.net_pnl >= 0 ? 'var(--red)' : 'var(--green)', fontWeight: 600 }}>
                {attr.net_pnl >= 0 ? '+' : ''}{Number(attr.net_pnl).toFixed(2)}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function OrderRow({ o, fmt }) {
  const realizedPnl = o.direction === 'sell' && o.status === 'filled' && o.pnl != null ? Number(o.pnl) : null;
  const pnlColor = realizedPnl != null && realizedPnl >= 0 ? 'var(--red)' : 'var(--green)';
  return (
    <div style={{ marginBottom: '10px', paddingBottom: '10px', borderBottom: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
        <span style={{ fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px', background: o.direction === 'buy' ? 'rgba(244,63,94,0.15)' : 'rgba(16,185,129,0.15)', color: o.direction === 'buy' ? 'var(--red)' : 'var(--green)', border: `1px solid ${o.direction === 'buy' ? 'rgba(244,63,94,0.3)' : 'rgba(16,185,129,0.3)'}` }}>
          {o.direction === 'buy' ? '买' : '卖'}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.3 }}>{o.code}</div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.stock_name || ''}</div>
        </div>
        <span style={{ marginLeft: 'auto', fontSize: '11px', color: o.status === 'filled' ? 'var(--green)' : 'var(--text-muted)', flexShrink: 0 }}>{o.status === 'filled' ? '✓ 已成交' : o.status}</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>
        <span>×{o.volume} @ ¥{fmt(o.price)}</span>
        <span>{o.time ? o.time.slice(0, 16).replace('T', ' ') : ''}</span>
      </div>
      {realizedPnl != null && (
        <div style={{ fontSize: '12px', fontWeight: 600, color: pnlColor, fontFamily: 'var(--font-mono)', marginTop: '2px' }}>
          已实现盈亏 {realizedPnl >= 0 ? '+' : ''}{fmt(realizedPnl)}
        </div>
      )}
    </div>
  );
}

export default function RightPanel({ onSelect = () => {} }) {
  const [portfolio, setPortfolio] = useState(null);
  const [orders, setOrders] = useState([]);
  const [orderStats, setOrderStats] = useState(null);
  const [tab, setTab] = useState('pos');
  const [orderDir, setOrderDir] = useState('all');
  const [error, setError] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [hoveredRow, setHoveredRow] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [p, o, s] = await Promise.all([getPortfolio(), getOrders(), getOrderStats().catch(() => null)]);
        if (!cancelled) {
          setPortfolio(p);
          setOrders(o?.orders || []);
          setOrderStats(s);
          setError(null);
          setLastRefresh(new Date());
        }
      } catch (e) { if (!cancelled) setError(e.message); }
    }
    load();
    const t = setInterval(load, 15000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  const bal = portfolio?.balance || {};
  const positions = portfolio?.positions || [];
  // 持仓明细聚合：今日盈亏（按昨收基准）与累计盈亏（按成本基准）
  const todayTotal = positions.reduce((s, p) => {
    const pc = Number(p.prev_close || 0);
    if (pc <= 0) return s;
    return s + ((Number(p.current_price || 0) - pc) * (Number(p.volume || 0)));
  }, 0);
  const pnlTotal = positions.reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
  const isFirstLoad = portfolio === null;
  const filteredOrders = orderDir === 'all' ? orders : orders.filter(o => o.direction === orderDir);

  return (
    <div>
      <ResearchPanel />
      <div className="tabs">
        <button className={`tab ${tab === 'pos' ? 'active' : ''}`} onClick={() => setTab('pos')}>持仓</button>
        <button className={`tab ${tab === 'orders' ? 'active' : ''}`} onClick={() => setTab('orders')}>订单</button>
      </div>

      {error && (
        <div style={{ color: 'var(--red)', fontSize: '12px', padding: '8px', textAlign: 'center', background: '#f43f5e1a', borderRadius: '6px', marginBottom: '8px' }}>
          加载失败: {error}
        </div>
      )}

      {tab === 'pos' && (
        <>
          <div className="sb-section mb-2">
            <div className="balance-row"><span className="balance-label">总资产</span><span className="balance-value">¥{fmt(bal.total_assets)}</span></div>
            <div className="balance-row"><span className="balance-label">现金</span><span className="balance-value" style={{ fontSize: '13px' }}>¥{fmt(bal.cash)}</span></div>
            <div className="balance-row"><span className="balance-label">持仓市值</span><span className="balance-value" style={{ fontSize: '13px' }}>¥{fmt(bal.market_value)}</span></div>
            {lastRefresh && <div style={{ fontSize: '10px', color: 'var(--text-muted)', textAlign: 'right', marginTop: '4px' }}>{lastRefresh.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</div>}
          </div>

          {isFirstLoad ? (
            <InlineSpinner text="加载持仓..." />
          ) : positions.length > 0 ? (
            <div className="card">
              <div className="card-header">
                <span className="card-title">持仓明细</span>
                <span className="pos-agg">
                  <span className="pos-agg-stack" title="今日盈亏汇总（按昨收基准）">
                    <span className="pos-agg-label">今日</span>
                    <span className="pos-agg-val" style={{ color: todayTotal >= 0 ? 'var(--red)' : 'var(--green)' }}>
                      {todayTotal >= 0 ? '+' : ''}{fmt(todayTotal)}
                    </span>
                  </span>
                  <span className="pos-agg-sep">·</span>
                  <span className="pos-agg-stack" title="累计盈亏汇总（按成本基准）">
                    <span className="pos-agg-label">累计盈亏</span>
                    <span className="pos-agg-val" style={{ color: pnlTotal >= 0 ? 'var(--red)' : 'var(--green)' }}>
                      {pnlTotal >= 0 ? '+' : ''}{fmt(pnlTotal)}
                    </span>
                  </span>
                  <span className="pos-agg-sep">·</span>
                  <span className="text-sm text-muted">{positions.length}只</span>
                </span>
              </div>

              {/* Header */}
              <div className="pos-row-header">
                <div style={{ textAlign: 'left' }}>代码/名称</div>
                <div style={{ textAlign: 'right' }}>持仓</div>
                <div style={{ textAlign: 'right' }}>现价</div>
                <div style={{ textAlign: 'right' }}>今日</div>
                <div style={{ textAlign: 'right' }}>盈亏</div>
                <div style={{ textAlign: 'center' }}>周期</div>
              </div>

              {/* Rows */}
              {positions.map((p, i) => {
                const pnl = p.unrealized_pnl || 0;
                const costBasis = (p.avg_cost || 0) * (p.volume || 0);
                const pct = costBasis > 0 ? (pnl / costBasis * 100) : 0;
                const prevClose = Number(p.prev_close || 0);
                const todayPnl = prevClose > 0 ? (p.current_price - prevClose) * (p.volume || 0) : 0;
                const todayPct = prevClose > 0 ? ((p.current_price / prevClose - 1) * 100) : 0;

                return (
                  <div key={i}
                    className="pos-row"
                    onClick={() => { onSelect(p.stock_code); }}
                    onMouseEnter={() => setHoveredRow(i)}
                    onMouseLeave={() => setHoveredRow(null)}
                    style={{
                      borderBottom: i < positions.length - 1 ? '1px solid #ffffff08' : 'none',
                      background: hoveredRow === i ? 'rgba(59,130,246,0.25)' : 'rgba(59,130,246,0.06)',
                      cursor: 'pointer',
                    }}
                  >
                    <div className="col-code">
                      <div className="code"
                        onClick={e => { e.stopPropagation(); onSelect(p.stock_code); }}>
                        {p.stock_code}
                      </div>
                      <div className="name">
                        {p.stock_name || ''}
                      </div>
                    </div>

                    <div className="col-vol">
                      {(p.volume || 0).toLocaleString()}
                    </div>

                    <div className="col-px">
                      ¥{fmt(p.current_price)}
                    </div>

                    <div className="col-today"
                      style={{ color: todayPnl >= 0 ? 'var(--red)' : 'var(--green)' }}
                      onClick={e => e.stopPropagation()}>
                      <div>{prevClose > 0 ? (todayPnl >= 0 ? '+' : '') + fmt(todayPnl) : '—'}</div>
                      <div className="pct">{prevClose > 0 ? (todayPct >= 0 ? '+' : '') + fmt(todayPct) + '%' : '—'}</div>
                    </div>

                    <div className="col-pnl"
                      style={{ color: pnl >= 0 ? 'var(--red)' : 'var(--green)' }}
                      onClick={e => e.stopPropagation()}>
                      <div>{pnl >= 0 ? '+' : ''}{fmt(pnl)}</div>
                      <div className="pct">{pct >= 0 ? '+' : ''}{fmt(pct)}%</div>
                    </div>

                    <div className="col-hz">
                      <HorizonBadge h={p.horizon} code={p.stock_code} onClick={onSelect} />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-muted)', fontSize: '13px' }}>
              暂无持仓
            </div>
          )}
        </>
      )}

      {tab === 'orders' && (
        <div className="card">
          <div className="order-tab-head">
            <div className="title-row">
              <span className="card-title">最近订单</span>
              <button className="btn btn-ghost btn-sm" onClick={() => window.location.reload()}>刷新</button>
            </div>
            <div className="btn-row">
              <button className={`btn btn-ghost btn-sm ${orderDir === 'all' ? 'active' : ''}`} onClick={() => setOrderDir('all')}>
                全部<span className="count">{orderStats?.counts?.all ?? '—'}</span>
              </button>
              <button className={`btn btn-ghost btn-sm ${orderDir === 'buy' ? 'active' : ''}`} onClick={() => setOrderDir('buy')}>
                买入<span className="count">{orderStats?.counts?.buy ?? '—'}</span>
              </button>
              <button className={`btn btn-ghost btn-sm ${orderDir === 'sell' ? 'active' : ''}`} onClick={() => setOrderDir('sell')}>
                卖出<span className="count">{orderStats?.counts?.sell ?? '—'}</span>
              </button>
            </div>
          </div>
          {orderStats && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', padding: '8px 10px', borderBottom: '1px solid var(--border)', fontSize: '11px', fontFamily: 'var(--font-mono)' }}>
              {(orderDir === 'all' || orderDir === 'sell') && orderStats.sell && (
                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center', color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>卖出</span>
                  <span>{orderStats.sell.count}笔</span>
                  <span style={{ color: orderStats.sell.profit > 0 ? 'var(--red)' : 'var(--text-muted)' }}>盈利 +{fmt(orderStats.sell.profit)}</span>
                  <span style={{ color: orderStats.sell.loss < 0 ? 'var(--green)' : 'var(--text-muted)' }}>亏损 {fmt(orderStats.sell.loss)}</span>
                  <span style={{ color: orderStats.sell.net >= 0 ? 'var(--red)' : 'var(--green)', fontWeight: 700 }}>合计 {orderStats.sell.net >= 0 ? '+' : ''}{fmt(orderStats.sell.net)}</span>
                </div>
              )}
              {(orderDir === 'all' || orderDir === 'buy') && orderStats.buy && (
                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center', color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>买入</span>
                  <span>{orderStats.buy.count}笔</span>
                  <span>成本 ¥{fmt(orderStats.buy.cost)}</span>
                  <span style={{ color: orderStats.buy.profit > 0 ? 'var(--red)' : 'var(--text-muted)' }}>浮盈 +{fmt(orderStats.buy.profit)}</span>
                  <span style={{ color: orderStats.buy.loss < 0 ? 'var(--green)' : 'var(--text-muted)' }}>浮亏 {fmt(orderStats.buy.loss)}</span>
                  <span style={{ color: orderStats.buy.net >= 0 ? 'var(--red)' : 'var(--green)', fontWeight: 700 }}>浮动 {orderStats.buy.net >= 0 ? '+' : ''}{fmt(orderStats.buy.net)}</span>
                </div>
              )}
            </div>
          )}
          <div className="card-body">
            {isFirstLoad ? (
              <InlineSpinner text="加载订单..." />
            ) : filteredOrders.length > 0 ? (
              filteredOrders.map((o, i) => <OrderRow key={i} o={o} fmt={fmt} />)
            ) : (
              <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-muted)', fontSize: '13px' }}>暂无订单记录</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
