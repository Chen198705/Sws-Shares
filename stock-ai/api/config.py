import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:8000")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "sk-placeholder")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "Qwen3.6-35B-A3B-4bit")

STOCK_CODES = ["600519", "000001", "600036", "601318", "000858", "300750", "002475"]
INDEX_CODES = ["上证指数", "深证成指", "创业板指"]
SCAN_INTERVAL_MINUTES = 15
INITIAL_CASH = 1_000_000.0
STOP_LOSS_PCT = -5.0
TAKE_PROFIT_PCT = 15.0
REPORT_DIR = Path(__file__).parent / "reports"
ENABLE_AUTO_TRADE = False
TRADING_PLAN = "conservative"
