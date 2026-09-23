import { useState, useEffect } from 'react';
import { placeOrder, getHotStocks, getSignal } from '../api';

const DEFAULT_STOCKS = [
  { code: '600519', name: '贵州茅台' },
  { code: '000001', name: '平安银行' },
  { code: '600036', name: '招商银行' },
  { code: '601318', name: '中国平安' },
  { code: '000858', name: '五粮液' },
  { code: '300750', name: '宁德时代' },
  { code: '002475', name: '立讯精密' },
];

export default function Sidebar({ code, setCode, action, setAction, onAnalyze, aiOnline, marketState, tCode, setTCode, signalMode, setSignalMode }) {
  const [internalTCode, setInternalTCode] = useState(tCode || code || '600519');
  const effectiveTCode = tCode !== undefined ? tCode : internalTCode;
  const effectiveSetTCode = (v) => { if (setTCode) setTCode(v); setInternalTCode(v); };
  const [hotStocks, setHotStocks] = useState([]);
  const [hotLoading, setHotLoading] = useState(true);
  const [volume, setVolume] = useState(10);
  const [horizon, setHorizon] = useState('medium');
  const [ordering, setOrdering] = useState(false);
  const [msg, setMsg] = useState(null);
 const [signal, setSignal] = useState(null);
  const [signalVisible, setSignalVisible] = useState(false);
 const [signalLoading, setSignalLoading] = useState(false);


  useEffect(() => {
    getHotStocks()
      .then(d => {
        const stocks = d.stocks || [];
        if (stocks.length > 0) {
          setHotStocks(stocks);
        } else {
          // fallback to default stocks
          setHotStocks(DEFAULT_STOCKS.map((s, i) => ({ ...s, chg_pct: 0, price: 0 })));
        }
        setHotLoading(false);
      })
      .catch(() => {
        setHotStocks(DEFAULT_STOCKS.map((s) => ({ ...s, chg_pct: 0, price: 0 })));
        setHotLoading(false);
      });
  }, []);

  async function doOrder() {
    if (!effectiveTCode) return;
    setOrdering(true);
    try {
      const r = await placeOrder({ code: effectiveTCode, direction: action, volume, horizon });
      setMsg({ type: 'success', text: `${action === 'buy' ? '买入' : '卖出'}成功 ${effectiveTCode} × ${volume * 100}股 @¥${r.order?.price ?? '?'}` });
      setTimeout(() => setMsg(null), 4000);
    } catch (e) {
      setMsg({ type: 'error', text: e.message || '下单失败' });
      setTimeout(() => setMsg(null), 5000);
    } finally {
      setOrdering(false);
    }
  }

  function handleSelect(c) {
    effectiveSetTCode(c);
    setCode(c);
    setAction('buy');
   setSignal(null); setSignalVisible(false);
  }

  async function doSignal() {
    if (signal && !signalLoading) { setSignalVisible(v => !v); return; }
    if (!effectiveTCode || !aiOnline) return;
    setSignalLoading(true);
    setSignal(null); setSignalVisible(false);
    if (setSignalMode) setSignalMode(true); // block auto-analyze
    try {
      const d = await getSignal(effectiveTCode);
      setSignal(d);
      setSignalVisible(true);
    } catch (e) {
      setSignal({ error: e.message });
      setSignalVisible(true);
    } finally {
      setSignalLoading(false);
      if (setSignalMode) setSignalMode(false); // restore
    }
  }

  const signalResult = signal;
  const signalAction = signalResult?.action || '';
  const signalText = signalResult?.text || signalResult?.error || '';

  return (
    <>
      {msg && <div className={`toast ${msg.type === 'success' ? 'toast-success' : 'toast-error'}`}>{msg.text}</div>}

      <div className="sb-section">
        <div className="sb-section-title">股票查询</div>
        <div className="stock-input-wrap">
          <input className="stock-input" value={effectiveTCode} onChange={e => effectiveSetTCode(e.target.value)} placeholder="6位代码" onKeyDown={e => e.key === 'Enter' && (handleSelect(effectiveTCode), onAnalyze && onAnalyze())} />
        </div>
        <div style={{ display: 'flex', gap: '6px', marginBottom: '6px' }}>
          <button className="btn btn-primary" style={{ flex: 1 }} onClick={() => { handleSelect(effectiveTCode); onAnalyze && onAnalyze(); }}>
            🔍 分析
          </button>
          <button className="btn btn-secondary" style={{ flex: 1 }} onClick={() => { handleSelect(effectiveTCode); doSignal(); }} disabled={!aiOnline || signalLoading}>
            {signalLoading ? '分析中...' : '⚡ 信号'}
          </button>
        </div>
        {/* Quick signal result */}
        {signalVisible && signalResult && !signalResult.error && (
          <div style={{ marginTop: '6px', padding: '8px 10px', background: 'var(--bg-secondary)', borderRadius: '8px', border: `1px solid ${signalAction === 'buy' ? 'rgba(244,63,94,0.3)' : signalAction === 'sell' ? 'rgba(16,185,129,0.3)' : 'var(--border)'}`, fontSize: '11px', color: 'var(--text-secondary)' }}>
            <div style={{ fontWeight: 600, marginBottom: '4px', color: signalAction === 'buy' ? 'var(--red)' : signalAction === 'sell' ? 'var(--green)' : 'var(--text-muted)' }}>
              {signalAction === 'buy' ? '📈 买入信号' : signalAction === 'sell' ? '📉 卖出信号' : '⏸️ 观望'}
              <span style={{ marginLeft: '8px', fontSize: '10px', fontWeight: 400, color: 'var(--text-muted)' }}>{signalResult.period === 'short' ? '短线' : signalResult.period === 'medium' ? '中线' : '长线'}</span>
            </div>
            <div style={{ lineHeight: 1.5 }}>{signalText}</div>
          </div>
        )}
        {signalResult?.error && (
          <div style={{ marginTop: '6px', padding: '8px', background: 'rgba(244,63,94,0.1)', borderRadius: '6px', fontSize: '11px', color: 'var(--red)' }}>
            {signalResult.error}
          </div>
        )}
      </div>

      <div className="sb-section">
        <div className="sb-section-title">快速下单</div>
        <div className="label">方向</div>
        <select className="sel" value={action} onChange={e => setAction(e.target.value)}>
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select>
        <div className="label">数量（手）</div>
        <input className="num-input" type="number" min={1} max={1000} value={volume} onChange={e => setVolume(Number(e.target.value))} />
        {!marketState.open && <div style={{fontSize:'11px',color:'var(--text-muted)',marginBottom:'6px',padding:'6px 8px',background:'var(--bg-secondary)',borderRadius:'6px',textAlign:'center'}}>{"⚠️ " + marketState.message}</div>}
        <div className="label">持仓周期</div>
        <select className="sel" value={horizon} onChange={e => setHorizon(e.target.value)} style={{marginBottom:'8px'}}>
          <option value="short">短线 (5-15天)</option>
          <option value="medium">中线 (1-3个月)</option>
          <option value="long">长线 (3个月+)</option>
        </select>
        <button
          className="btn btn-full"
          style={{
            background: action === 'buy' ? '#f43f5e' : '#10b981',
            color: '#fff',
            fontWeight: 700,
            border: 'none',
            opacity: (!marketState.open || ordering || !tCode) ? 0.5 : 1,
          }}
          onClick={doOrder}
          disabled={ordering || !effectiveTCode || !marketState.open}
        >
          {ordering ? '处理中...' : `${action === 'buy' ? '买入' : '卖出'} ${effectiveTCode}`}
        </button>
      </div>

      <div className="sb-section">
        <div className="sb-section-title">快捷代码</div>
        {hotLoading ? (
          <div style={{textAlign:'center',padding:'12px',color:'var(--text-muted)',fontSize:'11px'}}>加载中...</div>
        ) : hotStocks.length === 0 ? (
          <div style={{textAlign:'center',padding:'12px',color:'var(--text-muted)',fontSize:'11px'}}>暂无推荐</div>
        ) : (
          hotStocks.map((s, idx) => (
            <button key={s.code || idx} className="btn btn-ghost btn-full btn-sm mb-2" onClick={() => handleSelect(s.code)}>
             <span style={{fontWeight:600}}>{s.code}</span>
              <span style={{marginLeft:'6px',fontSize:'11px',color:'var(--text-muted)',whiteSpace:'nowrap'}}>{s.name || ''}</span>
              <span style={{marginLeft:'auto',fontSize:'10px',color: (s.chg_pct || 0) >= 0 ? 'var(--red)' : 'var(--green)'}}>
                {(s.chg_pct || 0) >= 0 ? '↑' : '↓'} {Math.abs(s.chg_pct || 0).toFixed(2)}%
              </span>
            </button>
          ))
        )}
      </div>
    </>
  );
}
