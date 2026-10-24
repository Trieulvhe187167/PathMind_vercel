"""A printed mastery tool tag must not finish the turn without running a tool."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pathmind.agents.loop.agent_loop import AgentLoop, AgentLoopState, LLMCallResult
from pathmind.agents.loop.dsml_tool_calls import (
    extract_dsml_tool_calls,
    has_unexecuted_tool_markup,
)
from pathmind.agents.loop.pipeline import AgenticLoopPipeline
from pathmind.capabilities.mastery.loop import NATIVE_LOOP_FLAG, MasteryLoopCapability
from pathmind.capabilities.mastery.pipeline import MasteryLoopPipeline
from pathmind.core.context import UnifiedContext
from pathmind.runtime.agentic.tool_dispatch import DispatchOutcome
from pathmind.services.llm.exceptions import LLMConfigError


@pytest.mark.parametrize(
    "text",
    [
        "<tool_call> <mastery_status>",
        "<tool_call> <mastery_status",
        "Let me check the current path.\n<tool_call><mastery_status>",
        '<｜｜DSML｜｜invoke name="mastery_status">',
    ],
)
def test_detects_unexecuted_calls_from_the_screenshot(text):
    assert has_unexecuted_tool_markup(text, ["mastery_status"])


@pytest.mark.parametrize(
    "text",
    [
        "Your outline is ready.",
        "Call mastery_status first.",
        "<tool_call><unavailable_tool>",
        "`<tool_call><mastery_status>` is an XML example.",
        "```xml\n<tool_call><mastery_status>\n```",
        "~~~xml\n<tool_call><mastery_status>\n~~~",
        "```xml\n<tool_call><mastery_status>",
    ],
)
def test_does_not_reinterpret_answers_or_code_examples(text):
    assert not has_unexecuted_tool_markup(text, ["mastery_status"])


def test_only_this_turn_can_supply_a_tool_surface():
    assert not has_unexecuted_tool_markup("<tool_call><mastery_status>", [])


def test_complete_dsml_still_uses_the_existing_parser():
    calls, text = extract_dsml_tool_calls(
        '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="mastery_status">'
        "</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
    )
    assert len(calls) == 1
    assert calls[0]["name"] == "mastery_status"
    assert calls[0]["arguments"] == "{}"
    assert text == ""


def make_loop(results):
    pipeline = SimpleNamespace(
        event_source="mastery",
        event_stage="responding",
        binding="openai",
        model="test",
        initial_tool_choice=None,
        loop_max_tokens=1000,
        _t=lambda key, default="", **kwargs: default,
        effective_max_rounds=lambda context: 8,
        _capability_buffers_visible_output=lambda context: False,
        _capability_finish_instruction=lambda context, text: "",
        _capability_final_text_override=lambda context, text: None,
        _capability_tool_round_output_policy=lambda context, text, tools: "publish",
        _dispatch_tool_calls=AsyncMock(
            return_value=DispatchOutcome(
                tool_messages=[],
                sources=[],
                terminate=True,
                terminate_payload={"content": "Outline created."},
            )
        ),
        _emit_protocol_fallback_final_response=AsyncMock(),
        _emit_terminator_final_response=AsyncMock(),
        _finish_exhausted_instruction=lambda: "Finish without tools.",
    )
    loop = AgentLoop(
        pipeline=pipeline,
        context=SimpleNamespace(
            runtime=SimpleNamespace(previous_model_turn=None),
            capability_output=SimpleNamespace(answer_published=False),
        ),
        stream=SimpleNamespace(progress=AsyncMock()),
        client=None,
        enabled_tools=["mastery_status"],
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "mastery_status",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
    )
    loop._call_llm = AsyncMock(side_effect=results)
    return loop


def broken_round():
    return LLMCallResult(
        text="<tool_call><mastery_status",
        finish_reason="stop",
        completion_metadata={"call_id": "bad-call", "answer_visible": True},
    )


@pytest.mark.asyncio
async def test_retries_markup_then_dispatches_only_a_native_call():
    loop = make_loop(
        [
            broken_round(),
            LLMCallResult(
                text="",
                tool_calls=[
                    {
                        "id": "native-call",
                        "name": "mastery_status",
                        "arguments": "{}",
                    }
                ],
            ),
        ]
    )
    messages = [{"role": "user", "content": "Create my learning outline."}]
    state = AgentLoopState()
    outcome = await loop._run_loop(messages=messages, state=state, checkpoint_boundary=1)
    assert outcome.completed
    assert outcome.final_text == "Outline created."
    assert loop._call_llm.await_count == 2
    assert state.tool_steps == 1
    call = loop.pipeline._dispatch_tool_calls.call_args.kwargs["tool_calls"][0]
    assert call["id"] == "native-call"
    assert "No tool was executed" in messages[2]["content"]
    corrections = [call.kwargs.get("metadata", {}) for call in loop.stream.progress.call_args_list]
    assert any(meta.get("finish_rejected") and not meta["answer_visible"] for meta in corrections)


@pytest.mark.asyncio
async def test_repeated_malformed_calls_stop_after_one_recovery():
    loop = make_loop([broken_round(), broken_round()])
    outcome = await loop._run_loop(
        messages=[{"role": "user", "content": "Start"}],
        state=AgentLoopState(),
        checkpoint_boundary=1,
    )
    assert not outcome.completed
    assert "could not call the tools" in outcome.final_text
    assert "<tool_call>" not in outcome.final_text
    assert loop._call_llm.await_count == 2
    loop.pipeline._dispatch_tool_calls.assert_not_awaited()
    loop.pipeline._emit_protocol_fallback_final_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_normal_prose_is_not_retried():
    loop = make_loop([LLMCallResult(text="What is your current English level?")])
    outcome = await loop._run_loop(
        messages=[{"role": "user", "content": "Start"}],
        state=AgentLoopState(),
        checkpoint_boundary=1,
    )
    assert outcome.completed
    assert outcome.final_text == "What is your current English level?"
    assert loop._call_llm.await_count == 1


@pytest.mark.asyncio
async def test_provider_without_native_schemas_gets_complete_dsml_guidance():
    loop = make_loop([broken_round(), LLMCallResult(text="I cannot call tools.")])
    loop.tool_schemas = None
    messages = [{"role": "user", "content": "Start"}]
    await loop._run_loop(messages=messages, state=AgentLoopState(), checkpoint_boundary=1)
    assert "Native function calling is unavailable" in messages[2]["content"]
    assert "</invoke>" in messages[2]["content"]
    loop.pipeline._dispatch_tool_calls.assert_not_awaited()


@pytest.mark.asyncio
async def test_hard_finish_never_accepts_markup_as_success():
    loop = make_loop([broken_round()])
    outcome = await loop._forced_finish(
        [{"role": "user", "content": "Start"}],
        AgentLoopState(),
    )
    assert not outcome.completed
    loop.pipeline._dispatch_tool_calls.assert_not_awaited()


def test_mastery_rejects_a_promise_without_a_status_call():
    context = UnifiedContext(
        user_message="Tạo lộ trình học",
        metadata={
            "mastery_mode": True,
            NATIVE_LOOP_FLAG: True,
        },
    )
    cap = MasteryLoopCapability()
    instruction = cap.finish_instruction(context, "Tôi sẽ kiểm tra trạng thái lộ trình của bạn.")
    assert instruction and "Call mastery_status" in instruction
    cap.augment_kwargs("mastery_status", {}, context)
    assert cap.finish_instruction(context, "Bạn muốn ưu tiên giao tiếp hay ngữ pháp?") is None


@pytest.mark.asyncio
async def test_mastery_without_tool_support_fails_before_loading_user_data():
    pipeline = object.__new__(MasteryLoopPipeline)
    pipeline._can_use_native_tool_calling = lambda: False
    with pytest.raises(LLMConfigError, match="requires tool calling"):
        await pipeline.run(UnifiedContext(user_message="Start"), None)


@pytest.mark.asyncio
async def test_repeated_mastery_finish_rejection_has_a_visible_error():
    loop = make_loop([LLMCallResult(text="I will check."), LLMCallResult(text="I will check.")])
    loop.pipeline._capability_finish_instruction = lambda context, text: "Call mastery_status."
    outcome = await loop._run_loop(
        messages=[{"role": "user", "content": "Start"}],
        state=AgentLoopState(),
        checkpoint_boundary=1,
    )
    assert not outcome.completed
    assert outcome.final_text
    loop.pipeline._emit_protocol_fallback_final_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_keeps_authorized_names_when_native_schemas_are_unavailable(monkeypatch):
    captured = {}

    def make_agent(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run=AsyncMock(return_value={"completed": False}))

    monkeypatch.setattr("pathmind.agents.loop.pipeline.AgentLoop", make_agent)
    pipeline = object.__new__(AgenticLoopPipeline)
    pipeline._prepare_deferred_tools = AsyncMock()
    pipeline._prepare_kb_manifests = AsyncMock()
    pipeline._exec_allowed = AsyncMock(return_value=False)
    pipeline._compose_enabled_tools = lambda context: ["mastery_status"]
    pipeline._can_use_native_tool_calling = lambda: False
    pipeline._build_openai_client = lambda: None
    pipeline._tool_view = None
    context = UnifiedContext(user_message="Start")
    context.runtime.workspace = SimpleNamespace()
    await pipeline.run(context, None)
    assert captured["tool_schemas"] is None
    assert captured["enabled_tools"] == ["mastery_status"]
