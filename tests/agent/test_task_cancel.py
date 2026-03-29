"""Tests for /stop task cancellation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_loop(*, exec_config=None):
    """Create a minimal AgentLoop with mocked dependencies."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    workspace = MagicMock()
    workspace.__truediv__ = MagicMock(return_value=MagicMock())

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as MockSubMgr:
        MockSubMgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace, exec_config=exec_config)
    return loop, bus


class TestHandleStop:
    @pytest.mark.asyncio
    async def test_stop_no_active_task(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_stop
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)
        assert "No active task" in out.content

    @pytest.mark.asyncio
    async def test_stop_cancels_active_task(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_stop
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = [task]

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)

        assert cancelled.is_set()
        assert "stopped" in out.content.lower()

    @pytest.mark.asyncio
    async def test_stop_cancels_multiple_tasks(self):
        from nanobot.bus.events import InboundMessage
        from nanobot.command.builtin import cmd_stop
        from nanobot.command.router import CommandContext

        loop, bus = _make_loop()
        events = [asyncio.Event(), asyncio.Event()]

        async def slow(idx):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                events[idx].set()
                raise

        tasks = [asyncio.create_task(slow(i)) for i in range(2)]
        await asyncio.sleep(0)
        loop._active_tasks["test:c1"] = tasks

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw="/stop", loop=loop)
        out = await cmd_stop(ctx)

        assert all(e.is_set() for e in events)
        assert "2 task" in out.content


class TestDispatch:
    def test_exec_tool_not_registered_when_disabled(self):
        from nanobot.config.schema import ExecToolConfig

        loop, _bus = _make_loop(exec_config=ExecToolConfig(enable=False))

        assert loop.tools.get("exec") is None

    @pytest.mark.asyncio
    async def test_dispatch_processes_and_publishes(self):
        from nanobot.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
        loop._process_message = AsyncMock(
            return_value=OutboundMessage(channel="test", chat_id="c1", content="hi")
        )
        await loop._dispatch(msg)
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert out.content == "hi"

    @pytest.mark.asyncio
    async def test_same_session_serializes(self):
        """Messages from the same session must be processed one at a time."""
        from nanobot.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        order = []

        async def mock_process(m, **kwargs):
            order.append(f"start-{m.content}")
            await asyncio.sleep(0.05)
            order.append(f"end-{m.content}")
            return OutboundMessage(channel="test", chat_id="c1", content=m.content)

        loop._process_message = mock_process
        msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="a")
        msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="b")

        t1 = asyncio.create_task(loop._dispatch(msg1))
        t2 = asyncio.create_task(loop._dispatch(msg2))
        await asyncio.gather(t1, t2)
        assert order == ["start-a", "end-a", "start-b", "end-b"]

    @pytest.mark.asyncio
    async def test_different_sessions_run_concurrently(self):
        """Messages from different sessions must overlap (not be serialized)."""
        from nanobot.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        order = []

        async def mock_process(m, **kwargs):
            order.append(f"start-{m.content}")
            await asyncio.sleep(0.05)
            order.append(f"end-{m.content}")
            return OutboundMessage(channel="test", chat_id=m.chat_id, content=m.content)

        loop._process_message = mock_process
        # Two different chat_ids → two different session_keys
        msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="a")
        msg2 = InboundMessage(channel="test", sender_id="u2", chat_id="c2", content="b")

        t1 = asyncio.create_task(loop._dispatch(msg1))
        t2 = asyncio.create_task(loop._dispatch(msg2))
        await asyncio.gather(t1, t2)
        # Both should have started before either finished
        assert order[0].startswith("start-") and order[1].startswith("start-"), (
            f"Expected both to start before either ends, got: {order}"
        )

    @pytest.mark.asyncio
    async def test_new_inbound_message_interrupts_same_session(self):
        from nanobot.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        second_processed = asyncio.Event()

        async def mock_process(m, **kwargs):
            if m.content == "first":
                started.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            second_processed.set()
            return OutboundMessage(channel="test", chat_id="c1", content=f"done:{m.content}")

        loop._process_message = mock_process
        loop._connect_mcp = AsyncMock()

        run_task = asyncio.create_task(loop.run())
        try:
            await bus.publish_inbound(
                InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="first")
            )
            await asyncio.wait_for(started.wait(), timeout=1.0)

            await bus.publish_inbound(
                InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="second")
            )

            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert out.content == "done:second"
            assert second_processed.is_set()
            assert cancelled.is_set()
        finally:
            loop.stop()
            run_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run_task


