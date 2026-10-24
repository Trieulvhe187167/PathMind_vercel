"""Regression coverage for chat language selection and contradictory prompts."""
from types import SimpleNamespace

import pytest

from pathmind.agents.loop import pipeline as loop_pipeline
from pathmind.agents.loop.prompt_blocks import LoopPromptAssembler
from pathmind.services.prompt.language import language_directive


@pytest.mark.parametrize("language", ["vi", "vi-VN", "vietnamese"])
def test_chat_pipeline_keeps_vietnamese_through_prompt_assembly(monkeypatch, language):
    monkeypatch.setattr(loop_pipeline, "get_llm_config", lambda: SimpleNamespace(model="test-model"))
    monkeypatch.setattr(loop_pipeline, "get_tool_registry", lambda: {})
    monkeypatch.setattr(loop_pipeline, "get_chat_params", lambda: {})
    pipeline = loop_pipeline.AgenticLoopPipeline(language=language)
    assert pipeline.language == "vi"
    assert pipeline._prompts  # English prompt pack fallback still loads.
    assert pipeline._prompt_assembler.language == "vi"
    prompt = pipeline._prompt_assembler.render([])
    assert "default output language is Vietnamese" in prompt
    assert "Write ALL reader-facing text" not in prompt
    assert "Do NOT switch languages" not in prompt


@pytest.mark.parametrize("language,label", [("en", "English"), ("zh", "中文"), ("vi", "Vietnamese")])
def test_chat_uses_adaptive_language_policy(language, label):
    prompt = LoopPromptAssembler(prompts={}, language=language).render([])
    assert label in prompt
    assert "user explicitly asks" in prompt
    assert "answer a Vietnamese message in Vietnamese" in prompt
    assert "quoted documents, tool results" in prompt
    assert "Write ALL reader-facing text" not in prompt
    assert "Do NOT switch languages" not in prompt


@pytest.mark.parametrize("language", ["en", "vi", "zh"])
def test_generated_artifacts_keep_strict_language(language):
    prompt = language_directive(language)
    assert "Conversation language" not in prompt
    assert "Do NOT switch languages" in prompt or "不得切换语言" in prompt


@pytest.mark.parametrize("allow_user_override", [True, False])
@pytest.mark.parametrize("language", ["vi", "vi-VN", "en", "zh"])
def test_vietnamese_quality_depends_on_selected_output_language(language, allow_user_override):
    prompt = language_directive(language, allow_user_override=allow_user_override)
    assert ("[Vietnamese writing quality]" in prompt) == language.startswith("vi")
    if language.startswith("vi"):
        assert "regardless of the topic" in prompt
        assert "missing or misplaced tone marks" in prompt
    assert "proofreading or tutoring" not in prompt
    assert "learner's original text" not in prompt
