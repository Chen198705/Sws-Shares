import ReactECharts from 'echarts-for-react';
import { useEffect, useState } from 'react';
import { getHistory } from '../api';

const FREQS = [
  { key: '5m',  label: '5分钟' },
  { key: '15m', label: '15分钟' },
  { key: '30m', label: '30分钟' },
  { key: '60m', label: '60分钟' },
  { key: 'day', label: '日线' },
  { key: 'week', label: '周线' },
  { key: 'month', label: '月线' },
];

function getMA(closes, n) {
  return closes.map((_, i) => {
    if (i < n - 1) return null;
    return closes.slice(i - n + 1, i + 1).reduce((a, b) => a + b, 0) / n;
  });
}

function xLabel(dateStr, freq) {
  if (!dateStr) return '';
  if (freq === 'day') return dateStr.slice(5, 10);      // MM-DD
  if (freq === 'week') return dateStr.slice(5, 10);
  if (freq === 'month') return dateStr.slice(0, 7);     // YYYY-MM
  return dateStr.slice(11, 16);                         // HH:MM
}

export default function StockChart({ code }) {
  const [freq, setFreq] = useState('day');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!code) return;
    setLoading(true);
    setData(null);
    getHistory(code, 240, freq)
      .then(d => { setData(d); setLoading(false); })
      .catch(() => { setData(null); setLoading(false); });
  }, [code, freq]);

  // Determine date format for x-axis
  const dates = (data?.history || []).map(d => xLabel(d.date, freq));
  const closes = (data?.history || []).map(d => d.close);
  const highs = (data?.history || []).map(d => d.high);
  const lows = (data?.history || []).map(d => d.low);
  const volumes = (data?.history || []).map(d => d.volume);
  const rawDates = (data?.history || []).map(d => d.date);

  const ma5 = getMA(closes, 5);
  const ma20 = getMA(closes, 20);

  const tooltipFormatter = params => {
    const p = params.find(x => x.seriesType === 'candlestick');
    if (!p) return '';
    const raw = rawDates[params[0].dataIndex];
    return `<b>${raw}</b><br/>开: ${p.data[1]}  收: ${p.data[2]}<br/>高: ${p.data[3]}  低: ${p.data[4]}`;
  };

  const option = {
    backgroundColor: 'transparent',
    animation: false,
    grid: [
      { left: 60, right: 12, top: 10, height: '65%' },
      { left: 60, right: 12, top: '78%', height: '15%' },
    ],
    xAxis: [
      {
        type: 'category', data: dates, gridIndex: 0,
        axisLine: { lineStyle: { color: '#30363d' } },
        axisLabel: { color: '#8b949e', fontSize: 10 },
        splitLine: { show: false },
      },
      {
        type: 'category', data: dates, gridIndex: 1,
        axisLine: { lineStyle: { color: '#30363d' } },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true, gridIndex: 0,
        axisLine: { show: false },
        axisLabel: { color: '#8b949e', fontSize: 10 },
        splitLine: { lineStyle: { color: '#21262d', type: 'dashed' } },
      },
      {
        scale: true, gridIndex: 1,
        axisLine: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', crossStyle: { color: '#484f58' } },
      backgroundColor: '#1c2128', borderColor: '#30363d',
      textStyle: { color: '#e6edf3', fontSize: 12 },
      formatter: tooltipFormatter,
    },
    series: [
      {
        name: 'K线', type: 'candlestick',
        data: (data?.history || []).map(d => [d.open, d.close, d.low, d.high]),
        xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: {
          color: '#f85149', color0: '#3fb950',
          borderColor: '#f85149', borderColor0: '#3fb950',
        },
      },
      {
        name: 'MA5', type: 'line', data: ma5,
        xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none',
        lineStyle: { color: '#f0883e', width: 1 },
      },
      {
        name: 'MA20', type: 'line', data: ma20,
        xAxisIndex: 0, yAxisIndex: 0, smooth: true, symbol: 'none',
        lineStyle: { color: '#58a6ff', width: 1 },
      },
      {
        name: '成交量', type: 'bar',
        data: volumes.map((v, i) => ({
          value: v,
          itemStyle: {
            color: (data?.history || [])[i]?.close >= (data?.history || [])[i]?.open
              ? 'rgba(248,81,73,0.6)' : 'rgba(63,185,80,0.6)',
          },
        })),
        xAxisIndex: 1, yAxisIndex: 1,
      },
    ],
  };

  return (
    <div>
      {/* Freq switcher */}
      <div style={{
        display: 'flex', gap: '4px', marginBottom: '10px', flexWrap: 'wrap',
      }}>
        {FREQS.map(f => (
          <button
            key={f.key}
            onClick={() => setFreq(f.key)}
            style={{
              padding: '3px 10px', fontSize: '11px', fontWeight: 600,
              borderRadius: '6px', border: '1px solid',
              cursor: 'pointer', transition: 'all 0.15s',
              background: freq === f.key ? 'var(--blue)' : 'transparent',
              borderColor: freq === f.key ? 'var(--blue)' : 'var(--border)',
              color: freq === f.key ? '#fff' : 'var(--text-muted)',
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Chart */}
      <div className="chart-wrap">
        {loading ? (
          <div className="loading"><div className="spinner" />加载中...</div>
        ) : !data?.history?.length ? (
          <div className="loading">暂无数据</div>
        ) : (
          <ReactECharts option={option} notMerge style={{ height: '100%' }} />
        )}
      </div>
    </div>
  );
}
