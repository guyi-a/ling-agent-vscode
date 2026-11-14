"""Configuration for the coding-agent harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .compaction import CompactionConfig


@dataclass(slots=True, frozen=True)
class ComputerUseConfig:
    enabled: bool = True
    helper_path: str = ""
    helper_init_timeout_seconds: float = 10.0
    helper_call_timeout_seconds: float = 30.0
    list_max_windows: int = 100
    session_ttl_seconds: float = 600.0
    session_idle_seconds: float = 90.0
    max_tree_nodes: int = 300
    max_tree_chars: int = 30_000
    audit_enabled: bool = True
    audit_root: str = ""
    denied_apps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.helper_init_timeout_seconds <= 0:
            raise ValueError(
                "helper_init_timeout_seconds must be positive"
            )
        if self.helper_call_timeout_seconds <= 0:
            raise ValueError(
                "helper_call_timeout_seconds must be positive"
            )
        if not 1 <= self.list_max_windows <= 500:
            raise ValueError("list_max_windows must be between 1 and 500")
        if self.session_ttl_seconds <= 0:
            raise ValueError("session_ttl_seconds must be positive")
        if self.session_idle_seconds <= 0:
            raise ValueError("session_idle_seconds must be positive")
        if self.max_tree_nodes < 1:
            raise ValueError("max_tree_nodes must be at least 1")
        if self.max_tree_chars < 1:
            raise ValueError("max_tree_chars must be at least 1")
        if not all(
            isinstance(value, str) and value.strip()
            for value in self.denied_apps
        ):
            raise ValueError(
                "denied_apps must contain non-empty executable names"
            )


@dataclass(slots=True, frozen=True)
class HarnessConfig:
    max_read_lines: int = 400
    max_read_chars: int = 50_000
    max_file_bytes: int = 1_000_000
    max_list_entries: int = 500
    max_grep_matches: int = 200
    max_command_output_chars: int = 50_000
    max_tool_result_chars: int = 50_000
    spill_threshold_chars: int = 12_000
    spill_preview_chars: int = 2_000
    max_attachment_image_bytes: int = 4 * 1024 * 1024
    max_document_bytes: int = 25 * 1024 * 1024
    max_document_text_chars: int = 256 * 1024
    max_document_xml_bytes: int = 64 * 1024 * 1024
    command_timeout_seconds: float = 120.0
    approval_timeout_seconds: float = 120.0
    prompt_max_chars: int = 100_000
    storage_root: Path | None = None
    agent_max_steps: int = 50
    agent_max_parallel_tools: int = 4
    subagent_max_steps: int = 30
    subagent_max_summary_chars: int = 8_000
    background_task_concurrency: int = 3
    background_task_notify_debounce_ms: int = 150
    background_command_log_max_chars: int = 20_000_000
    background_command_tail_chars: int = 8_000
    background_command_drain_timeout_seconds: float = 2.0
    browser_bridge_port: int = 17_323
    browser_bridge_connect_wait_seconds: float = 3.0
    browser_bridge_command_timeout_seconds: float = 30.0
    compaction: CompactionConfig = field(
        default_factory=CompactionConfig
    )
    computer_use: ComputerUseConfig = field(
        default_factory=ComputerUseConfig
    )

    def __post_init__(self) -> None:
        positive_ints = {
            "max_read_lines": self.max_read_lines,
            "max_read_chars": self.max_read_chars,
            "max_file_bytes": self.max_file_bytes,
            "max_list_entries": self.max_list_entries,
            "max_grep_matches": self.max_grep_matches,
            "max_command_output_chars": self.max_command_output_chars,
            "max_tool_result_chars": self.max_tool_result_chars,
            "spill_threshold_chars": self.spill_threshold_chars,
            "spill_preview_chars": self.spill_preview_chars,
            "max_attachment_image_bytes": self.max_attachment_image_bytes,
            "max_document_bytes": self.max_document_bytes,
            "max_document_text_chars": self.max_document_text_chars,
            "max_document_xml_bytes": self.max_document_xml_bytes,
            "prompt_max_chars": self.prompt_max_chars,
            "agent_max_steps": self.agent_max_steps,
            "agent_max_parallel_tools": self.agent_max_parallel_tools,
            "subagent_max_steps": self.subagent_max_steps,
            "subagent_max_summary_chars": self.subagent_max_summary_chars,
            "background_task_concurrency": self.background_task_concurrency,
            "background_task_notify_debounce_ms": (
                self.background_task_notify_debounce_ms
            ),
            "background_command_log_max_chars": (
                self.background_command_log_max_chars
            ),
            "background_command_tail_chars": (
                self.background_command_tail_chars
            ),
        }
        for name, value in positive_ints.items():
            if value < 1:
                raise ValueError(f"{name} must be at least 1")

        if self.command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        if self.background_command_drain_timeout_seconds <= 0:
            raise ValueError(
                "background_command_drain_timeout_seconds must be positive"
            )
        if self.approval_timeout_seconds <= 0:
            raise ValueError("approval_timeout_seconds must be positive")
        if not 0 <= self.browser_bridge_port <= 65_535:
            raise ValueError(
                "browser_bridge_port must be between 0 and 65535"
            )
        if self.browser_bridge_connect_wait_seconds < 0:
            raise ValueError(
                "browser_bridge_connect_wait_seconds must not be negative"
            )
        if self.browser_bridge_command_timeout_seconds <= 0:
            raise ValueError(
                "browser_bridge_command_timeout_seconds must be positive"
            )


__all__ = ["ComputerUseConfig", "HarnessConfig"]
