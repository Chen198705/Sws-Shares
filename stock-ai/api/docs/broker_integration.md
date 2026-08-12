# 券商接入指南

## 当前系统状态

模拟交易已完整实现并验证通过。真实券商接入只需三步：
1. 选择券商方案（下方）
2. 填写配置（BROKER_MODE + 凭证）
3. 设置 `ENABLE_AUTO_TRADE=true` 并重启

---

## 快速开始

### 方式一：模拟交易（无需开户，立即可用）

```bash
# 默认就是模拟交易模式
python3 main.py auto-trade 600519

# Web UI
./start.sh web
# 访问 http://localhost:8501
```

### 方式二：HTTP Webhook（最灵活）

任何提供 REST API 的服务都可以用这种方式接入：
- 聚宽 Mini Program API
- 自建下单服务
- 其他量化平台 API

```bash
export BROKER_MODE=webhook
export WEBHOOK_BASE_URL=https://your-api.com
export WEBHOOK_TOKEN=your_token
```

### 方式三：QMT 迅投（推荐实盘）

最主流的零售量化平台，支持 80+ 券商。

**前置条件：**
- Windows 或 macOS 虚拟机（QMT 无 Mac 原生版）
- 券商开通量化权限（一般找客户经理免费开通）

**步骤：**

1. 联系券商客户经理，开通 QMT 量化交易权限，获取：
   - 资金账号
   - 交易密码
   - 极简口令（6位数字）

2. Windows 上安装 QMT 客户端，设置极简口令

3. 配置环境变量：
   ```bash
   export BROKER_MODE=qmt
   export QMT_ACCOUNT=你的资金账号
   export QMT_PASSWORD=你的交易密码
   export QMT_COMBO=极简口令
   export QMT_COMBO_TYPE=STOCK
   export ENABLE_AUTO_TRADE=true
   ```

4. 安装 QMT Python API：
   ```bash
   pip install xtquant
   ```

5. 运行：
   ```bash
   python3 main.py auto-trade 600519
   ```

---

## 券商适配器对比

| 方案 | 接入难度 | Mac 支持 | 模拟盘 | 实盘 | 推荐场景 |
|------|---------|---------|--------|------|---------|
| simulation | ⭐ 无 | ✅ 原生 | ✅ 免费 | ❌ | 策略开发/回测 |
| webhook | ⭐⭐ | ✅ 原生 | 看 API | 看 API | 对接任意 REST API |
| QMT | ⭐⭐⭐⭐ | ❌ 需 VM | ❌ | ✅ | 真实交易首选 |

---

## 切换券商模式

```bash
# 模拟交易（默认）
BROKER_MODE=simulation ENABLE_AUTO_TRADE=true python3 main.py auto-trade 600519

# QMT 实盘
BROKER_MODE=qmt QMT_ACCOUNT=xxx QMT_PASSWORD=xxx QMT_COMBO=xxx \
  ENABLE_AUTO_TRADE=true python3 main.py auto-trade 600519

# Webhook
BROKER_MODE=webhook WEBHOOK_BASE_URL=https://api.example.com \
  WEBHOOK_TOKEN=xxx python3 main.py auto-trade 600519
```

---

## 安全建议

1. **API Key 安全**：凭证用环境变量，不要硬编码
2. **资金安全**：先用模拟盘验证策略至少 1 个月
3. **风控**：系统已有置信度 + 止损位，建议再加单笔最大仓位限制
4. **网络**：实盘确保网络稳定，建议有备用网络
5. **监控**：定期查看 `python3 main.py portfolio` 确认持仓正确
