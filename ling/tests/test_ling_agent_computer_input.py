"""Tests for computer-use keyboard, mouse, and takeover detection."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Any, Iterator

from ling_agent.computer.helper import input as input_api


@contextmanager
def _patched(target: Any, **values: Any) -> Iterator[None]:
    original = {
        name: getattr(target, name)
        for name in values
    }
    for name, value in values.items():
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in original.items():
            setattr(target, name, value)


def _reset() -> None:
    input_api.ledger.clear()


def test_resolve_key_and_modifier() -> None:
    assert input_api._resolve_modifier("Ctrl") == 0x11
    assert input_api._resolve_key("enter") == 0x0D
    assert input_api._resolve_key("A") == ord("A")
    assert input_api._resolve_key("f5") == 0x74
    for invalid in ("unknown-key", "ctrl+alt"):
        try:
            input_api._resolve_key(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{invalid} should be rejected")
    try:
        input_api._resolve_modifier("hyper")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown modifier should be rejected")


def test_normalize_key_chord() -> None:
    assert input_api.validate_key_chord("ctrl+s") == ("s", ["ctrl"])
    assert input_api.validate_key_chord(
        "Control+Shift+period"
    ) == ("period", ["Control", "Shift"])
    assert input_api.validate_key_chord(
        "s",
        ["ctrl"],
    ) == ("s", ["ctrl"])


def test_ledger_tracks_and_clears() -> None:
    _reset()
    input_api.ledger.mark_down(keys=(0x11, 0x10), buttons=("left",))
    assert input_api.ledger.pending_keys() == (0x10, 0x11)
    assert input_api.ledger.pending_buttons() == ("left",)
    input_api.ledger.mark_up(keys=(0x11,), buttons=("left",))
    assert input_api.ledger.pending_keys() == (0x10,)
    assert input_api.ledger.pending_buttons() == ()
    _reset()
    assert input_api.ledger.pending_keys() == ()


def test_normalize_axis() -> None:
    assert input_api._normalize_axis(0, 0, 1920) == 0
    assert input_api._normalize_axis(1919, 0, 1920) == 65535
    assert input_api._normalize_axis(-200, -200, 1000) == 0
    assert input_api._normalize_axis(799, -200, 1000) == 65535
    assert input_api._normalize_axis(-500, 0, 1920) == 0
    assert input_api._normalize_axis(5000, 0, 1920) == 65535
    assert input_api._normalize_axis(10, 0, 1) == 0


def test_type_text_rejects_empty() -> None:
    try:
        input_api.type_text("")
    except ValueError:
        pass
    else:
        raise AssertionError("empty text should be rejected")


def test_type_text_sends_unicode() -> None:
    if sys.platform != "win32":
        return
    sent: list[list] = []
    with _patched(
        input_api,
        _send=lambda events: sent.append(list(events)),
    ):
        count = input_api.type_text("中\n")
    assert count == 2
    events = [event for batch in sent for event in batch]
    unicode_events = [
        event
        for event in events
        if event.type == input_api.INPUT_KEYBOARD
        and event.value.ki.dwFlags & input_api.KEYEVENTF_UNICODE
    ]
    assert len(unicode_events) == 2
    assert unicode_events[0].value.ki.wScan == 0x4E2D
    assert (
        unicode_events[0].value.ki.dwFlags
        == input_api.KEYEVENTF_UNICODE
    )
    assert (
        unicode_events[1].value.ki.dwFlags
        == input_api.KEYEVENTF_UNICODE
        | input_api.KEYEVENTF_KEYUP
    )
    assert any(
        event.value.ki.wVk == 0x0D
        for event in events
        if event.type == input_api.INPUT_KEYBOARD
    )


def test_click_and_scroll_events() -> None:
    if sys.platform != "win32":
        return
    _reset()
    sent: list[list] = []
    with _patched(
        input_api,
        _send=lambda events: sent.append(list(events)),
        _move_to=lambda x, y: None,
    ):
        input_api.click_at(100, 200)
        assert len(sent) == 2
        assert (
            sent[0][0].value.mi.dwFlags
            == input_api.MOUSEEVENTF_LEFTDOWN
        )
        assert (
            sent[1][0].value.mi.dwFlags
            == input_api.MOUSEEVENTF_LEFTUP
        )
        assert input_api.ledger.pending_buttons() == ()

        sent.clear()
        input_api.scroll_at(100, 200, "down", amount=3)
        assert len(sent) == 1
        wheel = sent[0][0]
        assert wheel.value.mi.dwFlags == input_api.MOUSEEVENTF_WHEEL
        assert wheel.value.mi.mouseData == (-360) & 0xFFFFFFFF

        sent.clear()
        input_api.scroll_at(100, 200, "right", amount=1)
        assert (
            sent[0][0].value.mi.dwFlags
            == input_api.MOUSEEVENTF_HWHEEL
        )
        assert sent[0][0].value.mi.mouseData == 120


def test_release_all_releases_pending_input() -> None:
    if sys.platform != "win32":
        return
    _reset()
    sent: list[list] = []
    with _patched(
        input_api,
        _send=lambda events: sent.append(list(events)),
    ):
        input_api.ledger.mark_down(keys=(0x11,), buttons=("left",))
        input_api.release_all()
    assert input_api.ledger.pending_keys() == ()
    assert input_api.ledger.pending_buttons() == ()
    assert len(sent) == 1
    events = sent[0]
    assert events[0].type == input_api.INPUT_KEYBOARD
    assert events[0].value.ki.wVk == 0x11
    assert events[0].value.ki.dwFlags == input_api.KEYEVENTF_KEYUP
    assert events[1].type == input_api.INPUT_MOUSE
    assert events[1].value.mi.dwFlags == input_api.MOUSEEVENTF_LEFTUP


if __name__ == "__main__":
    test_resolve_key_and_modifier()
    test_normalize_key_chord()
    test_ledger_tracks_and_clears()
    test_normalize_axis()
    test_type_text_rejects_empty()
    test_type_text_sends_unicode()
    test_click_and_scroll_events()
    test_release_all_releases_pending_input()
    print("computer input tests passed")
