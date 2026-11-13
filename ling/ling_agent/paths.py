"""User-level application directories shared by sidecar subsystems."""

from __future__ import annotations

import os
from pathlib import Path


def default_state_root() -> Path:
    """Local machine state: history, caches, and credentials."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "ling-agent"
        return Path.home() / "AppData" / "Local" / "ling-agent"
    if os.environ.get("XDG_STATE_HOME"):
        return Path(os.environ["XDG_STATE_HOME"]) / "ling-agent"
    return Path.home() / ".local" / "state" / "ling-agent"


def default_config_root() -> Path:
    """Roaming user configuration such as global memory and MCP servers."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "Ling"
        return Path.home() / "AppData" / "Roaming" / "Ling"
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "ling-agent"
    return Path.home() / ".config" / "ling-agent"


def default_mcp_config_path() -> Path:
    override = os.environ.get("LING_MCP_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return default_config_root() / "mcp.json"


def default_mcp_state_root() -> Path:
    override = os.environ.get("LING_MCP_STATE_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return default_state_root()


__all__ = [
    "default_config_root",
    "default_mcp_config_path",
    "default_mcp_state_root",
    "default_state_root",
]
