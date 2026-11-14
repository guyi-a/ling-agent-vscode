"""UI Automation actions for a saved snapshot."""

from __future__ import annotations

import time
from typing import Any
from _ctypes import COMError

from ..types import ComputerError
from .capture import resolve_capture_point
from .clipboard import paste_text
from .input import (
    click_at,
    drag as drag_pointer,
    ensure_foreground,
    press_key,
    scroll_at,
    type_text,
    validate_key_chord,
)

_ACTION_TYPES = frozenset(
    {
        "click",
        "click_at",
        "set_value",
        "type",
        "press_key",
        "focus",
        "toggle",
        "expand",
        "collapse",
        "select",
        "scroll",
        "scroll_at",
        "drag",
        "drag_at",
    }
)

_COORDINATE_TYPES = frozenset(
    {
        "click_at",
        "scroll_at",
        "drag_at",
    }
)

_MAX_TEXT_LENGTH = 4000


def execute_actions(
    module: Any,
    store: Any,
    actions: list[dict[str, Any]],
    should_abort: Any = None,
    capture_id: str = "",
) -> dict[str, Any]:
    """Execute actions in order; stop at the first failure."""

    results: list[dict[str, Any]] = []
    may_have_taken_effect = False
    for index, action in enumerate(actions):
        if should_abort is not None and should_abort():
            raise ComputerError(
                "CONTROL_NOT_ACTIVE",
                "Computer control was stopped.",
            )
        try:
            result = _execute_one(
                module,
                store,
                action,
                capture_id,
            )
        except ComputerError as exc:
            raise ComputerError(
                exc.code,
                f"Action {index + 1} failed: {exc.message}",
                hint=exc.hint,
                may_have_taken_effect=(
                    may_have_taken_effect
                    or exc.may_have_taken_effect
                ),
            ) from exc
        results.append(result)
        may_have_taken_effect = (
            may_have_taken_effect
            or bool(result.get("mayHaveTakenEffect"))
        )
    return {
        "actions": results,
        "delivered": True,
        "mayHaveTakenEffect": may_have_taken_effect,
    }


def _execute_one(
    module: Any,
    store: Any,
    action: dict[str, Any],
    capture_id: str = "",
) -> dict[str, Any]:
    kind = action.get("type")
    if kind not in _ACTION_TYPES:
        raise ComputerError(
            "INVALID_ARGS",
            f"Unsupported action type: {kind}",
        )
    coordinate = kind in _COORDINATE_TYPES or (
        kind == "type" and ("x" in action or "y" in action)
    )
    ref = action.get("ref")
    entry = None
    if not coordinate and not (kind == "press_key" and not ref):
        if not isinstance(ref, str) or not ref:
            raise ComputerError(
                "INVALID_ARGS",
                "This action requires a ref from the current snapshot.",
            )
        entry = store.elements.get(ref)
        if entry is None:
            raise ComputerError(
                "STALE_REF",
                f"Element ref is no longer available: {ref}",
            )
        _check_actionable(module, entry)
        if entry.password and kind in ("set_value", "type"):
            raise ComputerError(
                "SENSITIVE_WINDOW",
                "Password fields cannot be filled by computer use.",
                hint="Ask the user to type the credential.",
            )
    if not coordinate:
        ref = ref if isinstance(ref, str) else ""

    if kind == "click":
        return _click(module, store, entry, ref)
    if kind == "click_at":
        return _click_at_action(store, action, capture_id)
    if kind == "set_value":
        return _set_value_action(module, entry, ref, action)
    if kind == "type":
        if coordinate:
            return _type_at_action(store, action, capture_id)
        return _type_action(module, store, entry, ref, action)
    if kind == "press_key":
        return _press_key_action(module, store, entry, ref, action)
    if kind == "focus":
        return _focus_action(module, store, entry, ref)
    if kind == "toggle":
        return _toggle_action(module, entry, ref)
    if kind == "expand":
        return _expand_action(module, entry, ref, True)
    if kind == "collapse":
        return _expand_action(module, entry, ref, False)
    if kind == "select":
        return _select_action(module, entry, ref)
    if kind == "scroll":
        return _scroll_action(module, store, entry, ref, action)
    if kind == "scroll_at":
        return _scroll_at_action(store, action, capture_id)
    if kind == "drag_at":
        return _drag_at_action(store, action, capture_id)
    return _drag_action(module, store, entry, ref, action)


