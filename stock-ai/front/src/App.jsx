import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import IndexBar from './components/IndexBar';
import Sidebar from './components/Sidebar';
import StockChart from './components/StockChart';
import RightPanel from './components/RightPanel';
import { analyzeStock, getStock, getHistory, getMarketStatus, getBotModel, setBotModel, getHotStocks } from './api';

// ─── Logo SVG ───
function LogoMark() {
  return (
    <div className="logo-mark">
      <div className="logo-icon">
        <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect width="32" height="32" rx="8" fill="url(#logoGrad)" />
          <path d="M8 22 L12 14 L16 18 L20 10 L24 14" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="24" cy="14" r="2.5" fill="#10b981" />
          <defs>
            <linearGradient id="logoGrad" x1="0" y1="0" x2="32" y2="32">
              <stop offset="0%" stopColor="#1d4ed8" />
              <stop offset="100%" stopColor="#1e3a8a" />
            </linearGradient>
          </defs>
        </svg>
      </div>
      <div className="logo-text">
        <span className="logo-title">A股 · AI</span>
        <span className="logo-sub">量化交易系统</span>
      </div>
    </div>
  );
}

// ─── Chart icon ───
function ChartIcon() {
  return (
    <svg className="card-title-icon" viewBox="0 0 16 16" fill="none">
      <rect x="1" y="8" width="3" height="7" rx="1" fill="currentColor" opacity="0.5"/>
      <rect x="6" y="5" width="3" height="10" rx="1" fill="currentColor" opacity="0.7"/>
      <rect x="11" y="2" width="3" height="13" rx="1" fill="currentColor"/>
    </svg>
  );
}

// ─── Rule icon ───
function RuleIcon() {
  return (
    <svg className="rule-icon" viewBox="0 0 16 16" fill="none">
      <rect x="2.5" y="2.5" width="11" height="11" rx="2" stroke="currentColor" strokeWidth="1.3"/>
      <path d="M5 6h6M5 8.5h6M5 11h4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
    </svg>
  );
}

// ─── Close icon ───
function CloseIcon() {
  return (
    <svg className="close-icon" viewBox="0 0 16 16" fill="none">
      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/>
    </svg>
  );
}

