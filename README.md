# A股 AI 量化交易系统 · Docker 部署

## 架构

```
┌─────────────────────────────────────────────────────────┐
│  docker compose                                          │
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
│  │  shenwansan-bot   │   自动选股 / 操盘 / 汇报            │
│  └──────────────────┘                                     │
└─────────────────────────────────────────────────────────┘
```

- **API**：Web UI（React）+ REST API，同一进程提供静态文件 + 数据接口
- **Bot**：独立进程，调用 API 获取分析结果，执行买入/卖出/风控逻辑
- **oMLX**：外部模型推理服务（Studio Mac），容器内直连，无需打包
- **数据源**：腾讯/新浪行情，无需额外数据服务
- **券商**：默认模拟交易（SQLite 持久化）

## 快速开始

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

## 端口

| 端口 | 服务 |
|------|------|
| 5168 | Web UI + API |

浏览器打开 `http://<host>:5168`

## 数据持久化

| Docker Volume | 内容 |
|---------------|------|
| `shenwansan-data` | 模拟仓数据库、成交记录 |
| `shenwansan-logs` | API + Bot 日志 |

## 模型切换

API 端模型通过 `.env` 的 `OLLAMA_MODEL` 控制；
前端 Dashboard 支持运行时动态切换（不影响 Bot 行为）。

## 注意事项

- 本机 Mac Studio 上已有稳定运行的服务（直接 `python` 启动），
  Docker 部署适合迁移到新机器或做环境隔离。
- Bot 使用 `restart: unless-stopped`，重启后自动恢复选股逻辑。
- 非交易时段（收盘/周末）Bot 自动休眠，不执行任何操作。