def _click(
    module: Any,
    store: Any,
    entry: Any,
    ref: str,
) -> dict[str, Any]:
    try:
        _invoke(entry.element, module)
        mechanism = "uia_invoke"
    except ComputerError as exc:
        if exc.code != "PATTERN_UNAVAILABLE":
            raise
        if not ensure_foreground(store.identity.hwnd):
            raise ComputerError(
                "FOCUS_BLOCKED",
                "Unable to bring the window to the foreground.",
            ) from exc
        x, y = _center(_live_bounds(entry))
        try:
            click_at(x, y)
        except OSError as input_error:
            raise ComputerError(
                "INPUT_FAILED",
                f"Unable to click: {input_error}",
                may_have_taken_effect=True,
            ) from input_error
        mechanism = "sendinput"
    return {
        "type": "click",
        "ref": ref,
        "ok": True,
        "mechanism": mechanism,
        "verified": False,
        "mayHaveTakenEffect": True,
    }


def _set_value_action(
    module: Any,
    entry: Any,
    ref: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    value = action.get("value")
    if not isinstance(value, str):
        raise ComputerError(
            "INVALID_ARGS",
            "set_value requires a string value.",
        )
    verified = _set_value(entry.element, module, value)
    return {
        "type": "set_value",
        "ref": ref,
        "ok": True,
        "mechanism": "uia_value",
        "verified": verified,
        "mayHaveTakenEffect": True,
    }


def _type_action(
    module: Any,
    store: Any,
    entry: Any,
    ref: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    text = action.get("text")
    if not isinstance(text, str) or not text:
        raise ComputerError(
            "INVALID_ARGS",
            "type requires a non-empty text string.",
        )
    if len(text) > _MAX_TEXT_LENGTH:
        raise ComputerError(
            "INVALID_ARGS",
            f"type text is limited to {_MAX_TEXT_LENGTH} characters.",
        )
    if not ensure_foreground(store.identity.hwnd):
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to bring the window to the foreground for typing.",
        )
    _focus(entry.element)
    before = _current_value(entry.element, module)
    mechanism, clipboard = _paste_or_type(text)
    after = _current_value(entry.element, module)
    verified = (
        before is not None
        and after is not None
        and after == before + text
    )
    result = {
        "type": "type",
        "ref": ref,
        "ok": True,
        "mechanism": mechanism,
        "verified": verified,
        "mayHaveTakenEffect": True,
    }
    result.update(clipboard)
    if after is not None:
        result["valueAfter"] = after[:200]
    if not verified:
        result["warning"] = (
            "Text was sent but readback did not confirm it; "
            "take a screenshot to verify."
        )
    return result


def _paste_or_type(text: str) -> tuple[str, dict[str, Any]]:
    status = paste_text(text, overwrite=True)
    if status.get("ok"):
        return "clipboard", {
            "clipboardRestored": bool(status.get("clipboardRestored")),
            "clipboardReplaced": bool(status.get("clipboardReplaced")),
        }
    try:
        type_text(text)
    except OSError as exc:
        raise ComputerError(
            "INPUT_FAILED",
            f"Unable to type: {exc}",
            may_have_taken_effect=True,
        ) from exc
    return "sendinput_unicode", {
        "clipboardSkipped": str(status.get("reason", "")),
    }


def _capture_point(
    store: Any,
    capture_id: str,
    x: Any,
    y: Any,
) -> tuple[int, int]:
    if (
        not isinstance(x, int)
        or isinstance(x, bool)
        or not isinstance(y, int)
        or isinstance(y, bool)
    ):
        raise ComputerError(
            "INVALID_ARGS",
            "Coordinate actions require integer x and y.",
        )
    return resolve_capture_point(
        capture_id,
        x,
        y,
        int(store.identity.hwnd),
    )


def _click_at_action(
    store: Any,
    action: dict[str, Any],
    capture_id: str,
) -> dict[str, Any]:
    x = action.get("x")
    y = action.get("y")
    button = action.get("button", "left")
    clicks = action.get("clicks", 1)
    if button not in ("left", "right", "middle"):
        raise ComputerError(
            "INVALID_ARGS",
            "button must be left, right, or middle.",
        )
    if (
        not isinstance(clicks, int)
        or isinstance(clicks, bool)
        or not 1 <= clicks <= 3
    ):
        raise ComputerError(
            "INVALID_ARGS",
            "clicks must be between 1 and 3.",
        )
    if not ensure_foreground(store.identity.hwnd):
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to bring the window to the foreground for the click.",
        )
    screen_x, screen_y = _capture_point(store, capture_id, x, y)
    try:
        click_at(
            screen_x,
            screen_y,
            button=button,
            clicks=clicks,
        )
    except (OSError, ValueError) as exc:
        raise ComputerError(
            "INPUT_FAILED",
            f"Unable to click: {exc}",
            may_have_taken_effect=True,
        ) from exc
    return {
        "type": "click_at",
        "x": x,
        "y": y,
        "screenX": screen_x,
        "screenY": screen_y,
        "button": button,
        "clicks": clicks,
        "ok": True,
        "mechanism": "sendinput",
        "verified": False,
        "mayHaveTakenEffect": True,
    }


