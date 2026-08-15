# A股 AI 量化交易系统 · Docker 部署

> 三层架构（研究层 → 信号层 → 执行层）设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│  docker compose / LaunchAgent (macOS)                    │
│                                                         │
│  ┌──────────────┐    ┌───────────────────────────────┐  │
│  │  shenwansan-api  │◄──►│  oMLX (Studio Mac :8000)  │  │
│  │  :5168           │    └───────────────────────────────┘  │
│  │  Web UI + API    │                                       │
│  └──────────────────┘                                       │
│           ▲                                                 │
│           │ HTTP                                            │
│           ▼                                                 │
│  ┌──────────────────┐                                     │
│  │  shenwansan-trading │   自动选股 / 操盘 / 汇报          │
│  └──────────────────┘                                     │
└─────────────────────────────────────────────────────────┘
```

- **API**：Web UI（React）+ REST API，同一进程提供静态文件 + 数据接口
- **Trading**：独立进程，调用 API 获取分析结果，执行买入/卖出/风控逻辑
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

将 `com.shenwansan.api.plist` 和 `com.shenwansan.trading.plist` 复制到 `~/Library/LaunchAgents/`，然后加载：

```bash
# 复制 plist
cp docker/com.shenwansan.api.plist ~/Library/LaunchAgents/
cp docker/com.shenwansan.trading.plist ~/Library/LaunchAgents/

# 加载（自动 RunAtLoad，开机自启）
launchctl load ~/Library/LaunchAgents/com.shenwansan.api.plist
launchctl load ~/Library/LaunchAgents/com.shenwansan.trading.plist

# 查看状态
launchctl list | grep shenwansan

# 重启服务
launchctl unload ~/Library/LaunchAgents/com.shenwansan.api.plist
launchctl load ~/Library/LaunchAgents/com.shenwansan.api.plist
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

## 模型切换

API 端默认模型通过 `.env` 的 `OLLAMA_MODEL` 控制；
前端 Dashboard 支持运行时动态切换（不影响 Trading Bot 行为）。

## 注意事项

- Trading Bot 使用 `restart: unless-stopped`，重启后自动恢复选股逻辑。
- 非交易时段（收盘/周末）Bot 自动休眠，不执行任何操作。
- 模拟仓初始资金 100 万，真实交易需另行配置券商。
