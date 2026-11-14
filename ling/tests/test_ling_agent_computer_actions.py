"""Tests for UIA actions, fallbacks, and readback."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

from ling_agent.computer.helper import actions
from ling_agent.computer.helper.actions import execute_actions
from ling_agent.computer.types import ComputerError

REF = "@snap-1:e1"


class FakeComError(Exception):
    pass


class FakeModule:
    UIA_InvokePatternId = 1
    UIA_LegacyIAccessiblePatternId = 2
    UIA_ValuePatternId = 3
    UIA_TogglePatternId = 4
    UIA_SelectionItemPatternId = 5
    UIA_ExpandCollapsePatternId = 6
    UIA_ScrollPatternId = 7
    UIA_ValueValuePropertyId = 8
    UIA_RuntimeIdPropertyId = 9

    IUIAutomationInvokePattern = "invoke"
    IUIAutomationLegacyIAccessiblePattern = "legacy"
    IUIAutomationValuePattern = "value"
    IUIAutomationTogglePattern = "toggle"
    IUIAutomationSelectionItemPattern = "select"
    IUIAutomationExpandCollapsePattern = "expand"
    IUIAutomationScrollPattern = "scroll"

    ScrollAmount_NoAmount = 2
    ScrollAmount_SmallDecrement = 1
    ScrollAmount_SmallIncrement = 4


class FakeRaw:
    def __init__(self, pattern: Any) -> None:
        self._pattern = pattern

    def QueryInterface(self, _interface: Any) -> Any:
        return self._pattern


class FakeElement:
    def __init__(
        self,
        *,
        runtime_id: tuple[int, ...] = (1, 2),
        enabled: bool = True,
        offscreen: bool = False,
        bounds: tuple[int, int, int, int] = (10, 10, 50, 30),
        value: str | None = None,
    ) -> None:
        self._runtime_id = runtime_id
        self._patterns: dict[int, Any] = {}
        self._value = value
        self.CurrentIsEnabled = enabled
        self.CurrentIsOffscreen = offscreen
        self.CurrentHasKeyboardFocus = False
        left, top, right, bottom = bounds
        self.CurrentBoundingRectangle = SimpleNamespace(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )

    def GetRuntimeId(self) -> tuple[int, ...]:
        return self._runtime_id

    def GetCachedPattern(self, pattern_id: int) -> FakeRaw:
        pattern = self._patterns.get(pattern_id)
        if pattern is None:
            raise actions.COMError("pattern unavailable")
        return FakeRaw(pattern)

    def GetCurrentPattern(self, pattern_id: int) -> FakeRaw:
        return self.GetCachedPattern(pattern_id)

    def GetCurrentPropertyValue(self, property_id: int) -> Any:
        if property_id == FakeModule.UIA_ValueValuePropertyId:
            return self._value
        raise actions.COMError("property unavailable")

    def SetFocus(self) -> None:
        self.CurrentHasKeyboardFocus = True


class FakeInvoke:
    def __init__(self) -> None:
        self.invoked = False

    def Invoke(self) -> None:
        self.invoked = True


class FakeValue:
    def __init__(self, element: FakeElement) -> None:
        self._element = element

    def SetValue(self, value: str) -> None:
        self._element._value = value


class FakeToggle:
    def __init__(self) -> None:
        self.CurrentToggleState = 0

    def Toggle(self) -> None:
        self.CurrentToggleState = 1 - self.CurrentToggleState


class FakeExpand:
    def __init__(self) -> None:
        self.CurrentExpandCollapseState = 0

    def Expand(self) -> None:
        self.CurrentExpandCollapseState = 1

    def Collapse(self) -> None:
        self.CurrentExpandCollapseState = 0


class FakeSelect:
    def __init__(self) -> None:
        self.CurrentIsSelected = False

    def Select(self) -> None:
        self.CurrentIsSelected = True


class FakeScroll:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.CurrentVerticalScrollPercent = 0.0
        self.CurrentHorizontalScrollPercent = 0.0

    def Scroll(self, horizontal: int, vertical: int) -> None:
        self.calls.append((horizontal, vertical))
        self.CurrentVerticalScrollPercent += 10.0


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


def _entry(
    element: FakeElement,
    *,
    ref: str = REF,
    runtime_id: tuple[int, ...] = (1, 2),
    bbox: tuple[int, int, int, int] = (10, 10, 50, 30),
    password: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        ref=ref,
        element=element,
        runtime_id=runtime_id,
        bbox=bbox,
        password=password,
    )


def _store(*entries: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        elements={entry.ref: entry for entry in entries},
        identity=SimpleNamespace(hwnd=1234),
        window_id="w-1234",
    )


def _run(
    store: SimpleNamespace,
    action_list: list[dict[str, Any]],
    capture_id: str = "",
    **patches: Any,
) -> dict[str, Any]:
    with _patched(actions, COMError=FakeComError, **patches):
        return execute_actions(
            FakeModule(),
            store,
            action_list,
            capture_id=capture_id,
        )


def _expect_error(
    store: SimpleNamespace,
    action_list: list[dict[str, Any]],
    code: str,
) -> ComputerError:
    try:
        _run(store, action_list)
    except ComputerError as exc:
        assert exc.code == code, (exc.code, exc.message)
        return exc
    raise AssertionError(f"expected {code}")


def test_click_uses_invoke() -> None:
    element = FakeElement()
    invoke = FakeInvoke()
    element._patterns[FakeModule.UIA_InvokePatternId] = invoke
    result = _run(
        _store(_entry(element)),
        [{"type": "click", "ref": REF}],
    )
    assert invoke.invoked is True
    assert result["actions"][0]["mechanism"] == "uia_invoke"
    assert result["mayHaveTakenEffect"] is True


def test_click_falls_back_to_mouse() -> None:
    element = FakeElement()
    clicked: list[tuple[int, int]] = []
    result = _run(
        _store(_entry(element)),
        [{"type": "click", "ref": REF}],
        ensure_foreground=lambda hwnd: True,
        click_at=lambda x, y: clicked.append((x, y)),
    )
    assert clicked == [(30, 20)]
    assert result["actions"][0]["mechanism"] == "sendinput"


def test_set_value_reads_back() -> None:
    element = FakeElement(value="old")
    element._patterns[FakeModule.UIA_ValuePatternId] = FakeValue(element)
    result = _run(
        _store(_entry(element)),
        [{"type": "set_value", "ref": REF, "value": "new"}],
    )
    assert element._value == "new"
    assert result["actions"][0]["verified"] is True


def test_password_rejects_text_actions() -> None:
    element = FakeElement()
    store = _store(_entry(element, password=True))
    _expect_error(
        store,
        [{"type": "set_value", "ref": REF, "value": "secret"}],
        "SENSITIVE_WINDOW",
    )
    _expect_error(
        store,
        [{"type": "type", "ref": REF, "text": "secret"}],
        "SENSITIVE_WINDOW",
    )


def test_stale_runtime_id() -> None:
    element = FakeElement(runtime_id=(1, 3))
    _expect_error(
        _store(_entry(element, runtime_id=(1, 2))),
        [{"type": "click", "ref": REF}],
        "STALE_REF",
    )


def test_toggle_and_expand_readback() -> None:
    toggle = FakeToggle()
    toggle_element = FakeElement()
    toggle_element._patterns[FakeModule.UIA_TogglePatternId] = toggle
    result = _run(
        _store(_entry(toggle_element)),
        [{"type": "toggle", "ref": REF}],
    )
    assert result["actions"][0]["state"] == "on"
    assert result["actions"][0]["verified"] is True

    expand = FakeExpand()
    expand_element = FakeElement()
    expand_element._patterns[
        FakeModule.UIA_ExpandCollapsePatternId
    ] = expand
    store = _store(_entry(expand_element))
    result = _run(store, [{"type": "expand", "ref": REF}])
    assert result["actions"][0]["state"] == "expanded"
    result = _run(store, [{"type": "collapse", "ref": REF}])
    assert result["actions"][0]["state"] == "collapsed"

    select = FakeSelect()
    select_element = FakeElement()
    select_element._patterns[
        FakeModule.UIA_SelectionItemPatternId
    ] = select
    result = _run(
        _store(_entry(select_element)),
        [{"type": "select", "ref": REF}],
    )
    assert result["actions"][0]["selected"] is True


def test_type_falls_back_to_unicode() -> None:
    element = FakeElement()
    typed: list[str] = []
    result = _run(
        _store(_entry(element)),
        [{"type": "type", "ref": REF, "text": "你好"}],
        ensure_foreground=lambda hwnd: True,
        paste_text=lambda text, overwrite=False: {
            "ok": False,
            "reason": "non_text",
        },
        type_text=lambda text: typed.append(text),
    )
    assert typed == ["你好"]
    assert element.CurrentHasKeyboardFocus is True
    step = result["actions"][0]
    assert step["mechanism"] == "sendinput_unicode"
    assert step["verified"] is False


def test_scroll_uses_uia() -> None:
    element = FakeElement()
    scroll = FakeScroll()
    element._patterns[FakeModule.UIA_ScrollPatternId] = scroll
    result = _run(
        _store(_entry(element)),
        [
            {
                "type": "scroll",
                "ref": REF,
                "direction": "down",
                "amount": 2,
            }
        ],
    )
    assert scroll.calls == [(2, 4), (2, 4)]
    step = result["actions"][0]
    assert step["mechanism"] == "uia_scroll"
    assert step["verticalPercent"] == 20.0


def test_drag_uses_element_centers() -> None:
    source = FakeElement()
    target = FakeElement(
        runtime_id=(3, 4),
        bounds=(100, 100, 140, 140),
    )
    points: list[tuple[int, int, int, int]] = []
    result = _run(
        _store(
            _entry(source),
            _entry(target, ref="@snap-1:e2", runtime_id=(3, 4)),
        ),
        [
            {
                "type": "drag",
                "ref": REF,
                "target_ref": "@snap-1:e2",
            }
        ],
        ensure_foreground=lambda hwnd: True,
        drag_pointer=lambda fx, fy, tx, ty: points.append(
            (fx, fy, tx, ty)
        ),
    )
    assert points == [(30, 20, 120, 120)]
    assert result["actions"][0]["targetRef"] == "@snap-1:e2"


def test_disabled_element_and_invalid_action() -> None:
    _expect_error(
        _store(_entry(FakeElement(enabled=False))),
        [{"type": "click", "ref": REF}],
        "NOT_ACTIONABLE",
    )
    _expect_error(
        _store(_entry(FakeElement())),
        [{"type": "teleport", "ref": REF}],
        "INVALID_ARGS",
    )
    _expect_error(
        _store(_entry(FakeElement())),
        [{"type": "click"}],
        "INVALID_ARGS",
    )


def test_focus_action_verifies() -> None:
    element = FakeElement()
    result = _run(
        _store(_entry(element)),
        [{"type": "focus", "ref": REF}],
        ensure_foreground=lambda hwnd: True,
    )
    assert element.CurrentHasKeyboardFocus is True
    assert result["actions"][0]["verified"] is True


def test_click_at_maps_capture_coordinates() -> None:
    calls: list[tuple[int, int, str, int]] = []
    result = _run(
        _store(),
        [
            {
                "type": "click_at",
                "x": 10,
                "y": 20,
                "button": "right",
                "clicks": 2,
            }
        ],
        capture_id="cap-1",
        resolve_capture_point=lambda capture_id, x, y, hwnd: (500, 400),
        ensure_foreground=lambda hwnd: True,
        click_at=lambda x, y, button="left", clicks=1: calls.append(
            (x, y, button, clicks)
        ),
    )
    step = result["actions"][0]
    assert calls == [(500, 400, "right", 2)]
    assert step["screenX"] == 500
    assert step["screenY"] == 400
    assert step["mechanism"] == "sendinput"


def test_type_at_clicks_then_pastes() -> None:
    events: list[tuple] = []
    result = _run(
        _store(),
        [{"type": "type", "x": 5, "y": 6, "text": "nb"}],
        capture_id="cap-1",
        resolve_capture_point=lambda capture_id, x, y, hwnd: (600, 500),
        ensure_foreground=lambda hwnd: True,
        click_at=lambda x, y: events.append(("click", x, y)),
        paste_text=lambda text, overwrite=False: (
            events.append(("paste", text, overwrite))
            or {
                "ok": True,
                "clipboardRestored": True,
                "clipboardReplaced": False,
            }
        ),
    )
    step = result["actions"][0]
    assert events == [("click", 600, 500), ("paste", "nb", True)]
    assert step["mechanism"] == "clipboard"
    assert step["clipboardRestored"] is True
    assert step["verified"] is False


def test_press_key_without_ref_uses_foreground() -> None:
    keys: list[tuple[str, list[str]]] = []
    result = _run(
        _store(),
        [{"type": "press_key", "key": "enter"}],
        ensure_foreground=lambda hwnd: True,
        press_key=lambda key, modifiers=None: keys.append(
            (key, list(modifiers or []))
        ),
    )
    assert keys == [("enter", [])]
    assert result["actions"][0]["mechanism"] == "sendinput"


def test_press_key_shorthand_and_error_semantics() -> None:
    keys: list[tuple[str, list[str]]] = []
    result = _run(
        _store(),
        [{"type": "press_key", "key": "ctrl+s"}],
        ensure_foreground=lambda hwnd: True,
        press_key=lambda key, modifiers=None: keys.append(
            (key, list(modifiers or []))
        ),
    )
    assert keys == [("s", ["ctrl"])]
    assert result["actions"][0]["ok"] is True

    try:
        _run(
            _store(),
            [{"type": "press_key", "key": "ctrl+not-a-key"}],
            ensure_foreground=lambda hwnd: True,
            press_key=lambda key, modifiers=None: None,
        )
    except ComputerError as exc:
        assert exc.code == "INVALID_ARGS"
        assert exc.may_have_taken_effect is False
    else:
        raise AssertionError("invalid chord should fail")

    def fail_send(key: str, modifiers: list[str] | None = None) -> None:
        raise OSError("send failed")

    try:
        _run(
            _store(),
            [{"type": "press_key", "key": "ctrl+s"}],
            ensure_foreground=lambda hwnd: True,
            press_key=fail_send,
        )
    except ComputerError as exc:
        assert exc.code == "INPUT_FAILED"
        assert exc.may_have_taken_effect is True
    else:
        raise AssertionError("send failure should be uncertain")


if __name__ == "__main__":
    if sys.platform != "win32":
        print("computer action tests skipped")
    else:
        test_click_uses_invoke()
        test_click_falls_back_to_mouse()
        test_set_value_reads_back()
        test_password_rejects_text_actions()
        test_stale_runtime_id()
        test_toggle_and_expand_readback()
        test_type_falls_back_to_unicode()
        test_scroll_uses_uia()
        test_drag_uses_element_centers()
        test_disabled_element_and_invalid_action()
        test_focus_action_verifies()
        test_click_at_maps_capture_coordinates()
        test_type_at_clicks_then_pastes()
        test_press_key_without_ref_uses_foreground()
        test_press_key_shorthand_and_error_semantics()
        print("computer action tests passed")