def _scroll_at_action(
    store: Any,
    action: dict[str, Any],
    capture_id: str,
) -> dict[str, Any]:
    direction = action.get("direction")
    if direction not in ("up", "down", "left", "right"):
        raise ComputerError(
            "INVALID_ARGS",
            "scroll_at requires direction up, down, left, or right.",
        )
    amount = action.get("amount", 3)
    if (
        not isinstance(amount, int)
        or isinstance(amount, bool)
        or not 1 <= amount <= 10
    ):
        raise ComputerError(
            "INVALID_ARGS",
            "amount must be between 1 and 10.",
        )
    if not ensure_foreground(store.identity.hwnd):
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to bring the window to the foreground for scrolling.",
        )
    x = action.get("x")
    y = action.get("y")
    screen_x, screen_y = _capture_point(store, capture_id, x, y)
    try:
        scroll_at(screen_x, screen_y, direction, amount=amount)
    except (OSError, ValueError) as exc:
        raise ComputerError(
            "INPUT_FAILED",
            f"Unable to scroll: {exc}",
            may_have_taken_effect=True,
        ) from exc
    return {
        "type": "scroll_at",
        "x": x,
        "y": y,
        "screenX": screen_x,
        "screenY": screen_y,
        "direction": direction,
        "amount": amount,
        "ok": True,
        "mechanism": "sendinput",
        "verified": False,
        "mayHaveTakenEffect": True,
    }


def _drag_at_action(
    store: Any,
    action: dict[str, Any],
    capture_id: str,
) -> dict[str, Any]:
    x = action.get("x")
    y = action.get("y")
    to_x = action.get("to_x")
    to_y = action.get("to_y")
    if not ensure_foreground(store.identity.hwnd):
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to bring the window to the foreground for the drag.",
        )
    from_screen_x, from_screen_y = _capture_point(
        store,
        capture_id,
        x,
        y,
    )
    to_screen_x, to_screen_y = _capture_point(
        store,
        capture_id,
        to_x,
        to_y,
    )
    try:
        drag_pointer(
            from_screen_x,
            from_screen_y,
            to_screen_x,
            to_screen_y,
        )
    except OSError as exc:
        raise ComputerError(
            "INPUT_FAILED",
            f"Unable to drag: {exc}",
            may_have_taken_effect=True,
        ) from exc
    return {
        "type": "drag_at",
        "x": x,
        "y": y,
        "toX": to_x,
        "toY": to_y,
        "screenX": from_screen_x,
        "screenY": from_screen_y,
        "toScreenX": to_screen_x,
        "toScreenY": to_screen_y,
        "ok": True,
        "mechanism": "sendinput",
        "verified": False,
        "mayHaveTakenEffect": True,
    }


