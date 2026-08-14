import { useEffect, useState } from 'react';
import { getPortfolio, getOrders } from '../api';

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

export default function RightPanel({ onSelect = () => {} }) {
  const [portfolio, setPortfolio] = useState(null);
  const [orders, setOrders] = useState([]);
  const [tab, setTab] = useState('pos');
  const [error, setError] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [hoveredRow, setHoveredRow] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [p, o] = await Promise.all([getPortfolio(), getOrders()]);
        if (!cancelled) {
          setPortfolio(p);
          setOrders(o?.orders || []);
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
  const isFirstLoad = portfolio === null;

  return (
    <div>
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
                <span className="text-sm text-muted">{positions.length}只</span>
              </div>

              {/* Header */}
              <div style={{ display: 'flex', alignItems: 'center', padding: '5px 8px', borderBottom: '1px solid #ffffff08', minWidth: 'max-content' }}>
                <div style={{ width: '60px', fontSize: '10px', color: 'var(--text-muted)', fontWeight: 600, letterSpacing: '0.05em', flexShrink: 0 }}>代码/名称</div>
                <div style={{ width: '56px', fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textAlign: 'right', flexShrink: 0 }}>持仓</div>
                <div style={{ width: '52px', fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textAlign: 'right', flexShrink: 0, marginRight: '8px' }}>现价</div>
                <div style={{ width: '68px', fontSize: '10px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', textAlign: 'right', flexShrink: 0, marginRight: '12px' }}>盈亏</div>
                <div style={{ width: '48px', fontSize: '10px', color: 'var(--text-muted)', textAlign: 'center', flexShrink: 0 }}>周期</div>
              </div>

              {/* Rows */}
              {positions.map((p, i) => {
                const pnl = p.unrealized_pnl || 0;
                const costBasis = (p.avg_cost || 0) * (p.volume || 0);
                const pct = costBasis > 0 ? (pnl / costBasis * 100) : 0;
                const pnlColor = pnl >= 0 ? 'var(--red)' : 'var(--green)';
                const pnlTag = pnl >= 0
                  ? { label: '盈', bg: 'rgba(244,63,94,0.12)', color: 'var(--red)' }
                  : { label: '亏', bg: 'rgba(16,185,129,0.12)', color: 'var(--green)' };

                return (
                  <div key={i}
                    onClick={() => { console.log('[DEBUG] onSelect called:', p.stock_code); onSelect(p.stock_code); }}
                    onMouseEnter={() => setHoveredRow(i)}
                    onMouseLeave={() => setHoveredRow(null)}
                    style={{
                      display: 'flex', alignItems: 'center',
                      padding: '7px 8px',
                      minWidth: 'max-content',
                      borderBottom: i < positions.length - 1 ? '1px solid #ffffff08' : 'none',
                      transition: 'background .12s',
                      background: hoveredRow === i ? 'rgba(59,130,246,0.25)' : 'rgba(59,130,246,0.06)',
                      cursor: 'pointer',
                    }}
                  >
                    {/* 代码/名称 合并单元格 */}
                    <div style={{ width: '60px', flexShrink: 0 }}>
                      <div style={{ fontWeight: 600, fontSize: '13px', cursor: 'pointer', color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', lineHeight: 1.3 }}
                        onClick={e => { e.stopPropagation(); onSelect(p.stock_code); }}>
                        {p.stock_code}
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--text-muted)', lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {p.stock_name || ''}
                      </div>
                    </div>

                    {/* 持仓量 */}
                    <div style={{ width: '56px', fontFamily: 'var(--font-mono)', fontSize: '12px', textAlign: 'right', color: 'var(--text-secondary)', flexShrink: 0 }}>
                      {(p.volume || 0).toLocaleString()}
                    </div>

                    {/* 现价 */}
                    <div style={{ width: '52px', fontFamily: 'var(--font-mono)', fontSize: '12px', textAlign: 'right', color: 'var(--text-primary)', flexShrink: 0, marginRight: '8px' }}>
                      ¥{fmt(p.current_price)}
                    </div>

                    {/* 盈亏 */}
                    <div style={{ width: '68px', fontFamily: 'var(--font-mono)', fontSize: '12px', textAlign: 'right', color: pnlColor, fontWeight: 600, flexShrink: 0, marginRight: '12px' }}
                      onClick={e => e.stopPropagation()}>
                      <div>{pnl >= 0 ? '+' : ''}{fmt(pnl)}</div>
                      <div style={{ fontSize: '10px', opacity: 0.8 }}>{pct >= 0 ? '+' : ''}{fmt(pct)}%</div>
                    </div>

                    {/* 周期标签 */}
                    <div style={{ width: '48px', textAlign: 'center', flexShrink: 0 }}>
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
          <div className="card-header">
            <span className="card-title">最近订单</span>
            <button className="btn btn-ghost btn-sm" onClick={() => window.location.reload()}>刷新</button>
          </div>
          <div className="card-body">
            {isFirstLoad ? <InlineSpinner text="加载订单..." /> : orders.length > 0 ? orders.map((o, i) => {
              const realizedPnl = o.direction === 'sell' && o.status === 'filled' && o.pnl != null ? Number(o.pnl) : null;
              const pnlColor = realizedPnl != null && realizedPnl >= 0 ? 'var(--red)' : 'var(--green)';
              return (
                <div key={i} style={{ marginBottom: '10px', paddingBottom: '10px', borderBottom: '1px solid var(--border)' }}>
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
            }) : <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-muted)', fontSize: '13px' }}>暂无订单记录</div>}
          </div>
        </div>
      )}
    </div>
  );
}