class TestAgentLoopResume:
    @pytest.mark.asyncio
    async def test_recovered_from_error_injects_resume_and_continues(self, tmp_path):
        """When chat_with_retry signals recovered_from_error=True, the loop injects a
        resume user message and retries so the agent can continue with full context."""
        from nanobot.agent.loop import AgentLoop
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        call_count = {"n": 0}
        injected_messages: list[list] = []

        async def scripted_chat_with_retry(*, messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate: network blip happened but eventually recovered
                return LLMResponse(
                    content="任务完成", finish_reason="stop", recovered_from_error=True
                )
            # Second call (after resume injection): capture messages and return
            injected_messages.append(list(messages))
            return LLMResponse(content="继续完成", finish_reason="stop")

        provider.chat_with_retry = scripted_chat_with_retry

        with patch("nanobot.agent.loop.SubagentManager"):
            loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

        initial_messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "做点什么"},
        ]
        final_content, _, _ = await loop._run_agent_loop(initial_messages)

        assert call_count["n"] == 2, "Should have made a second call after resume injection"
        assert final_content == "继续完成"

        # The second call's messages should contain the injected resume prompt
        second_call_msgs = injected_messages[0]
        user_msgs = [m for m in second_call_msgs if m.get("role") == "user"]
        resume_msgs = [m for m in user_msgs if "继续" in (m.get("content") or "")]
        assert resume_msgs, f"Resume message not found in: {user_msgs}"

    @pytest.mark.asyncio
    async def test_no_resume_when_not_recovered(self, tmp_path):
        """Normal successful responses (recovered_from_error=False) must NOT inject resume."""
        from nanobot.agent.loop import AgentLoop
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        call_count = {"n": 0}

        async def scripted_chat_with_retry(*, messages, **kwargs):
            call_count["n"] += 1
            return LLMResponse(content="正常回复", finish_reason="stop")

        provider.chat_with_retry = scripted_chat_with_retry

        with patch("nanobot.agent.loop.SubagentManager"):
            loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

        initial_messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        final_content, _, _ = await loop._run_agent_loop(initial_messages)

        assert call_count["n"] == 1, "Normal response should complete in one call"
        assert final_content == "正常回复"

    @pytest.mark.asyncio
    async def test_permanent_error_breaks_immediately(self, tmp_path):
        """Permanent errors (401, invalid_key, etc.) must break immediately, no resume."""
        from nanobot.agent.loop import AgentLoop
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        call_count = {"n": 0}

        async def scripted_chat_with_retry(*, messages, **kwargs):
            call_count["n"] += 1
            return LLMResponse(content="401 unauthorized", finish_reason="error")

        provider.chat_with_retry = scripted_chat_with_retry

        with patch("nanobot.agent.loop.SubagentManager"):
            loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

        initial_messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        final_content, _, _ = await loop._run_agent_loop(initial_messages)

        assert call_count["n"] == 1, "Permanent error should break after first call"
        assert "401" in (final_content or "")


class TestSubagentConcurrencyAndTimeout:
    @pytest.mark.asyncio
    async def test_spawn_rejects_when_cap_reached(self):
        """spawn() returns an error string when MAX_CONCURRENT is already reached."""
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=MagicMock(), bus=bus, max_concurrent=2)

        # Fake two already-running tasks
        for i in range(2):
            task = asyncio.create_task(asyncio.sleep(60))
            mgr._running_tasks[f"fake-{i}"] = task

        result = await mgr.spawn("some task", session_key="test:c1")
        assert "concurrency limit" in result.lower()

        # Cleanup
        for t in mgr._running_tasks.values():
            t.cancel()
        await asyncio.gather(*mgr._running_tasks.values(), return_exceptions=True)

    @pytest.mark.asyncio
    async def test_subagent_timeout_announces_error(self, monkeypatch):
        """_run_subagent wraps inner with wait_for; timeout triggers error announcement."""
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(
            provider=provider, workspace=MagicMock(), bus=bus, timeout_s=1
        )

        async def _hang(*args, **kwargs):
            await asyncio.sleep(9999)

        monkeypatch.setattr(mgr, "_run_subagent_inner", _hang)

        announced: list[dict] = []

        async def _capture(*args, **kwargs):
            announced.append({"status": kwargs.get("status") or args[5]})

        monkeypatch.setattr(mgr, "_announce_result", _capture)

        await mgr._run_subagent("t1", "task", "label", {"channel": "test", "chat_id": "c1"})

        assert len(announced) == 1
        assert announced[0]["status"] == "error"