def _type_at_action(
    store: Any,
    action: dict[str, Any],
    capture_id: str,
) -> dict[str, Any]:
    text = action.get("text")
    if not isinstance(text, str) or not text:
        raise ComputerError(
            "INVALID_ARGS",
            "type requires a non-empty text string.",
        )
    if len(text) > _MAX_TEXT_LENGTH:
        raise ComputerError(
            "INVALID_ARGS",
            f"type text is limited to {_MAX_TEXT_LENGTH} characters.",
        )
    if not ensure_foreground(store.identity.hwnd):
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to bring the window to the foreground for typing.",
        )
    x = action.get("x")
    y = action.get("y")
    screen_x, screen_y = _capture_point(store, capture_id, x, y)
    try:
        click_at(screen_x, screen_y)
    except (OSError, ValueError) as exc:
        raise ComputerError(
            "INPUT_FAILED",
            f"Unable to click before typing: {exc}",
            may_have_taken_effect=True,
        ) from exc
    time.sleep(0.12)
    mechanism, clipboard = _paste_or_type(text)
    result = {
        "type": "type",
        "x": x,
        "y": y,
        "screenX": screen_x,
        "screenY": screen_y,
        "ok": True,
        "mechanism": mechanism,
        "verified": False,
        "mayHaveTakenEffect": True,
        "warning": (
            "No UIA readback for coordinate typing; "
            "take a screenshot to verify."
        ),
    }
    result.update(clipboard)
    return result


def _press_key_action(
    module: Any,
    store: Any,
    entry: Any,
    ref: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    key = action.get("key")
    modifiers = action.get("modifiers") or []
    if not isinstance(key, str) or not key:
        raise ComputerError(
            "INVALID_ARGS",
            "press_key requires a key.",
        )
    if not isinstance(modifiers, list) or not all(
        isinstance(modifier, str) for modifier in modifiers
    ):
        raise ComputerError(
            "INVALID_ARGS",
            "press_key modifiers must be a list of strings.",
        )
    try:
        key, modifiers = validate_key_chord(key, modifiers)
    except ValueError as exc:
        raise ComputerError(
            "INVALID_ARGS",
            f"Invalid key chord: {exc}",
            may_have_taken_effect=False,
        ) from exc
    if not ensure_foreground(store.identity.hwnd):
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to bring the window to the foreground for the key.",
        )
    if entry is not None:
        _focus(entry.element)
    try:
        press_key(key, modifiers)
    except ValueError as exc:
        raise ComputerError(
            "INVALID_ARGS",
            f"Invalid key chord: {exc}",
            may_have_taken_effect=False,
        ) from exc
    except OSError as exc:
        raise ComputerError(
            "INPUT_FAILED",
            f"Unable to send the key: {exc}",
            may_have_taken_effect=True,
        ) from exc
    return {
        "type": "press_key",
        "ref": ref,
        "ok": True,
        "mechanism": "sendinput",
        "verified": False,
        "mayHaveTakenEffect": True,
    }


def _focus_action(
    module: Any,
    store: Any,
    entry: Any,
    ref: str,
) -> dict[str, Any]:
    if not ensure_foreground(store.identity.hwnd):
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to bring the window to the foreground.",
        )
    _focus(entry.element)
    return {
        "type": "focus",
        "ref": ref,
        "ok": True,
        "mechanism": "uia_focus",
        "verified": _has_focus(entry.element),
        "mayHaveTakenEffect": True,
    }


