import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()


def _env_first(*names, default="", env=None):
    """按优先级取第一个非空环境变量。

    服务商是 oMLX（跑在 Mac Studio 上），不是 Ollama。历史代码沿用了
    OLLAMA_* 这套名字，这里统一到 OMLX_*：
      1. OMLX_*        —— 规范名，新配置一律用这套
      2. OMLX_META_*   —— oMLX-Meta（平台聚合层）写法，同样认
      3. OLLAMA_*      —— 仅作历史兼容，不要再新增

    env 仅用于测试注入；默认读 os.environ。
    """
    source = os.environ if env is None else env
    for name in names:
        value = source.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


OMLX_BASE_URL = _env_first(
    "OMLX_BASE_URL", "OMLX_META_BASE_URL", "OLLAMA_BASE_URL",
    default="http://127.0.0.1:8000",
)
OMLX_API_KEY = _env_first(
    "OMLX_API_KEY", "OMLX_META_API_KEY", "OLLAMA_API_KEY",
    default="sk-placeholder",
)
OMLX_MODEL = _env_first(
    "OMLX_MODEL", "OMLX_META_MODEL", "OLLAMA_MODEL",
    default="Qwen3.6-35B-A3B-4bit",
)

# 历史别名：老脚本 / 老文档 import 这三个名字时仍然可用
OLLAMA_BASE_URL = OMLX_BASE_URL
OLLAMA_API_KEY = OMLX_API_KEY
OLLAMA_MODEL = OMLX_MODEL

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
