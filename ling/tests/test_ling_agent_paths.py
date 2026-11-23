"""Tests for platform-specific user paths."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ling_agent import memory
from ling_agent import paths

if os.name == "nt":
    pytest.skip(
        "macOS path behavior",
        allow_module_level=True,
    )


def test_macos_paths_use_application_support() -> None:
    with tempfile.TemporaryDirectory() as temp:
        home = Path(temp)
        with (
            patch.dict(os.environ, {"HOME": temp}, clear=True),
            patch.object(paths.sys, "platform", "darwin"),
            patch.object(memory.sys, "platform", "darwin"),
        ):
            application_support = (
                home / "Library" / "Application Support"
            )
            assert paths.default_state_root() == (
                application_support / "ling-agent"
            )
            assert paths.default_config_root() == (
                application_support / "Ling"
            )
            assert paths.default_mcp_config_path() == (
                application_support / "Ling" / "mcp.json"
            )
            assert memory.default_global_memory_root() == (
                application_support / "Ling" / "memory"
            )


if __name__ == "__main__":
    test_macos_paths_use_application_support()
