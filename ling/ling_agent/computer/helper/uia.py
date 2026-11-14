"""UI Automation snapshots for the computer-use helper."""

from __future__ import annotations

import ctypes
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from _ctypes import COMError

from ..policy import (
    observe_deny_reason,
    uipi_blocked,
    window_deny_reason,
)
from ..types import ComputerError, WindowIdentity
from . import win32
from .actions import execute_actions

_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "calendar",
        "checkbox",
        "combobox",
        "dataitem",
        "document",
        "edit",
        "hyperlink",
        "listitem",
        "menuitem",
        "radiobutton",
        "slider",
        "spinner",
        "splitbutton",
        "tabitem",
        "treeitem",
    }
)

_CACHE_PROPERTIES = (
    "UIA_NamePropertyId",
    "UIA_AutomationIdPropertyId",
    "UIA_ControlTypePropertyId",
    "UIA_BoundingRectanglePropertyId",
    "UIA_IsEnabledPropertyId",
    "UIA_IsOffscreenPropertyId",
    "UIA_IsPasswordPropertyId",
    "UIA_IsKeyboardFocusablePropertyId",
    "UIA_HasKeyboardFocusPropertyId",
    "UIA_RuntimeIdPropertyId",
    "UIA_ValueValuePropertyId",
    "UIA_ToggleToggleStatePropertyId",
    "UIA_ExpandCollapseExpandCollapseStatePropertyId",
    "UIA_SelectionItemIsSelectedPropertyId",
    "UIA_IsInvokePatternAvailablePropertyId",
    "UIA_IsValuePatternAvailablePropertyId",
    "UIA_IsTogglePatternAvailablePropertyId",
    "UIA_IsSelectionItemPatternAvailablePropertyId",
    "UIA_IsExpandCollapsePatternAvailablePropertyId",
    "UIA_IsScrollPatternAvailablePropertyId",
    "UIA_IsTextPatternAvailablePropertyId",
)

_CACHE_PATTERNS = (
    "UIA_InvokePatternId",
    "UIA_ValuePatternId",
    "UIA_TogglePatternId",
    "UIA_SelectionItemPatternId",
    "UIA_ExpandCollapsePatternId",
    "UIA_ScrollPatternId",
    "UIA_TextPatternId",
)

_PATTERN_ACTIONS = (
    ("UIA_IsInvokePatternAvailablePropertyId", "invoke"),
    ("UIA_IsValuePatternAvailablePropertyId", "value"),
    ("UIA_IsTogglePatternAvailablePropertyId", "toggle"),
    ("UIA_IsSelectionItemPatternAvailablePropertyId", "select"),
    ("UIA_IsExpandCollapsePatternAvailablePropertyId", "expand"),
    ("UIA_IsScrollPatternAvailablePropertyId", "scroll"),
    ("UIA_IsTextPatternAvailablePropertyId", "text"),
)

_MAX_SNAPSHOTS = 32
_MAX_ELEMENTS_IN_RESULT = 200
_SNAPSHOT_TIMEOUT_SECONDS = 25.0


@dataclass(slots=True)
class SnapshotElement:
    ref: str
    role: str
    name: str
    value: str
    automation_id: str
    bbox: tuple[int, int, int, int]
    actions: tuple[str, ...]
    states: tuple[str, ...]
    runtime_id: tuple[int, ...] | None
    element: Any = field(repr=False, compare=False)
    password: bool = False

    def to_json(self) -> dict[str, Any]:
        left, top, right, bottom = self.bbox
        return {
            "ref": self.ref,
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "automationId": self.automation_id,
            "bounds": {
                "x": left,
                "y": top,
                "width": max(0, right - left),
                "height": max(0, bottom - top),
            },
            "actions": list(self.actions),
            "states": list(self.states),
        }


@dataclass(slots=True)
class SnapshotStore:
    snapshot_id: str
    window_id: str
    identity: WindowIdentity
    created_at: float
    tree: str
    elements: dict[str, SnapshotElement]
    complete: bool
    truncated: bool
    identity_unproven: bool
    node_count: int


