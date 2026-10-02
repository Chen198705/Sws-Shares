"""模型过滤：把与选股分析无关的模型从下拉列表里剔除。

oMLX 上同时挂着 chat LLM 和 Embedding / OCR / ASR / 视觉 / 图像 / 视频
等非文本模型，前端下拉只应出现能做文本推理的模型。

规则优先级（高到低）：
  1. allow        —— 白名单，命中即保留，覆盖后面所有规则
  2. hide         —— 精确名单，命中即剔除
  3. exclude_tokens    —— 分词后整词命中即剔除（如 "vl"、"image"）
  4. exclude_keywords  —— 子串命中即剔除（如 "embedding"、"bge-"）

默认规则见 DEFAULT_*；同目录的 model_filter.json 可以在不改代码、
不重启服务的前提下追加规则（每次调用都会检查文件 mtime）：

  {
    "allow": ["Qwen2.5-VL-7B-Instruct-4bit"],
    "hide": ["gpt-oss-20b-MXFP4-Q8"],
    "exclude_tokens": ["my-modality"],
    "exclude_keywords": ["my-custom-mod"]
  }
"""

import json
import re
import threading
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "model_filter.json"
_lock = threading.Lock()
_cache = {"mtime": None, "cfg": None}


# 分词整词匹配：模型名按非字母数字切分后，任一 token 命中即剔除。
# 这样 "Qwen2.5-VL-7B-Instruct-4bit" 会切出 "vl" 被拦下，
# 而像 "vlxx" 这种误伤不会发生。
DEFAULT_EXCLUDE_TOKENS = [
    # 多模态 / 视觉
    "vl", "vlm", "vision", "vit", "clip", "llava", "moondream",
    # 图像 / 视频 / 音频生成
    "image", "img", "video", "audio", "speech", "voice", "diffusion", "flux", "sdxl",
    # 向量 / 检索
    "embedding", "embed", "bge", "rerank",
    # 语音识别 / 合成
    "whisper", "asr", "tts",
    # 非文字识别
    "ocr",
    # 非 chat 架构（推测解码 / 多 token 预测）
    "dflash", "mtp", "mtplx",
]

# 子串匹配：没有天然分词边界的写法用这里兜底。
DEFAULT_EXCLUDE_KEYWORDS = [
    "embedding", "bge-", "whisper", "stable-diffusion",
    "-vl-", "-vl.", "dflash", "mtplx",
]


def _load_config():
    """读 model_filter.json，按 mtime 缓存；文件不存在或损坏则视为空配置。"""
    try:
        mtime = _CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _cache["mtime"] == mtime and _cache["cfg"] is not None:
            return _cache["cfg"]
        cfg = {}
        try:
            raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = raw
        except Exception:
            cfg = {}
        _cache["mtime"] = mtime
        _cache["cfg"] = cfg
        return cfg


def _as_list(cfg, key):
    value = cfg.get(key)
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _tokenize(name):
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def active_rules():
    """当前生效的完整规则集（默认 + json 追加），供诊断接口使用。"""
    cfg = _load_config()
    return {
        "allow": _as_list(cfg, "allow"),
        "hide": _as_list(cfg, "hide"),
        "exclude_tokens": DEFAULT_EXCLUDE_TOKENS + _as_list(cfg, "exclude_tokens"),
        "exclude_keywords": DEFAULT_EXCLUDE_KEYWORDS + _as_list(cfg, "exclude_keywords"),
        "source": str(_CONFIG_PATH) if _CONFIG_PATH.exists() else None,
    }


def explain(name):
    """返回 (是否保留, 原因)。原因用于诊断接口和日志。"""
    rules = active_rules()
    lowered = name.lower()

    if name in rules["allow"]:
        return True, "allow"
    if name in rules["hide"]:
        return False, "hide"

    tokens = set(_tokenize(name))
    for token in rules["exclude_tokens"]:
        if token.lower() in tokens:
            return False, f"token:{token}"

    for kw in rules["exclude_keywords"]:
        if kw.lower() in lowered:
            return False, f"keyword:{kw}"

    return True, ""


def filter_models(all_models):
    """返回 (保留列表, 剔除列表)，剔除项带原因。"""
    kept, dropped = [], []
    for name in all_models:
        keep, reason = explain(name)
        if keep:
            kept.append(name)
        else:
            dropped.append({"model": name, "reason": reason})
    return kept, dropped


def rule_summary():
    """过滤规则的可读摘要，供 /api/model-filter 展示。"""
    rules = active_rules()
    return {
        "allow": rules["allow"],
        "hide": rules["hide"],
        "exclude_tokens": sorted(set(rules["exclude_tokens"])),
        "exclude_keywords": sorted(set(rules["exclude_keywords"])),
        "config_file": rules["source"],
    }
