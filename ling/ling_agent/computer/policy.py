"""Hard-deny policy for computer use.

The product default is to allow every non-sensitive application. These
processes are the safety boundary: terminals, password managers, and
Windows security surfaces are never controllable, even inside an active
control session.
"""

from __future__ import annotations

from .types import WindowIdentity

_HARD_DENY_AUMID_PREFIXES = (
    # Lock screen / secure logon surfaces hosted by LockApp/runtimebroker.
    "microsoft.lockapp_",
)

_PASSWORD_MANAGER_EXES = frozenset(
    {
        "keepass.exe",
        "keepassxc.exe",
        "1password.exe",
        "bitwarden.exe",
        "dashlane.exe",
        "lastpass.exe",
    }
)

_HARD_DENY_EXES = frozenset(
    {
        # UAC, logon, and Windows security surfaces.
        "consent.exe",
        "logonui.exe",
        "winlogon.exe",
        "lsass.exe",
        "csrss.exe",
        "smss.exe",
        "services.exe",
        "svchost.exe",
        "fontdrvhost.exe",
        "dwm.exe",
        # Terminals; use run_command instead.
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "windowsterminal.exe",
        "wt.exe",
        "conhost.exe",
        "openconsole.exe",
        # Password managers.
        "keepass.exe",
        "keepassxc.exe",
        "1password.exe",
        "bitwarden.exe",
        "dashlane.exe",
        "lastpass.exe",
        # System administration surfaces.
        "taskmgr.exe",
        "regedit.exe",
    }
)


def _normalize_exe(value: str) -> str:
    normalized = (value or "").strip().strip('"')
    normalized = normalized.replace("/", "\\").rsplit("\\", 1)[-1]
    normalized = normalized.lower()
    if normalized and "." not in normalized:
        return f"{normalized}.exe"
    return normalized


def hard_deny_reason(
    exe_name: str,
    denied_apps: tuple[str, ...] = (),
    aumid: str | None = None,
) -> str | None:
    """Return a human-readable reason when a process is never controllable."""

    normalized_aumid = (aumid or "").strip().lower()
    if any(
        normalized_aumid.startswith(prefix)
        for prefix in _HARD_DENY_AUMID_PREFIXES
    ):
        return "The Windows lock screen is blocked by computer-use policy."
    normalized = _normalize_exe(exe_name)
    if not normalized:
        return "Process identity is unavailable."
    if normalized in _HARD_DENY_EXES:
        return f"{normalized} is blocked by computer-use policy."
    if normalized in {
        _normalize_exe(value)
        for value in denied_apps
        if value.strip()
    }:
        return (
            f"{normalized} is blocked by your computer-use settings."
        )
    return None


def window_deny_reason(
    window: WindowIdentity,
    denied_apps: tuple[str, ...] = (),
) -> str | None:
    """Return why a window cannot be controlled, or None when it is allowed."""

    reason = hard_deny_reason(
        window.exe_name,
        denied_apps,
        window.aumid,
    )
    if reason is not None:
        return reason
    if window.integrity in ("high", "system"):
        return (
            "Elevated windows are observe-only and cannot be controlled."
        )
    if window.pid <= 0 or window.creation_time <= 0:
        return "Process identity is unavailable."
    return None


def uipi_blocked(window: WindowIdentity) -> bool:
    """Return whether UIPI blocks input from the medium-integrity helper."""

    return window.integrity in ("high", "system")


def observe_deny_reason(window: WindowIdentity) -> str | None:
    """Return why a window's UI tree must not be read at all."""

    normalized = _normalize_exe(window.exe_name)
    if normalized in _PASSWORD_MANAGER_EXES:
        return f"{normalized} cannot be observed by computer use."
    aumid = (window.aumid or "").strip().lower()
    if any(aumid.startswith(prefix) for prefix in _HARD_DENY_AUMID_PREFIXES):
        return "The Windows lock screen cannot be observed."
    return None


def is_controllable(
    window: WindowIdentity,
    denied_apps: tuple[str, ...] = (),
) -> bool:
    return window_deny_reason(window, denied_apps) is None