class UiaWorker:
    """Own one MTA UIA client on a dedicated thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ling-uia",
        )
        self._module: Any = None
        self._uia: Any = None
        self._condition: Any = None
        self._control_types: dict[int, str] = {}
        self._snapshots: dict[str, SnapshotStore] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def snapshot(
        self,
        *,
        window_id: str,
        max_depth: int,
        max_nodes: int,
        max_chars: int,
        query: str,
    ) -> dict[str, Any]:
        future = self._executor.submit(
            self._snapshot_sync,
            window_id,
            max_depth,
            max_nodes,
            max_chars,
            query,
        )
        try:
            return future.result(timeout=_SNAPSHOT_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise ComputerError(
                "UIA_TIMEOUT",
                "UI Automation snapshot timed out.",
                hint="The helper may restart; observe again.",
            ) from exc
        except COMError as exc:
            raise ComputerError(
                "APP_UNRESPONSIVE",
                f"UI Automation failed: {exc}",
                hint="The target window may not be responding.",
            ) from exc

    def find(
        self,
        *,
        window_id: str,
        snapshot_id: str,
        ref: str,
    ) -> dict[str, Any]:
        with self._lock:
            store = self._snapshots.get(snapshot_id)
            if store is None or store.window_id != window_id:
                raise ComputerError(
                    "STALE_SNAPSHOT",
                    "Snapshot is no longer available.",
                )
            if not ref.startswith(f"@{snapshot_id}:"):
                raise ComputerError(
                    "STALE_REF",
                    "Ref does not belong to this snapshot.",
                )
            element = store.elements.get(ref)
            if element is None:
                raise ComputerError(
                    "STALE_REF",
                    "Element ref is no longer available.",
                )
            return {
                "snapshotId": snapshot_id,
                "windowId": window_id,
                "element": element.to_json(),
            }

    def act(
        self,
        *,
        window_id: str,
        snapshot_id: str,
        actions: list[dict[str, Any]],
        should_abort: Any = None,
        capture_id: str = "",
    ) -> dict[str, Any]:
        future = self._executor.submit(
            self._act_sync,
            window_id,
            snapshot_id,
            actions,
            should_abort,
            capture_id,
        )
        try:
            return future.result(timeout=20.0)
        except TimeoutError as exc:
            raise ComputerError(
                "UIA_TIMEOUT",
                "UI Automation action timed out.",
                hint="The helper may restart; observe before retrying.",
                may_have_taken_effect=True,
            ) from exc
        except COMError as exc:
            raise ComputerError(
                "ACTION_FAILED",
                f"UI Automation action failed: {exc}",
                may_have_taken_effect=True,
            ) from exc

    def _act_sync(
        self,
        window_id: str,
        snapshot_id: str,
        actions: list[dict[str, Any]],
        should_abort: Any = None,
        capture_id: str = "",
    ) -> dict[str, Any]:
        self._ensure_client()
        store = None
        if snapshot_id:
            with self._lock:
                store = self._snapshots.get(snapshot_id)
            if store is None or store.window_id != window_id:
                raise ComputerError(
                    "STALE_SNAPSHOT",
                    "Snapshot is no longer available; observe again.",
                )
        hwnd = _parse_window_id(window_id)
        current = win32.describe_window(hwnd)
        if current is None:
            raise ComputerError(
                "WINDOW_NOT_FOUND",
                f"Window is gone: {window_id}",
            )
        identity = store.identity if store is not None else current
        if (
            current.pid != identity.pid
            or current.creation_time != identity.creation_time
        ):
            raise ComputerError(
                "WINDOW_CHANGED",
                "The window now belongs to a different process; observe again.",
            )
        if current.minimized:
            raise ComputerError(
                "WINDOW_MINIMIZED",
                "The window was minimized; restore it before acting.",
            )
        if uipi_blocked(current):
            raise ComputerError(
                "UIPI_BLOCKED",
                "The target runs at a higher integrity level; "
                "Windows blocks synthetic input to it.",
                hint="Ask the user to run the app at the same level.",
            )
        deny_reason = window_deny_reason(current)
        if deny_reason is not None:
            raise ComputerError("APP_BLOCKED", deny_reason)
        if store is None:
            store = SnapshotStore(
                snapshot_id="",
                window_id=window_id,
                identity=current,
                created_at=time.time(),
                tree="",
                elements={},
                complete=True,
                truncated=False,
                identity_unproven=False,
                node_count=0,
            )
        return execute_actions(
            self._module,
            store,
            actions,
            should_abort=should_abort,
            capture_id=capture_id,
        )

    def _snapshot_sync(
        self,
        window_id: str,
        max_depth: int,
        max_nodes: int,
        max_chars: int,
        query: str,
    ) -> dict[str, Any]:
        hwnd = _parse_window_id(window_id)
        identity = win32.describe_window(hwnd)
        if identity is None:
            raise ComputerError(
                "WINDOW_NOT_FOUND",
                f"Window is gone: {window_id}",
            )
        observe_reason = observe_deny_reason(identity)
        if observe_reason is not None:
            raise ComputerError("APP_BLOCKED", observe_reason)
        if identity.minimized:
            raise ComputerError(
                "WINDOW_MINIMIZED",
                "The window is minimized; restore it before observing.",
                hint="Restore the window from the taskbar or launch it again.",
            )
        self._ensure_client()
        cache = self._create_cache()
        try:
            root = self._uia.ElementFromHandle(hwnd)
            root = root.BuildUpdatedCache(cache)
        except COMError as exc:
            raise ComputerError(
                "APP_UNRESPONSIVE",
                f"Unable to read the window: {exc}",
            ) from exc
        snapshot_id = f"snap-{uuid.uuid4().hex[:12]}"
        store = SnapshotStore(
            snapshot_id=snapshot_id,
            window_id=window_id,
            identity=identity,
            created_at=time.time(),
            tree="",
            elements={},
            complete=True,
            truncated=False,
            identity_unproven=(
                identity.pid <= 0
                or identity.creation_time <= 0
                or not identity.exe_name
            ),
            node_count=0,
        )
        state = {
            "nodes": 0,
            "chars": 0,
            "truncated": False,
        }
        lines = [
            (
                f'window "{_clean(identity.title, 120)}" '
                f'app={_clean(identity.exe_name, 60)} '
                f'pid={identity.pid}'
            )
        ]
        self._walk(
            root,
            1,
            max_depth,
            max_nodes,
            max_chars,
            query.strip().casefold(),
            cache,
            store,
            lines,
            state,
            frozenset(),
        )
        store.tree = "\n".join(lines)
        store.complete = not state["truncated"]
        store.truncated = bool(state["truncated"])
        store.node_count = int(state["nodes"])
        with self._lock:
            self._snapshots[snapshot_id] = store
            self._order.append(snapshot_id)
            while len(self._order) > _MAX_SNAPSHOTS:
                old = self._order.pop(0)
                self._snapshots.pop(old, None)
        elements = list(store.elements.values())[:_MAX_ELEMENTS_IN_RESULT]
        element_payloads = [element.to_json() for element in elements]
        focused_element = next(
            (
                element
                for element in element_payloads
                if "focused" in element["states"]
            ),
            None,
        )
        selected_elements = [
            element
            for element in element_payloads
            if "selected" in element["states"]
        ][:20]
        document_text = next(
            (
                element["value"]
                for element in element_payloads
                if element["role"] == "document"
                and element["value"]
            ),
            "",
        )
        deny_reason = window_deny_reason(identity)
        return {
            "snapshotId": snapshot_id,
            "window": {
                "id": window_id,
                "title": identity.title,
                "app": identity.exe_name,
                "exeName": identity.exe_name,
                "exePath": identity.exe_path,
                "pid": identity.pid,
                "processCreationTime": identity.creation_time,
                "bounds": identity.bounds.to_json(),
                "minimized": identity.minimized,
                "integrity": identity.integrity,
                "aumid": identity.aumid,
                "controllable": deny_reason is None,
                "denyReason": deny_reason,
            },
            "tree": store.tree,
            "elements": element_payloads,
            "focusedElement": focused_element,
            "selectedElements": selected_elements,
            "selectedText": "",
            "documentText": document_text,
            "complete": store.complete,
            "truncated": store.truncated,
            "identityUnproven": store.identity_unproven,
            "nodeCount": store.node_count,
        }

    def _walk(
        self,
        element: Any,
        depth: int,
        max_depth: int,
        max_nodes: int,
        max_chars: int,
        query: str,
        cache: Any,
        store: SnapshotStore,
        lines: list[str],
        state: dict[str, int | bool],
        ancestors: frozenset[tuple[int, ...]],
    ) -> None:
        if depth > max_depth:
            state["truncated"] = True
            return
        try:
            children = element.FindAllBuildCache(
                self._module.TreeScope_Children,
                self._condition,
                cache,
            )
        except COMError:
            state["truncated"] = True
            return
        try:
            count = int(children.Length)
        except (AttributeError, TypeError, ValueError):
            return
        for index in range(count):
            if int(state["nodes"]) >= max_nodes:
                state["truncated"] = True
                return
            try:
                child = children.GetElement(index)
            except COMError:
                state["truncated"] = True
                continue
            state["nodes"] = int(state["nodes"]) + 1
            runtime_id = self._runtime_id(child)
            if runtime_id is not None and runtime_id in ancestors:
                continue
            line = self._format_element(child, store, query)
            if line is not None:
                prefix = "  " * depth
                if int(state["chars"]) + len(prefix) + len(line) > max_chars:
                    state["truncated"] = True
                    return
                lines.append(prefix + line)
                state["chars"] = (
                    int(state["chars"]) + len(prefix) + len(line)
                )
            next_ancestors = (
                ancestors | {runtime_id}
                if runtime_id is not None
                else ancestors
            )
            self._walk(
                child,
                depth + 1,
                max_depth,
                max_nodes,
                max_chars,
                query,
                cache,
                store,
                lines,
                state,
                next_ancestors,
            )

    def _format_element(
        self,
        element: Any,
        store: SnapshotStore,
        query: str,
    ) -> str | None:
        try:
            role = self._control_type(element)
            name = self._cached_string(element, "CachedName")
            password = self._cached_bool(element, "CachedIsPassword")
            redacted = store.identity.integrity in ("high", "system")
            value = (
                ""
                if password or redacted
                else self._cached_property_string(
                    element,
                    self._module.UIA_ValueValuePropertyId,
                )
            )
            automation_id = self._cached_string(
                element,
                "CachedAutomationId",
            )
            actions = self._actions(element)
            states = self._states(element, password)
            if redacted:
                states = states + ("elevated",)
            focusable = self._cached_bool(
                element,
                "CachedIsKeyboardFocusable",
            )
            bbox = self._bounds(element)
            runtime_id = self._runtime_id(element)
        except COMError:
            return None
        if query and not any(
            query in part.casefold()
            for part in (name, value, automation_id, role)
        ):
            return None
        actionable = bool(actions) or focusable or role in _INTERACTIVE_ROLES
        if not name and not value and not automation_id and not actions:
            return None
        ref = ""
        if actionable:
            ref = f"@{store.snapshot_id}:e{len(store.elements) + 1}"
            store.elements[ref] = SnapshotElement(
                ref=ref,
                role=role,
                name=_clean(name, 120),
                value=_clean(value, 120),
                automation_id=_clean(automation_id, 80),
                bbox=bbox,
                actions=actions,
                states=states,
                runtime_id=runtime_id,
                password=password,
                element=element,
            )
        parts: list[str] = []
        if ref:
            parts.append(f"[{ref}]")
        parts.append(role)
        if name:
            parts.append(f'"{_clean(name, 80)}"')
        if value and value != name:
            parts.append(f'value="{_clean(value, 80)}"')
        if automation_id and automation_id != name:
            parts.append(f"id={_clean(automation_id, 60)}")
        if actions:
            parts.append("actions=" + ",".join(actions))
        if states:
            parts.append(" ".join(states))
        if ref:
            left, top, right, bottom = bbox
            parts.append(
                f"bbox={left},{top},{max(0, right - left)},"
                f"{max(0, bottom - top)}"
            )
        return " ".join(parts)

    def _ensure_client(self) -> None:
        if self._uia is not None:
            return
        try:
            import comtypes
            import comtypes.client
        except ImportError as exc:
            raise ComputerError(
                "UIA_UNAVAILABLE",
                "comtypes is not installed; computer observation is unavailable.",
            ) from exc
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except (AttributeError, OSError):
            pass
        module = comtypes.client.GetModule("UIAutomationCore.dll")
        try:
            uia = comtypes.client.CreateObject(
                module.CUIAutomation8,
                interface=module.IUIAutomation2,
            )
            uia.ConnectionTimeout = 2_000
            uia.TransactionTimeout = 20_000
        except (COMError, AttributeError, OSError):
            uia = comtypes.client.CreateObject(
                module.CUIAutomation8,
                interface=module.IUIAutomation,
            )
        self._module = module
        self._uia = uia
        self._condition = uia.ControlViewCondition
        self._control_types = {
            int(getattr(module, name)): name[
                len("UIA_") : -len("ControlTypeId")
            ].lower()
            for name in dir(module)
            if name.startswith("UIA_") and name.endswith("ControlTypeId")
        }

    def _create_cache(self) -> Any:
        cache = self._uia.CreateCacheRequest()
        cache.TreeScope = (
            self._module.TreeScope_Element
            | self._module.TreeScope_Children
        )
        for name in _CACHE_PROPERTIES:
            property_id = getattr(self._module, name, None)
            if property_id is not None:
                cache.AddProperty(property_id)
        for name in _CACHE_PATTERNS:
            pattern_id = getattr(self._module, name, None)
            if pattern_id is not None:
                cache.AddPattern(pattern_id)
        return cache

    def _control_type(self, element: Any) -> str:
        try:
            raw = int(getattr(element, "CachedControlType", 0))
        except (TypeError, ValueError, AttributeError):
            return "unknown"
        return self._control_types.get(raw, f"type-{raw}")

    def _cached_string(self, element: Any, name: str) -> str:
        try:
            value = getattr(element, name)
        except (AttributeError, COMError):
            return ""
        return str(value) if value is not None else ""

    def _cached_bool(
        self,
        element: Any,
        name: str,
        default: bool = False,
    ) -> bool:
        try:
            value = getattr(element, name)
        except (AttributeError, COMError):
            return default
        return bool(value)

    def _cached_property(
        self,
        element: Any,
        property_id: int,
        default: Any = None,
    ) -> Any:
        try:
            value = element.GetCachedPropertyValue(property_id)
        except COMError:
            return default
        return default if value is None else value

    def _cached_property_string(
        self,
        element: Any,
        property_id: int,
    ) -> str:
        value = self._cached_property(element, property_id, "")
        return str(value) if value is not None else ""

    def _actions(self, element: Any) -> tuple[str, ...]:
        actions: list[str] = []
        for property_name, action in _PATTERN_ACTIONS:
            property_id = getattr(self._module, property_name, None)
            if property_id is None:
                continue
            if bool(self._cached_property(element, property_id, False)):
                actions.append(action)
        return tuple(actions)

    def _states(
        self,
        element: Any,
        password: bool,
    ) -> tuple[str, ...]:
        states: list[str] = []
        if not self._cached_bool(element, "CachedIsEnabled", True):
            states.append("disabled")
        if self._cached_bool(element, "CachedIsOffscreen"):
            states.append("offscreen")
        if self._cached_bool(element, "CachedHasKeyboardFocus"):
            states.append("focused")
        if password:
            states.append("password")
        selected = self._cached_property(
            element,
            self._module.UIA_SelectionItemIsSelectedPropertyId,
            False,
        )
        if bool(selected):
            states.append("selected")
        toggle = self._cached_property(
            element,
            self._module.UIA_ToggleToggleStatePropertyId,
            -1,
        )
        if int(toggle) == 1:
            states.append("checked")
        elif int(toggle) == 0:
            states.append("unchecked")
        expand = self._cached_property(
            element,
            self._module.UIA_ExpandCollapseExpandCollapseStatePropertyId,
            -1,
        )
        if int(expand) == 0:
            states.append("collapsed")
        elif int(expand) == 1:
            states.append("expanded")
        return tuple(states)

    def _bounds(self, element: Any) -> tuple[int, int, int, int]:
        try:
            rect = element.CachedBoundingRectangle
            return (
                int(rect.left),
                int(rect.top),
                int(rect.right),
                int(rect.bottom),
            )
        except (AttributeError, COMError, TypeError, ValueError):
            return (0, 0, 0, 0)

    def _runtime_id(self, element: Any) -> tuple[int, ...] | None:
        value = self._cached_property(
            element,
            self._module.UIA_RuntimeIdPropertyId,
            None,
        )
        if not isinstance(value, (tuple, list)):
            return None
        try:
            return tuple(int(part) for part in value)
        except (TypeError, ValueError):
            return None


def _parse_window_id(window_id: str) -> int:
    if not isinstance(window_id, str) or not window_id.startswith("w-"):
        raise ComputerError(
            "INVALID_ARGS",
            "window_id must look like w-<hwnd>.",
        )
    try:
        return int(window_id[2:])
    except ValueError as exc:
        raise ComputerError(
            "INVALID_ARGS",
            "window_id must look like w-<hwnd>.",
        ) from exc


def _clean(value: str, limit: int) -> str:
    text = " ".join(str(value).replace('"', "'").split())
    return text[:limit]


_worker: UiaWorker | None = None
_worker_lock = threading.Lock()


def _get_worker() -> UiaWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = UiaWorker()
        return _worker


def snapshot_window(
    *,
    window_id: str,
    max_depth: int,
    max_nodes: int,
    max_chars: int,
    query: str,
) -> dict[str, Any]:
    return _get_worker().snapshot(
        window_id=window_id,
        max_depth=max_depth,
        max_nodes=max_nodes,
        max_chars=max_chars,
        query=query,
    )


def find_ref(
    *,
    window_id: str,
    snapshot_id: str,
    ref: str,
) -> dict[str, Any]:
    return _get_worker().find(
        window_id=window_id,
        snapshot_id=snapshot_id,
        ref=ref,
    )


def run_actions(
    *,
    window_id: str,
    snapshot_id: str,
    actions: list[dict[str, Any]],
    should_abort: Any = None,
    capture_id: str = "",
) -> dict[str, Any]:
    return _get_worker().act(
        window_id=window_id,
        snapshot_id=snapshot_id,
        actions=actions,
        should_abort=should_abort,
        capture_id=capture_id,
    )
