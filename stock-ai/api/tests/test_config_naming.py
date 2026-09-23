"""服务商命名回归：oMLX 是规范名，oMLX-Meta 是别名，OLLAMA_* 只做历史兼容。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import _env_first

_NAMES = ("OMLX_MODEL", "OMLX_META_MODEL", "OLLAMA_MODEL")


def test_omlx_name_wins_over_everything():
    env = {
        "OMLX_MODEL": "canonical",
        "OMLX_META_MODEL": "meta",
        "OLLAMA_MODEL": "legacy",
    }
    assert _env_first(*_NAMES, default="fallback", env=env) == "canonical"


def test_omlx_meta_alias_works():
    env = {"OMLX_META_MODEL": "meta", "OLLAMA_MODEL": "legacy"}
    assert _env_first(*_NAMES, default="fallback", env=env) == "meta"


def test_legacy_ollama_name_still_honored():
    env = {"OLLAMA_MODEL": "legacy"}
    assert _env_first(*_NAMES, default="fallback", env=env) == "legacy"


def test_blank_values_fall_through_to_default():
    env = {"OMLX_MODEL": "   ", "OMLX_META_MODEL": "", "OLLAMA_MODEL": ""}
    assert _env_first(*_NAMES, default="fallback", env=env) == "fallback"


def test_resolved_constants_use_omlx_names():
    import config

    for attr in ("OMLX_BASE_URL", "OMLX_API_KEY", "OMLX_MODEL"):
        assert getattr(config, attr), f"{attr} 不能为空"
    # 历史别名必须与规范名一致，避免老脚本读到两套值
    assert config.OLLAMA_BASE_URL == config.OMLX_BASE_URL
    assert config.OLLAMA_API_KEY == config.OMLX_API_KEY
    assert config.OLLAMA_MODEL == config.OMLX_MODEL


if __name__ == "__main__":
    for _name in (
        "test_omlx_name_wins_over_everything",
        "test_omlx_meta_alias_works",
        "test_legacy_ollama_name_still_honored",
        "test_blank_values_fall_through_to_default",
        "test_resolved_constants_use_omlx_names",
    ):
        globals()[_name]()
        print("PASS", _name)
