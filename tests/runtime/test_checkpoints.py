from copy import deepcopy

import pytest

from minimal_agent.errors import SessionStoreError
from minimal_agent.models import (
    AgentRunStatus,
    ConversationMessage,
    LLMResponseType,
    LLMResult,
    MessageRole,
    SessionState,
    ToolCall,
)
from tests.runtime.fakes import ScriptedFakeLLM
from tests.runtime.test_runtime import runtime


def final(text: str = "Done") -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=text,
        ),
        response_type=LLMResponseType.FINAL,
    )


def tools(response_id: str, *calls: ToolCall) -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=list(calls),
        ),
        response_type=LLMResponseType.TOOL_CALLS,
        provider_response_id=response_id,
    )


def call(call_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments_json=arguments)


class RecordingCheckpoint:
    def __init__(self, *, fail_on: int | None = None) -> None:
        self.fail_on = fail_on
        self.snapshots: list[SessionState] = []

    async def __call__(self, state: SessionState) -> None:
        self.snapshots.append(deepcopy(state))
        if len(self.snapshots) == self.fail_on:
            raise SessionStoreError("checkpoint failed")


def roles(state: SessionState) -> list[MessageRole]:
    return [message.role for message in state.history]


@pytest.mark.asyncio
async def test_final_turn_checkpoints_user_then_final() -> None:
    fake = ScriptedFakeLLM([final("Hello")])
    checkpoint = RecordingCheckpoint()

    await runtime(fake).run(
        SessionState(user_id="u", session_id="s"),
        "Hi",
        checkpoint=checkpoint,
    )

    assert [roles(snapshot) for snapshot in checkpoint.snapshots] == [
        [MessageRole.USER],
        [MessageRole.USER, MessageRole.ASSISTANT],
    ]


@pytest.mark.asyncio
async def test_multiple_tools_share_one_batch_checkpoint() -> None:
    fake = ScriptedFakeLLM(
        [
            tools(
                "resp-1",
                call("calc", "calculator", '{"expression":"2+3"}'),
                call("todo", "todo", '{"action":"add","text":"Keep five"}'),
            ),
            final("Both done"),
        ]
    )
    checkpoint = RecordingCheckpoint()

    await runtime(fake).run(
        SessionState(user_id="u", session_id="s"),
        "Do both",
        checkpoint=checkpoint,
    )

    assert len(checkpoint.snapshots) == 3
    assert roles(checkpoint.snapshots[1]) == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.TOOL,
    ]
    assert checkpoint.snapshots[1].tool_state["todo"]["items"][0]["text"] == (
        "Keep five"
    )
    assert roles(checkpoint.snapshots[2])[-1] is MessageRole.ASSISTANT


@pytest.mark.asyncio
async def test_each_tool_round_has_exactly_one_checkpoint() -> None:
    fake = ScriptedFakeLLM(
        [
            tools("resp-1", call("c1", "calculator", '{"expression":"1+1"}')),
            tools("resp-2", call("c2", "calculator", '{"expression":"2+2"}')),
            final(),
        ]
    )
    checkpoint = RecordingCheckpoint()

    await runtime(fake).run(
        SessionState(user_id="u", session_id="s"),
        "Twice",
        checkpoint=checkpoint,
    )

    assert len(checkpoint.snapshots) == 4
    assert [len(snapshot.history) for snapshot in checkpoint.snapshots] == [1, 3, 5, 6]


@pytest.mark.asyncio
async def test_max_steps_checkpoints_last_batch_and_terminal() -> None:
    fake = ScriptedFakeLLM(
        [
            tools("resp-1", call("c1", "calculator", '{"expression":"1+1"}')),
            tools("resp-2", call("c2", "calculator", '{"expression":"2+2"}')),
        ]
    )
    checkpoint = RecordingCheckpoint()
    state = SessionState(user_id="u", session_id="s")

    result = await runtime(fake, max_steps=2).run(
        state,
        "Continue",
        checkpoint=checkpoint,
    )

    assert result.status is AgentRunStatus.MAX_STEPS
    assert len(fake.requests) == 2
    assert len(checkpoint.snapshots) == 4
    assert [len(snapshot.history) for snapshot in checkpoint.snapshots] == [1, 3, 5, 6]
    assert checkpoint.snapshots[-1].history[-1].content == runtime(
        ScriptedFakeLLM([])
    ).MAX_STEPS_MESSAGE


@pytest.mark.asyncio
async def test_failed_user_checkpoint_prevents_llm_call() -> None:
    fake = ScriptedFakeLLM([final()])
    checkpoint = RecordingCheckpoint(fail_on=1)

    with pytest.raises(SessionStoreError, match="checkpoint failed"):
        await runtime(fake).run(
            SessionState(user_id="u", session_id="s"),
            "Hi",
            checkpoint=checkpoint,
        )

    assert fake.requests == []


@pytest.mark.asyncio
async def test_failed_tool_batch_checkpoint_prevents_continuation() -> None:
    fake = ScriptedFakeLLM(
        [
            tools(
                "resp-1",
                call("todo", "todo", '{"action":"add","text":"Persist me"}'),
            ),
            final(),
        ]
    )
    checkpoint = RecordingCheckpoint(fail_on=2)
    state = SessionState(user_id="u", session_id="s")

    with pytest.raises(SessionStoreError, match="checkpoint failed"):
        await runtime(fake).run(state, "Add", checkpoint=checkpoint)

    assert len(fake.requests) == 1
    assert roles(state) == [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL]
    assert state.tool_state["todo"]["items"][0]["text"] == "Persist me"


@pytest.mark.asyncio
async def test_failed_final_checkpoint_does_not_return_completed_result() -> None:
    fake = ScriptedFakeLLM([final()])
    checkpoint = RecordingCheckpoint(fail_on=2)
    state = SessionState(user_id="u", session_id="s")

    with pytest.raises(SessionStoreError, match="checkpoint failed"):
        await runtime(fake).run(state, "Hi", checkpoint=checkpoint)

    assert len(fake.requests) == 1
    assert roles(state) == [MessageRole.USER, MessageRole.ASSISTANT]
