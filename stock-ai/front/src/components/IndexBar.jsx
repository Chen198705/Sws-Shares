import { useEffect, useState } from 'react';
import { getIndices } from '../api';

function fmtPrice(v) { return v != null ? v.toFixed(2) : '—'; }
function fmtPct(v) { return v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '—'; }

export default function IndexBar() {
  const [indices, setIndices] = useState({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getIndices().then(d => { setIndices(d); setLoading(false); }).catch(() => setLoading(false));
    const t = setInterval(() => getIndices().then(setIndices), 15000);
    return () => clearInterval(t);
  }, []);

  if (loading) return <div className="index-bar"><div className="loading"><div className="spinner" />加载中</div></div>;
  const entries = Object.entries(indices);
  return (
    <div className="index-bar">
      {entries.map(([name, d]) => {
        const pct = d?.涨跌幅 ?? 0;
        const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
        return (
          <div key={name} className={`index-card ${cls}`}>
            <div className="idx-name">{name}</div>
            <div className="idx-price">{fmtPrice(d?.最新价)}</div>
            <div className="idx-pct">{fmtPct(pct)}</div>
          </div>
        );
      })}
    </div>
  );
}