class TestSubagentCancellation:
    @pytest.mark.asyncio
    async def test_cancel_by_session(self):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=MagicMock(), bus=bus)

        cancelled = asyncio.Event()

        async def slow():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow())
        await asyncio.sleep(0)
        mgr._running_tasks["sub-1"] = task
        mgr._session_tasks["test:c1"] = {"sub-1"}

        count = await mgr.cancel_by_session("test:c1")
        assert count == 1
        assert cancelled.is_set()

    @pytest.mark.asyncio
    async def test_cancel_by_session_no_tasks(self):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=MagicMock(), bus=bus)
        assert await mgr.cancel_by_session("nonexistent") == 0

    @pytest.mark.asyncio
    async def test_subagent_preserves_reasoning_fields_in_tool_turn(self, monkeypatch, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"

        captured_second_call: list[dict] = []

        call_count = {"n": 0}

        async def scripted_chat_with_retry(*, messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return LLMResponse(
                    content="thinking",
                    tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
                    reasoning_content="hidden reasoning",
                    thinking_blocks=[{"type": "thinking", "thinking": "step"}],
                )
            captured_second_call[:] = messages
            return LLMResponse(content="done", tool_calls=[])
        provider.chat_with_retry = scripted_chat_with_retry
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        async def fake_execute(self, name, arguments):
            return "tool result"

        monkeypatch.setattr("nanobot.agent.tools.registry.ToolRegistry.execute", fake_execute)

        await mgr._run_subagent_inner("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})

        assistant_messages = [
            msg for msg in captured_second_call
            if msg.get("role") == "assistant" and msg.get("tool_calls")
        ]
        assert len(assistant_messages) == 1
        assert assistant_messages[0]["reasoning_content"] == "hidden reasoning"
        assert assistant_messages[0]["thinking_blocks"] == [{"type": "thinking", "thinking": "step"}]

    @pytest.mark.asyncio
    async def test_subagent_announces_error_when_tool_execution_fails(self, monkeypatch, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
            content="thinking",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
        ))
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr._announce_result = AsyncMock()

        calls = {"n": 0}

        async def fake_execute(self, name, arguments):
            calls["n"] += 1
            if calls["n"] == 1:
                return "first result"
            raise RuntimeError("boom")

        monkeypatch.setattr("nanobot.agent.tools.registry.ToolRegistry.execute", fake_execute)

        await mgr._run_subagent("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})

        mgr._announce_result.assert_awaited_once()
        args = mgr._announce_result.await_args.args
        assert "Completed steps:" in args[3]
        assert "- list_dir: first result" in args[3]
        assert "Failure:" in args[3]
        assert "- list_dir: boom" in args[3]
        assert args[5] == "error"

    @pytest.mark.asyncio
    async def test_cancel_by_session_cancels_running_subagent_tool(self, monkeypatch, tmp_path):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus
        from nanobot.providers.base import LLMResponse, ToolCallRequest

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
            content="thinking",
            tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={})],
        ))
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        mgr._announce_result = AsyncMock()

        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_execute(self, name, arguments):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr("nanobot.agent.tools.registry.ToolRegistry.execute", fake_execute)

        task = asyncio.create_task(
            mgr._run_subagent("sub-1", "do task", "label", {"channel": "test", "chat_id": "c1"})
        )
        mgr._running_tasks["sub-1"] = task
        mgr._session_tasks["test:c1"] = {"sub-1"}

        await started.wait()

        count = await mgr.cancel_by_session("test:c1")

        assert count == 1
        assert cancelled.is_set()
        assert task.cancelled()
        mgr._announce_result.assert_not_awaited()
