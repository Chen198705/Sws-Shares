import json

import model_filter


def test_default_filter_drops_non_chat_models():
    models = [
        "Qwen3.6-35B-A3B-4bit",
        "GLM-OCR-bf16",
        "Qwen2.5-VL-7B-Instruct-4bit",
        "Qwen3-Embedding-0.6B-4bit-DWQ",
        "agnes-image-2.5-flash",
        "agnes-video-2.5",
        "whisper-large-v3-turbo-asr-fp16",
    ]

    kept, dropped = model_filter.filter_models(models)

    assert kept == ["Qwen3.6-35B-A3B-4bit"]
    assert {row["model"] for row in dropped} == {
        "GLM-OCR-bf16",
        "Qwen2.5-VL-7B-Instruct-4bit",
        "Qwen3-Embedding-0.6B-4bit-DWQ",
        "agnes-image-2.5-flash",
        "agnes-video-2.5",
        "whisper-large-v3-turbo-asr-fp16",
    }


def test_json_allow_and_hide_rules_override_defaults(monkeypatch, tmp_path):
    config_path = tmp_path / "model_filter.json"
    config_path.write_text(json.dumps({
        "allow": ["GLM-OCR-bf16"],
        "hide": ["Qwen3.6-35B-A3B-4bit"],
        "exclude_keywords": ["customvision"],
    }))
    monkeypatch.setattr(model_filter, "_CONFIG_PATH", config_path)
    monkeypatch.setattr(model_filter, "_cache", {"mtime": None, "cfg": None})

    kept, dropped = model_filter.filter_models([
        "GLM-OCR-bf16",
        "Qwen3.6-35B-A3B-4bit",
        "customvision-v1",
    ])

    assert kept == ["GLM-OCR-bf16"]
    assert dropped == [
        {"model": "Qwen3.6-35B-A3B-4bit", "reason": "hide"},
        {"model": "customvision-v1", "reason": "keyword:customvision"},
    ]