def _toggle_action(
    module: Any,
    entry: Any,
    ref: str,
) -> dict[str, Any]:
    pattern = _pattern(
        entry.element,
        module,
        module.UIA_TogglePatternId,
        module.IUIAutomationTogglePattern,
    )
    try:
        pattern.Toggle()
    except COMError as exc:
        raise ComputerError(
            "ACTION_FAILED",
            f"Toggle failed: {exc}",
            may_have_taken_effect=True,
        ) from exc
    state = _read_int_pattern(pattern, "CurrentToggleState")
    return {
        "type": "toggle",
        "ref": ref,
        "ok": True,
        "mechanism": "uia_toggle",
        "state": _toggle_name(state),
        "verified": state is not None,
        "mayHaveTakenEffect": True,
    }


def _expand_action(
    module: Any,
    entry: Any,
    ref: str,
    expand: bool,
) -> dict[str, Any]:
    pattern = _pattern(
        entry.element,
        module,
        module.UIA_ExpandCollapsePatternId,
        module.IUIAutomationExpandCollapsePattern,
    )
    try:
        if expand:
            pattern.Expand()
        else:
            pattern.Collapse()
    except COMError as exc:
        raise ComputerError(
            "ACTION_FAILED",
            f"Expand/collapse failed: {exc}",
            may_have_taken_effect=True,
        ) from exc
    state = _read_int_pattern(
        pattern,
        "CurrentExpandCollapseState",
    )
    return {
        "type": "expand" if expand else "collapse",
        "ref": ref,
        "ok": True,
        "mechanism": "uia_expand_collapse",
        "state": _expand_name(state),
        "verified": state is not None,
        "mayHaveTakenEffect": True,
    }


def _select_action(
    module: Any,
    entry: Any,
    ref: str,
) -> dict[str, Any]:
    pattern = _pattern(
        entry.element,
        module,
        module.UIA_SelectionItemPatternId,
        module.IUIAutomationSelectionItemPattern,
    )
    try:
        pattern.Select()
    except COMError as exc:
        raise ComputerError(
            "ACTION_FAILED",
            f"Select failed: {exc}",
            may_have_taken_effect=True,
        ) from exc
    selected = _read_bool_pattern(pattern, "CurrentIsSelected")
    return {
        "type": "select",
        "ref": ref,
        "ok": True,
        "mechanism": "uia_select",
        "verified": selected is not None,
        "selected": selected,
        "mayHaveTakenEffect": True,
    }


