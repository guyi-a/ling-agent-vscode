"""Tests for computer-use effects, approval, and persistence."""

from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from ling_agent import (
    ApprovalMode,
    ComputerEffect,
    HarnessConfig,
    UnknownEffect,
    Workspace,
    approval_reason,
    derive_effect,
)
from ling_agent.approvals import ApprovalKind, _kind_for_effect
from ling_agent.effects import plan_mode_denial
from ling_agent.storage import _effect_from_dict
from ling_ai.types import ToolCall


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments=arguments)


def _workspace(root: Path) -> Workspace:
    return Workspace(root)


def test_readonly_computer_effects_do_not_prompt() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = _workspace(Path(temp))
        for name in ("computer_list", "computer_stop"):
            effect = derive_effect(_call(name, {}), workspace)
            assert isinstance(effect, ComputerEffect)
            assert effect.classification == "readonly"
            assert approval_reason(effect, ApprovalMode.MANUAL) is None
            assert plan_mode_denial(effect) is None
        screenshot = derive_effect(
            _call("computer_screenshot", {"window_id": "w-1"}),
            workspace,
        )
        assert isinstance(screenshot, ComputerEffect)
        assert screenshot.operation == "screenshot"
        assert screenshot.classification == "readonly"
        assert approval_reason(screenshot, ApprovalMode.MANUAL) is None
        assert plan_mode_denial(screenshot) is None
        missing_screenshot = derive_effect(
            _call("computer_screenshot", {}),
            workspace,
        )
        assert isinstance(missing_screenshot, UnknownEffect)
        observe = derive_effect(
            _call("computer_observe", {"window_id": "w-1"}),
            workspace,
        )
        assert isinstance(observe, ComputerEffect)
        assert observe.operation == "observe"
        assert approval_reason(observe, ApprovalMode.AUTO) is None


def test_mutating_computer_effect_requires_takeover() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = _workspace(Path(temp))
        effect = derive_effect(
            _call(
                "computer_act",
                {
                    "window_id": "w-1",
                    "actions": [{"type": "click", "ref": "@s:e1"}],
                },
            ),
            workspace,
        )
        assert isinstance(effect, ComputerEffect)
        assert effect.classification == "mutating"
        assert effect.action == "click"
        for mode in (
            ApprovalMode.MANUAL,
            ApprovalMode.ACCEPT_WRITE,
            ApprovalMode.AUTO,
        ):
            assert approval_reason(effect, mode) is not None
        assert plan_mode_denial(effect) is not None
        assert _kind_for_effect(effect) is ApprovalKind.COMPUTER


def test_launch_computer_effect_and_invalid_args() -> None:
    with tempfile.TemporaryDirectory() as temp:
        workspace = _workspace(Path(temp))
        launch = derive_effect(
            _call("computer_launch", {"app": "notepad.exe"}),
            workspace,
        )
        assert isinstance(launch, ComputerEffect)
        assert launch.classification == "launch"
        assert approval_reason(launch, ApprovalMode.AUTO) is not None
        focus = derive_effect(
            _call("computer_focus", {"window_id": "w-1"}),
            workspace,
        )
        assert isinstance(focus, ComputerEffect)
        assert focus.operation == "focus"
        assert focus.classification == "mutating"
        assert approval_reason(focus, ApprovalMode.AUTO) is not None
        assert plan_mode_denial(focus) is not None
        missing_window = derive_effect(
            _call("computer_observe", {}),
            workspace,
        )
        assert isinstance(missing_window, UnknownEffect)
        missing_focus = derive_effect(
            _call("computer_focus", {}),
            workspace,
        )
        assert isinstance(missing_focus, UnknownEffect)
        empty_actions = derive_effect(
            _call("computer_act", {"window_id": "w-1", "actions": []}),
            workspace,
        )
        assert isinstance(empty_actions, UnknownEffect)


def test_computer_effect_storage_round_trip() -> None:
    effect = ComputerEffect(
        operation="act",
        classification="mutating",
        window_id="w-1",
        app="notepad.exe",
        action="click",
    )
    restored = _effect_from_dict(asdict(effect))
    assert isinstance(restored, ComputerEffect)
    assert restored == effect


def test_computer_config_defaults() -> None:
    config = HarnessConfig()
    assert config.computer_use.enabled is True
    assert config.computer_use.session_ttl_seconds == 600.0
    assert config.computer_use.session_idle_seconds == 90.0


if __name__ == "__main__":
    test_readonly_computer_effects_do_not_prompt()
    test_mutating_computer_effect_requires_takeover()
    test_launch_computer_effect_and_invalid_args()
    test_computer_effect_storage_round_trip()
    test_computer_config_defaults()
    print("computer effect tests passed")
