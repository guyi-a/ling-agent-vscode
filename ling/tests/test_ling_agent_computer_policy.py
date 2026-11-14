"""Tests for the hard-deny computer-use policy."""

from __future__ import annotations

from ling_agent.computer.policy import (
    hard_deny_reason,
    is_controllable,
    observe_deny_reason,
    uipi_blocked,
    window_deny_reason,
)
from ling_agent.computer.types import Rect, WindowIdentity


def _window(
    exe: str = "notepad.exe",
    *,
    integrity: str = "medium",
    pid: int = 100,
    creation_time: int = 1,
) -> WindowIdentity:
    return WindowIdentity(
        hwnd=1,
        pid=pid,
        creation_time=creation_time,
        exe_path=f"C:\\Windows\\{exe}",
        exe_name=exe,
        title="Test",
        class_name="TestWindow",
        bounds=Rect(0, 0, 100, 100),
        visible=True,
        cloaked=False,
        minimized=False,
        integrity=integrity,
    )


def test_hard_denied_processes() -> None:
    assert "policy" in str(hard_deny_reason("cmd.exe"))
    assert "policy" in str(hard_deny_reason("PowerShell.EXE"))
    assert "policy" in str(hard_deny_reason("keepass.exe"))
    assert hard_deny_reason("notepad.exe") is None
    assert "unavailable" in str(hard_deny_reason(""))


def test_user_denied_apps() -> None:
    assert hard_deny_reason("notepad.exe", ("notepad",)) is not None
    assert (
        hard_deny_reason(
            "C:\\Apps\\Slack.exe",
            ("slack.exe",),
        )
        is not None
    )
    assert hard_deny_reason("notepad.exe", ("slack.exe",)) is None
    assert hard_deny_reason("cmd.exe", ()) is not None


def test_lock_screen_aumid_is_blocked() -> None:
    window = _window(exe="lockapp.exe")
    window = WindowIdentity(
        hwnd=window.hwnd,
        pid=window.pid,
        creation_time=window.creation_time,
        exe_path=window.exe_path,
        exe_name=window.exe_name,
        title="",
        class_name="",
        bounds=window.bounds,
        visible=True,
        cloaked=False,
        minimized=False,
        aumid="Microsoft.LockApp_cw5n1h2txyewy!App",
        integrity="medium",
    )
    assert "lock screen" in str(window_deny_reason(window)).lower()
    assert "lock screen" in str(
        hard_deny_reason("explorer.exe", (), window.aumid)
    ).lower()


def test_observe_policy() -> None:
    assert observe_deny_reason(_window(exe="keepass.exe")) is not None
    assert observe_deny_reason(_window(exe="notepad.exe")) is None
    assert observe_deny_reason(_window(exe="cmd.exe")) is None
    assert observe_deny_reason(_window(integrity="high")) is None


def test_uipi_detection() -> None:
    assert uipi_blocked(_window(integrity="high")) is True
    assert uipi_blocked(_window(integrity="system")) is True
    assert uipi_blocked(_window(integrity="medium")) is False
    assert uipi_blocked(_window(integrity="low")) is False
    assert uipi_blocked(_window(integrity="unknown")) is False


def test_window_policy_blocks_elevated() -> None:
    assert "Elevated" in str(window_deny_reason(_window(integrity="high")))
    assert "Elevated" in str(
        window_deny_reason(_window(integrity="system"))
    )
    assert window_deny_reason(_window(integrity="medium")) is None
    assert window_deny_reason(_window(integrity="low")) is None
    assert window_deny_reason(_window(integrity="unknown")) is None


def test_window_policy_requires_identity() -> None:
    missing_pid = _window(pid=0)
    assert "identity" in str(window_deny_reason(missing_pid)).lower()
    missing_time = _window(creation_time=0)
    assert "identity" in str(window_deny_reason(missing_time)).lower()
    assert is_controllable(_window()) is True
    assert is_controllable(_window(exe="cmd.exe")) is False
    assert is_controllable(_window(), ("notepad.exe",)) is False


if __name__ == "__main__":
    test_hard_denied_processes()
    test_user_denied_apps()
    test_lock_screen_aumid_is_blocked()
    test_observe_policy()
    test_uipi_detection()
    test_window_policy_blocks_elevated()
    test_window_policy_requires_identity()
    print("computer policy tests passed")