// ─── Rule section ───
function RuleSection({ tone, badge, title, items }) {
  return (
    <div className={`rule-section rule-${tone}`}>
      <div className="rule-section-head">
        <span className="rule-section-title">{title}</span>
        <span className="rule-section-badge">{badge}</span>
      </div>
      <ul className="rule-list">
        {items.map((item, i) => (
          <li key={i}>
            <span className="rule-bullet" />
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─── Rule popover ───
function RulePopover({ onClose, anchorRect }) {
  const [pos, setPos] = useState(null);

  useEffect(() => {
    if (!anchorRect) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const panelW = Math.min(440, vw - 24);
    const left = Math.min(Math.max(8, anchorRect.right - panelW), Math.max(8, vw - panelW - 8));
    const top = anchorRect.bottom + 8;
    setPos({ left, top, panelW, maxH: Math.max(220, vh - top - 8) });
  }, [anchorRect]);

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return createPortal(
    <>
      <div className="rule-backdrop" onClick={onClose} />
      <div
        className="rule-popover"
        role="dialog"
        aria-label="K线 / 交易规则"
        style={pos ? {
          left: pos.left,
          top: pos.top,
          width: pos.panelW,
          maxHeight: pos.maxH,
        } : { visibility: 'hidden' }}
      >
        <div className="rule-popover-head">
          <span className="rule-popover-title"><RuleIcon />K线 / 交易规则</span>
          <button className="rule-popover-close" aria-label="关闭" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>
        <div className="rule-popover-body">
          <div className="rule-intro">
            <span className="rule-intro-dot" />
            沈万三按以下规则扫描全市场并给出短/中/长线决策
          </div>
          <div className="rule-grid">
            <RuleSection tone="buy" badge="需同时满足" title="买入条件" items={[
              'RSI(14) < 40 或 KDJ K值 < 30（超卖）',
              '股价在 MA5 与 MA20 之间企稳',
              '相对大盘（沪深300）涨幅领先',
            ]} />
            <RuleSection tone="sell" badge="任一触发" title="卖出条件" items={[
              'RSI(14) > 70 或 KDJ K值 > 80（超买）',
              '股价跌破 MA20 且未能在 3 日内收复',
              '持仓亏损超过 -5%',
            ]} />
          </div>
          <RuleSection tone="period" badge="持仓参考" title="持仓周期" items={[
            '短线：5–15 个交易日（RSI 超卖短线反弹）',
            '中线：1–3 个月（均线多头 + 趋势确立）',
            '长线：3 个月以上（基本面驱动）',
          ]} />
          <RuleSection tone="risk" badge="风控红线" title="风险控制" items={[
            '单只仓位上限 20% 总资产',
            '总持仓不超过 5 只股票',
            '止损位：买入价 -6%',
          ]} />
        </div>
      </div>
    </>,
    document.body
  );
}

// ─── AI icon ───
function AiIcon() {
  return (
    <svg className="card-title-icon" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="8" cy="8" r="2.5" fill="currentColor"/>
      <line x1="8" y1="1.5" x2="8" y2="4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="8" y1="12" x2="8" y2="14.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="1.5" y1="8" x2="4" y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="12" y1="8" x2="14.5" y2="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );
}

// ─── Helpers ───
function fmt(v, d = 2) {
  return v != null ? Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
}

function parseSignal(text) {
  const t = (text || '').toLowerCase();
  if (t.includes('[信号]')) {
    const seg = t.split('[信号]')[1].split('[')[0].trim();
    const first = seg.split('\n')[0].replace(/^[：: ]+|[，,。；;]+$/g, '');
    if (first.includes('持有') || first.includes('观望') || first.includes('等待')) return 'hold';
    if (first.includes('卖出') || first.includes('清仓') || first.includes('减仓') || first.includes('离场')) return 'sell';
    if (first.includes('买入') || first.includes('加仓') || first.includes('低吸') || first.includes('补仓')) return 'buy';
    return 'hold';
  }
  if (t.includes('建议卖出') || t.includes('卖出信号') || t.includes('建议清仓') || t.includes('清仓回避') || t.includes('建议减仓')) return 'sell';
  if (t.includes('建议买入') && !t.includes('不建议买入')) return 'buy';
  if (t.includes('买入信号') || t.includes('建议加仓') || t.includes('建议低吸')) return 'buy';
  return 'hold';
}

function horizonLabel(horizon) {
  return ({ short: '短线', medium: '中线', long: '长线' })[horizon] || '';
}

function extractAdvice(text) {
  if (!text) return '';
  let m = text.match(/\[操作建议\]\s*([^\[]+)/);
  if (!m) m = text.match(/(?:^|\n)\s*2\.\s*操作建议\s*[：:]\s*([^\n]+)/);
  if (!m) m = text.match(/操作建议[：:]\s*([^\n]+)/);
  if (!m) return '';
  return m[1].replace(/[#*`>]/g, '').replace(/\s+/g, ' ').trim().slice(0, 48);
}

// ─── Analysis Result ───
function AnalysisResult({ data, code }) {
  if (!data) return null;
  if (data.error) return <div className="empty"><div className="empty-title">请求失败</div><div className="empty-sub text-muted">{data.error}</div></div>;

  const { analysis } = data;
  const signal = data.action || parseSignal(analysis || '');
  const cycle = horizonLabel(data.horizon);
  const advice = extractAdvice(analysis || '');

  return (
    <div>
      {/* AI analysis card */}
      <div className="card">
        <div className="card-header">
          <span className="card-title"><AiIcon />AI 分析</span>
          <div className="analysis-head-summary">
            <span className={`analysis-chip signal-chip signal-${signal}`}>
              {signal === 'buy' ? '📈 买入信号' : signal === 'sell' ? '📉 卖出信号' : '⏸️ 观望'}
            </span>
            {cycle && <span className="analysis-chip horizon-chip">{cycle}</span>}
            {advice && <span className="analysis-chip advice-chip" title={advice}>{advice}</span>}
          </div>
        </div>
        <div className="card-body">
          <div className="analysis-box">{analysis || '分析中...'}</div>
        </div>
      </div>
    </div>
  );
}

// ─── Stock Info Card (auto-loads on code change) ───
function StockInfoCard({ code }) {
  const [stock, setStock] = useState(null);
  const [ind, setInd] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!code) return;
    setLoading(true);
    setInd(null);
    Promise.allSettled([
      getStock(code),
      getHistory(code, 240, 'day'),
    ]).then(([stockRes, histRes]) => {
      if (stockRes.status === 'fulfilled') setStock(stockRes.value);
      if (histRes.status === 'fulfilled') setInd(histRes.value.indicators || null);
      setLoading(false);
    });
  }, [code]);
  if (loading || !stock) return <div className="card"><div className="card-body"><div className="loading"><div className="spinner"/>加载行情...</div></div></div>;
  if (stock.错误) return null;
  const pct = stock.涨跌幅 || 0;
  const trendCls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
  return (
    <div className="card">
      <div className="card-body">
        <div className="stock-header">
          <div className="stock-info">
            <div className="stock-name">{stock.股票名 || code}</div>
            <div className="stock-code">{code}</div>
          </div>
          <div className="stock-price-block">
            <div className="stock-price">{stock.最新价 != null ? Number(stock.最新价).toLocaleString('zh-CN') : '—'}</div>
            <div className={`stock-trend ${trendCls}`}>{pct >= 0 ? '↑' : '↓'} {Math.abs(pct).toFixed(2)}%</div>
          </div>
        </div>
        <div className="metric-row">
          <div className="metric-item"><div className="metric-label">今开</div><div className="metric-value">{stock.今开}</div><div className="metric-sub">昨收 {stock.昨收}</div></div>
          <div className="metric-item"><div className="metric-label">最高</div><div className="metric-value">{stock.最高}</div><div className="metric-sub">最低 {stock.最低}</div></div>
          <div className="metric-item"><div className="metric-label">成交额</div><div className="metric-value">{(stock.成交额/1e8).toFixed(2)}亿</div><div className="metric-sub">成交量 {(stock.成交量/1e4).toFixed(2)}万手</div></div>
          <div className="metric-item"><div className="metric-label">换手率</div><div className="metric-value">{stock.换手率 || 0}%</div><div className="metric-sub">量比 {ind ? fmt(ind.量比) : '—'}</div></div>
        </div>
        {ind && (
          <div className="ind-row">
            <div className="ind-item"><div className="ind-label">MA5</div><div className="ind-value">{fmt(ind.MA5)}</div></div>
            <div className="ind-item"><div className="ind-label">MA20</div><div className="ind-value">{fmt(ind.MA20)}</div></div>
            <div className="ind-item"><div className="ind-label">RSI</div><div className="ind-value">{fmt(ind['RSI(14)'], 1)}</div></div>
            <div className="ind-item"><div className="ind-label">MACD</div><div className="ind-value">{fmt(ind.MACD, 4)}</div></div>
            <div className="ind-item"><div className="ind-label">KDJ</div><div className="ind-value">K{fmt(ind.K, 0)} D{fmt(ind.D, 0)}</div></div>
            <div className="ind-item"><div className="ind-label">均线</div><div className="ind-value" style={{ color: ind.均线多头 === '是' ? 'var(--green)' : 'var(--text-muted)' }}>{ind.均线多头}</div></div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Main App ───
export default function App() {
  const [code, setCode] = useState('');
  const codeRef = useRef('');
  const [analysisData, setAnalysisData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [signalMode, setSignalMode] = useState(false); // block auto-analyze during signal toggle
  const [aiOnline, setAiOnline] = useState(false);
  const [modelName, setModelName] = useState('');
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [marketState, setMarketState] = useState({ open: false, message: '' });
  const [ruleVisible, setRuleVisible] = useState(false);
  const [botModel, setBotModel] = useState('');
  const [botModelPending, setBotModelPending] = useState('');
  const [botSettingsOpen, setBotSettingsOpen] = useState(false);
  const [botSaving, setBotSaving] = useState(false);
  const [action, setAction] = useState('buy');
  const [ruleAnchor, setRuleAnchor] = useState(null);

  // Health check + market status
  useEffect(() => {
    fetch('/api/health')
      .then(r => r.json())
      .then(d => { setAiOnline(d.ai); setModelName(d.model || ''); })
      .catch(() => {});
    fetch('/api/bot-model')
      .then(r => r.json())
      .then(d => { setBotModel(d.model || ''); setBotModelPending(d.model || ''); })
      .catch(() => {});
    fetch('/api/models')
      .then(r => r.json())
      .then(d => { setModels(d.models || []); setSelectedModel(d.current || ''); setModelName(d.current || ''); })
      .catch(() => {});
    fetch('/api/market-status')
      .then(r => r.json())
      .then(d => setMarketState({ open: d.open, message: d.message }))
      .catch(() => {});
    // Default query code: first stock in today's recommendation list.
    getHotStocks()
      .then(d => {
        const first = (d.stocks && d.stocks[0] && d.stocks[0].code) || '600519';
        if (!codeRef.current) {
          codeRef.current = first;
          setCode(first);
        }
      })
      .catch(() => {
        if (!codeRef.current) {
          codeRef.current = '600519';
          setCode('600519');
        }
      });
    const t = setInterval(() => {
      fetch('/api/market-status').then(r => r.json()).then(d => setMarketState({ open: d.open, message: d.message })).catch(() => {});
    }, 60000);
   return () => clearInterval(t);
 }, []);


  // Auto-analyze when stock code changes
  useEffect(() => {
    if (!code || signalMode) return;
    setLoading(true);
    setAnalysisData(null);
    const timer = setTimeout(() => {
      analyzeStock(code).then(d => { setAnalysisData(d); setLoading(false); }).catch(e => { setAnalysisData({ error: e.message }); setLoading(false); });
    }, 800);
    return () => clearTimeout(timer);
  }, [code]);

 const handleModelSwitch = useCallback((model) => {
    setSelectedModel(model);
    setModelName(model);
    fetch('/api/model/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    }).then(r => r.json()).then(d => { if (d.error) alert('切换失败: ' + d.error); }).catch(() => alert('切换失败'));
  }, []);

  const doAnalyze = useCallback((c) => {
    const targetCode = c || code;
    if (!targetCode) return;
    setLoading(true);
    setAnalysisData(null);
    analyzeStock(targetCode).then(d => { setAnalysisData(d); setLoading(false); }).catch(e => { setAnalysisData({ error: e.message }); setLoading(false); });
  }, [code]);


  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-left">
          <LogoMark />
        </div>
        <div className="header-right">
          <div className="ai-badge">
            <div className={`dot ${aiOnline ? 'online' : 'offline'}`} />
            <span>{aiOnline ? 'AI在线 · ' : 'AI离线'}</span>
            {aiOnline && models.length > 0 && (
              <select value={selectedModel} onChange={e => handleModelSwitch(e.target.value)}
                style={{ background: 'transparent', border: 'none', color: 'inherit', fontSize: 'inherit', cursor: 'pointer', outline: 'none', maxWidth: '200px' }}
                title="切换模型">
                {models.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            )}
          </div>
          <span style={{color: 'var(--text-muted)'}}>{new Date().toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
          <div className={`market-pill ${marketState.open ? 'open' : 'closed'}`}>
            <div className="market-pill-dot" />
            {marketState.message}
          </div>
          <button
            className="btn btn-ghost"
            style={{ padding: '4px 10px', fontSize: '12px', color: 'var(--text-muted)', border: '1px solid var(--border)', borderRadius: '6px' }}
            onClick={() => { setBotModelPending(botModel); setBotSettingsOpen(true); }}
            title="沈万三设置"
          >
            ⚙️ 沈万三
          </button>
        </div>
      </header>

      {/* Index bar */}
      <IndexBar />

      {/* Body */}
      <div className="main-content">
        <div className="sidebar">
          <Sidebar code={code} setCode={setCode} tCode={code} setTCode={setCode} action={action} setAction={setAction} onAnalyze={doAnalyze} aiOnline={aiOnline} marketState={marketState} signalMode={signalMode} setSignalMode={setSignalMode} />
        </div>

        <div className="content">
          {/* Detailed stock dashboard — always visible above the chart */}
          {code && <StockInfoCard code={code} />}

          {/* Inline AI analyzing badge — non-blocking */}
          {loading && (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px',
              padding: '12px 16px', borderRadius: '10px', border: '1px solid var(--border)',
              background: '#3b82f614', color: 'var(--blue)', fontSize: '12px', fontWeight: 500,
              marginBottom: '12px'
            }}>
              <div style={{
                width: '18px', height: '18px',
                border: '2px solid var(--border)', borderTopColor: 'var(--blue)',
                borderRadius: '50%', animation: 'spin 0.8s linear infinite', flexShrink: 0
              }} />
              AI 分析中...
            </div>
          )}

          {/* K线 chart — always visible when code is selected */}
          {code && (
            <div className="card">
              <div className="card-header">
                <span className="card-title"><ChartIcon />K线走势</span>
                <button id="rule-popup-btn" className="rule-btn" onClick={e => { e.stopPropagation(); setRuleAnchor(e.currentTarget.getBoundingClientRect().toJSON()); setRuleVisible(v => !v); }}>
                  <RuleIcon />交易规则
                </button>
              </div>
              {ruleVisible && (
                <RulePopover anchorRect={ruleAnchor} onClose={() => setRuleVisible(false)} />
              )}
              <div className="card-body kline-body">
                <StockChart code={code} />
              </div>
            </div>
          )}

          {/* Analysis result or empty state — previous result stays visible during new analysis */}
          {analysisData ? (
            <div>
              {loading && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: '8px',
                  padding: '10px 14px', borderRadius: '10px',
                  background: '#3b82f610', border: '1px solid #3b82f630',
                  color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '10px'
                }}>
                  <div style={{
                    width: '14px', height: '14px',
                    border: '2px solid var(--border)', borderTopColor: 'var(--blue)',
                    borderRadius: '50%', animation: 'spin 0.8s linear infinite', flexShrink: 0
                  }} />
                  正在重新分析，保留上次结果...
                </div>
              )}
              <AnalysisResult data={analysisData} code={code} />
            </div>
          ) : !loading && (
            <div className="empty">
                <svg className="empty-icon" viewBox="0 0 64 64" fill="none">
                  <rect x="8" y="40" width="10" height="16" rx="2" fill="currentColor" opacity="0.3"/>
                  <rect x="22" y="28" width="10" height="28" rx="2" fill="currentColor" opacity="0.5"/>
                  <rect x="36" y="16" width="10" height="40" rx="2" fill="currentColor" opacity="0.7"/>
                  <rect x="50" y="24" width="10" height="32" rx="2" fill="currentColor"/>
                  <path d="M4 56 L14 36 L28 46 L42 20 L58 28" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                <div className="empty-title">输入股票代码开始分析</div>
                <div className="empty-sub text-muted">支持沪深A股，代码如 600519、000001</div>
              </div>
          )}
        </div>

        <div className="right-panel">
          <RightPanel onSelect={code => { setCode(code); setAction("sell"); }} />
        </div>

        {/* 沈万三设置弹窗 */}
        {botSettingsOpen && (
          <div className="modal-overlay" onClick={() => setBotSettingsOpen(false)}>
            <div className="modal" onClick={e => e.stopPropagation()}>
              <div className="modal-header">
                <div className="modal-title">沈万三 · 模型设置</div>
                <button className="btn btn-ghost" style={{padding:'4px'}} onClick={() => setBotSettingsOpen(false)}>✕</button>
              </div>
              <div className="modal-body">
                <div style={{marginBottom:'12px',fontSize:'12px',color:'var(--text-muted)'}}>当前模型：<span style={{color:'var(--blue)',fontWeight:600}}>{botModel}</span></div>
                <div className="label">切换模型</div>
                <select
                  className="sel"
                  value={botModelPending}
                  onChange={e => setBotModelPending(e.target.value)}
                  style={{width:'100%',marginBottom:'12px'}}
                >
                  {models.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
                <div style={{fontSize:'11px',color:'var(--text-muted)',marginBottom:'12px'}}>
                  切换后沈万三下次分析自动生效（已运行的分析不受影响）
                </div>
              </div>
              <div className="modal-footer">
                <button className="btn btn-secondary" onClick={() => setBotSettingsOpen(false)}>取消</button>
                <button
                  className="btn btn-primary"
                  disabled={botSaving || botModelPending === botModel}
                  onClick={async () => {
                    if (botModelPending === botModel) return;
                    setBotSaving(true);
                    try {
                      await setBotModel(botModelPending);
                      setBotModel(botModelPending);
                      setBotSettingsOpen(false);
                    } catch(e) {
                      alert('设置失败: ' + e.message);
                    } finally {
                      setBotSaving(false);
                    }
                  }}
                >
                  {botSaving ? '保存中...' : '保存'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
