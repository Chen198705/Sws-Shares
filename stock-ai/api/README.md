# A股 AI 交易系统

本地 AI 驱动的 A 股分析 + 信号生成平台，数据来自新浪财经，AI 运行在 Mac Studio 上。

## 快速开始

### 1. 激活环境
```bash
cd /Users/chenjianhui/AI/stock-ai
source .venv/bin/activate
```

### 2. 检查 AI 连接
```bash
python main.py check-ai
```

### 3. Web UI（推荐）
```bash
streamlit run app.py
# 打开 http://localhost:8501
```

### 4. 命令行模式
```bash
python main.py show-indices      # 行情
python main.py analyze-market    # 大盘分析
python main.py analyze-stock 600519  # 个股分析
python main.py trade 300750     # 分析+信号
```

### 5. 定时任务
```bash
python scheduler.py --once       # 单次
python scheduler.py             # 守护进程（每小时）
python scheduler.py --cron      # JSON输出（供n8n调用）
```

## 配置

编辑 `.env` 文件：
```
OLLAMA_BASE_URL=http://127.0.0.1:8000
OLLAMA_API_KEY=sk-placeholder
OLLAMA_MODEL=qwen2.5:14b
ENABLE_AUTO_TRADE=false   # 开启前务必确认策略已验证
TRADING_PLAN=moderate
```

## 监控股票列表

`scheduler.py` 中的 `WATCH_LIST`，默认包含茅台、宁德时代、平安银行、五粮液、中国平安。

## 架构

```
market_data.py   - 数据层（新浪/腾讯接口）
ai_client.py     - AI 调用（OpenAI兼容API）
analyzer.py      - Prompt 组织
trader.py        - 交易信号生成
main.py          - CLI 入口
app.py           - Streamlit Web UI
scheduler.py     - 定时调度
```

## 下一步

- [ ] Mac Studio 启动 oLLX 服务
- [ ] 接入聚宽模拟盘验证策略
- [ ] 稳定后接入实盘
- [ ] 设定定时任务（交易时段每小时运行）
