import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:8000")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "sk-placeholder")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "Qwen3.6-35B-A3B-4bit")

# /v1/models 未列出但 oMLX 控制台已启用的模型（逗号分隔）。
# 解决 oMLX 控制台 UI 与 /v1/models API 状态不一致的问题。
# 额外注入的 LLM（控制台启用但 /v1/models 未列出，逗号分隔）
EXTRA_LLM_MODELS = [m.strip() for m in os.getenv("EXTRA_LLM_MODELS", "").split(",") if m.strip()]
# 不在选择列表里显示的 LLM（控制台仍启用但前端不希望暴露，逗号分隔）
HIDE_LLM_MODELS = [m.strip() for m in os.getenv("HIDE_LLM_MODELS", "").split(",") if m.strip()]

STOCK_CODES = ["600519", "000001", "600036", "601318", "000858", "300750", "002475"]
INDEX_CODES = ["上证指数", "深证成指", "创业板指"]
SCAN_INTERVAL_MINUTES = 15
INITIAL_CASH = 1_000_000.0
STOP_LOSS_PCT = -5.0
TAKE_PROFIT_PCT = 15.0
REPORT_DIR = Path(__file__).parent / "reports"
ENABLE_AUTO_TRADE = False
TRADING_PLAN = "conservative"