def _scroll_action(
    module: Any,
    store: Any,
    entry: Any,
    ref: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    direction = action.get("direction", "down")
    amount = action.get("amount", 3)
    if direction not in ("up", "down", "left", "right"):
        raise ComputerError(
            "INVALID_ARGS",
            "scroll direction must be up, down, left, or right.",
        )
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise ComputerError(
            "INVALID_ARGS",
            "scroll amount must be an integer.",
        )
    if not 1 <= amount <= 10:
        raise ComputerError(
            "INVALID_ARGS",
            "scroll amount must be between 1 and 10.",
        )
    try:
        pattern = _pattern(
            entry.element,
            module,
            module.UIA_ScrollPatternId,
            module.IUIAutomationScrollPattern,
        )
    except ComputerError as exc:
        if exc.code != "PATTERN_UNAVAILABLE":
            raise
        if not ensure_foreground(store.identity.hwnd):
            raise ComputerError(
                "FOCUS_BLOCKED",
                "Unable to bring the window to the foreground.",
            ) from exc
        x, y = _center(_live_bounds(entry))
        try:
            scroll_at(x, y, direction, amount=amount)
        except OSError as input_error:
            raise ComputerError(
                "INPUT_FAILED",
                f"Unable to scroll: {input_error}",
                may_have_taken_effect=True,
            ) from input_error
        return {
            "type": "scroll",
            "ref": ref,
            "ok": True,
            "mechanism": "sendinput",
            "verified": False,
            "mayHaveTakenEffect": True,
        }
    no_amount = int(getattr(module, "ScrollAmount_NoAmount", 2))
    decrement = int(
        getattr(module, "ScrollAmount_SmallDecrement", 1)
    )
    increment = int(
        getattr(module, "ScrollAmount_SmallIncrement", 4)
    )
    horizontal = no_amount
    vertical = no_amount
    if direction == "up":
        vertical = decrement
    elif direction == "down":
        vertical = increment
    elif direction == "left":
        horizontal = decrement
    else:
        horizontal = increment
    try:
        for _ in range(amount):
            pattern.Scroll(horizontal, vertical)
    except COMError as exc:
        raise ComputerError(
            "ACTION_FAILED",
            f"Scroll failed: {exc}",
            may_have_taken_effect=True,
        ) from exc
    return {
        "type": "scroll",
        "ref": ref,
        "ok": True,
        "mechanism": "uia_scroll",
        "verified": False,
        "verticalPercent": _read_float_pattern(
            pattern,
            "CurrentVerticalScrollPercent",
        ),
        "horizontalPercent": _read_float_pattern(
            pattern,
            "CurrentHorizontalScrollPercent",
        ),
        "mayHaveTakenEffect": True,
    }


def _drag_action(
    module: Any,
    store: Any,
    entry: Any,
    ref: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    target_ref = action.get("target_ref")
    if not isinstance(target_ref, str) or not target_ref:
        raise ComputerError(
            "INVALID_ARGS",
            "drag requires target_ref from the current snapshot.",
        )
    target = store.elements.get(target_ref)
    if target is None:
        raise ComputerError(
            "STALE_REF",
            f"Drag target is no longer available: {target_ref}",
        )
    _check_actionable(module, target)
    if not ensure_foreground(store.identity.hwnd):
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to bring the window to the foreground for dragging.",
        )
    from_x, from_y = _center(_live_bounds(entry))
    to_x, to_y = _center(_live_bounds(target))
    try:
        drag_pointer(from_x, from_y, to_x, to_y)
    except OSError as exc:
        raise ComputerError(
            "INPUT_FAILED",
            f"Unable to drag: {exc}",
            may_have_taken_effect=True,
        ) from exc
    return {
        "type": "drag",
        "ref": ref,
        "targetRef": target_ref,
        "ok": True,
        "mechanism": "sendinput",
        "verified": False,
        "mayHaveTakenEffect": True,
    }


def _check_actionable(module: Any, entry: Any) -> None:
    element = entry.element
    try:
        if not bool(element.CurrentIsEnabled):
            raise ComputerError(
                "NOT_ACTIONABLE",
                "The element is disabled.",
            )
        if bool(element.CurrentIsOffscreen):
            raise ComputerError(
                "NOT_ACTIONABLE",
                "The element is offscreen.",
            )
        live_runtime_id = _live_runtime_id(element)
    except COMError as exc:
        raise ComputerError(
            "ELEMENT_GONE",
            "The element is no longer available.",
        ) from exc
    if (
        entry.runtime_id
        and live_runtime_id
        and tuple(entry.runtime_id) != tuple(live_runtime_id)
    ):
        raise ComputerError(
            "STALE_REF",
            "The element was replaced; observe the window again.",
        )


def _live_runtime_id(element: Any) -> tuple[int, ...] | None:
    try:
        raw = element.GetRuntimeId()
    except (COMError, AttributeError):
        return None
    if not isinstance(raw, (tuple, list)):
        return None
    try:
        return tuple(int(part) for part in raw)
    except (TypeError, ValueError):
        return None


def _live_bounds(entry: Any) -> tuple[int, int, int, int]:
    bounds = entry.bbox
    try:
        rect = entry.element.CurrentBoundingRectangle
        bounds = (
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        )
    except (COMError, AttributeError, TypeError, ValueError):
        bounds = entry.bbox
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ComputerError(
            "NOT_ACTIONABLE",
            "The element has no usable bounds.",
        )
    return bounds


def _center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = bounds
    return ((left + right) // 2, (top + bottom) // 2)


def _pattern(
    element: Any,
    module: Any,
    pattern_id: int,
    interface: Any,
) -> Any:
    raw = None
    try:
        raw = element.GetCachedPattern(pattern_id)
    except COMError:
        try:
            raw = element.GetCurrentPattern(pattern_id)
        except COMError as exc:
            raise ComputerError(
                "PATTERN_UNAVAILABLE",
                "The element does not support the required pattern.",
            ) from exc
    if raw is None:
        raise ComputerError(
            "PATTERN_UNAVAILABLE",
            "The element does not support the required pattern.",
        )
    try:
        return raw.QueryInterface(interface)
    except (COMError, AttributeError) as exc:
        raise ComputerError(
            "PATTERN_UNAVAILABLE",
            "The element pattern interface is unavailable.",
        ) from exc


def _invoke(element: Any, module: Any) -> None:
    try:
        pattern = _pattern(
            element,
            module,
            module.UIA_InvokePatternId,
            module.IUIAutomationInvokePattern,
        )
        pattern.Invoke()
        return
    except ComputerError as exc:
        if exc.code != "PATTERN_UNAVAILABLE":
            raise
    legacy_id = getattr(
        module,
        "UIA_LegacyIAccessiblePatternId",
        None,
    )
    legacy_interface = getattr(
        module,
        "IUIAutomationLegacyIAccessiblePattern",
        None,
    )
    if legacy_id is not None and legacy_interface is not None:
        try:
            legacy = _pattern(
                element,
                module,
                legacy_id,
                legacy_interface,
            )
            legacy.DoDefaultAction()
            return
        except ComputerError:
            pass
        except COMError as exc:
            raise ComputerError(
                "ACTION_FAILED",
                f"Default action failed: {exc}",
            ) from exc
    raise ComputerError(
        "PATTERN_UNAVAILABLE",
        "The element supports neither Invoke nor a default action.",
    )


def _set_value(element: Any, module: Any, value: str) -> bool:
    pattern = _pattern(
        element,
        module,
        module.UIA_ValuePatternId,
        module.IUIAutomationValuePattern,
    )
    try:
        pattern.SetValue(value)
    except COMError as exc:
        raise ComputerError(
            "ACTION_FAILED",
            f"SetValue failed: {exc}",
        ) from exc
    try:
        current = element.GetCurrentPropertyValue(
            module.UIA_ValueValuePropertyId
        )
        return str(current) == value
    except COMError:
        try:
            return str(pattern.CurrentValue) == value
        except COMError:
            return False


def _current_value(element: Any, module: Any) -> str | None:
    try:
        value = element.GetCurrentPropertyValue(
            module.UIA_ValueValuePropertyId
        )
    except (COMError, AttributeError):
        return None
    if value is None:
        return None
    return str(value)


def _focus(element: Any) -> None:
    try:
        element.SetFocus()
    except COMError as exc:
        raise ComputerError(
            "FOCUS_BLOCKED",
            "Unable to focus the element for keyboard input.",
        ) from exc


def _has_focus(element: Any) -> bool:
    try:
        return bool(element.CurrentHasKeyboardFocus)
    except (COMError, AttributeError):
        return False


def _read_bool_pattern(pattern: Any, name: str) -> bool | None:
    try:
        return bool(getattr(pattern, name))
    except (COMError, AttributeError, TypeError, ValueError):
        return None


def _read_int_pattern(pattern: Any, name: str) -> int | None:
    try:
        return int(getattr(pattern, name))
    except (COMError, AttributeError, TypeError, ValueError):
        return None


def _read_float_pattern(pattern: Any, name: str) -> float | None:
    try:
        return round(float(getattr(pattern, name)), 1)
    except (COMError, AttributeError, TypeError, ValueError):
        return None


def _toggle_name(state: int | None) -> str:
    if state == 1:
        return "on"
    if state == 0:
        return "off"
    return "indeterminate"


def _expand_name(state: int | None) -> str:
    if state == 0:
        return "collapsed"
    if state == 1:
        return "expanded"
    if state == 2:
        return "partially_expanded"
    if state == 3:
        return "leaf_node"
    return "unknown"


__all__ = ["execute_actions"]
