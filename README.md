# A股 AI 量化交易系统 · Docker 部署

> 三层架构（研究层 → 信号层 → 执行层）设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│  docker compose / LaunchAgent (macOS)                    │
│                                                         │
│  ┌──────────────────┐    ┌───────────────────────────────┐  │
│  │  shenwansan-api  │◄──►│  oMLX (Studio Mac :8000)      │  │
│  │  :5168           │    └───────────────────────────────┘  │
│  │  Web UI + API    │                                       │
│  └──────────────────┘                                       │
│           ▲            ▲                                    │
│           │ HTTP       │ 研究层契约（只读）                  │
│           ▼            │                                    │
│  ┌──────────────────┐  │                                    │
│  │ shenwansan-trading│ │  自动选股 / 操盘 / 汇报             │
│  └──────────────────┘  │                                    │
│  ┌──────────────────┐  │                                    │
│  │shenwansan-research│─┘  日更 regime/拥挤度/契约/归因       │
│  └──────────────────┘                                        │
└─────────────────────────────────────────────────────────┘
```

- **API**：Web UI（React）+ REST API，同一进程提供静态文件 + 数据接口
- **Trading**：独立进程，调用 API 获取分析结果，执行买入/卖出/风控逻辑
- **Research**：独立进程，工作日 15:35 刷新 regime / 因子拥挤度 / 策略契约 / 平仓归因，
  通过 `research/export/strategy_params.json` 以只读契约注入信号层
- **oMLX**：外部模型推理服务（Studio Mac），容器内直连，无需打包
- **数据源**：腾讯/新浪行情，无需额外数据服务
- **券商**：默认模拟交易（SQLite 持久化）

## 部署方式

### 方式一：Docker（跨平台）

```bash
# 1. 复制并编辑环境变量
cp .env.example .env
# 编辑 .env，填入 OLLAMA_BASE_URL 和 OLLAMA_API_KEY

# 2. 启动（首次自动构建镜像）
docker compose up -d

# 3. 查看日志
docker compose logs -f

# 4. 停止
docker compose down
```

### 方式二：macOS LaunchAgent（Studio Mac 推荐）

将 `docker/` 下三个 plist 复制到 `~/Library/LaunchAgents/`，然后加载：

```bash
# 复制 plist
cp docker/com.shenwansan.api.plist ~/Library/LaunchAgents/
cp docker/com.shenwansan.trading.plist ~/Library/LaunchAgents/
cp docker/com.shenwansan.research.plist ~/Library/LaunchAgents/

# 加载（自动 RunAtLoad，开机自启）
launchctl load ~/Library/LaunchAgents/com.shenwansan.api.plist
launchctl load ~/Library/LaunchAgents/com.shenwansan.trading.plist
launchctl load ~/Library/LaunchAgents/com.shenwansan.research.plist

# 查看状态
launchctl list | grep shenwansan

# 重启服务
launchctl unload ~/Library/LaunchAgents/com.shenwansan.api.plist
launchctl load ~/Library/LaunchAgents/com.shenwansan.api.plist
```

`shenwansan-research` 不常驻，`RunAtLoad=false`，由 launchd 在工作日 15:35 触发
`research/daily_refresh.py`（regime → 拥挤度 → 契约 → 归因），手动验证可运行：

```bash
launchctl kickstart -k gui/$(id -u)/com.shenwansan.research
```

## 端口

| 端口 | 服务 |
|------|------|
| 5168 | Web UI + API |

浏览器打开 `http://<host>:5168`

## 数据持久化

| 路径 | 内容 |
|------|------|
| `stock-ai/api/logs/` | API + Trading 日志 |
| `stock-ai/api/reports/` | 模拟仓数据库、成交记录 |
| `research/export/` | 策略契约（regime / factor / policy / risk） |
| `research/attribution/reports/` | 平仓归因聚合结果 |
| `research/logs/` | 研究层日更日志 |

## 三层落地校验

```bash
cd /Users/chenjianhui/AI/Sws-Shares
PYTHONPATH=stock-ai/api/.venv/lib/python3.9/site-packages:$PWD \
  stock-ai/api/.venv/bin/python research/verify_landing.py
```

校验研究层契约版本、16 因子约束、`value_bp` 权重、政策因子登记（权重 0）、
风险上限、周期权重、短线止损止盈映射、报告调度与飞书直连等落地项。

## 模型切换

API 端默认模型通过 `.env` 的 `OLLAMA_MODEL` 控制；
前端 Dashboard 支持运行时动态切换（不影响 Trading Bot 行为）。

## 注意事项

- Trading Bot 使用 `restart: unless-stopped`，重启后自动恢复选股逻辑。
- 非交易时段（收盘/周末）Bot 自动休眠，不执行任何操作。
- 模拟仓初始资金 100 万，真实交易需另行配置券商。
