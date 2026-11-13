"""Tolerant user-level MCP configuration for all workspaces."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..paths import default_mcp_config_path

MAX_SERVER_NAME_CHARS = 64


@dataclass(slots=True, frozen=True)
class McpConfigIssue:
    message: str
    name: str | None = None


@dataclass(slots=True, frozen=True)
class McpServerConfig:
    name: str
    kind: str
    enabled: bool = True
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    auth: str = "none"
    oauth_scope: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    trust_annotations: bool = False
    init_timeout_seconds: float = 30.0
    call_timeout_seconds: float = 120.0

    @property
    def target(self) -> str:
        if self.kind == "http":
            return self.url or ""
        return " ".join((self.command or "", *self.args)).strip()

    def pool_key(self) -> str:
        payload = {
            "name": self.name,
            "kind": self.kind,
            "url": self.url,
            "headers": sorted(self.headers.items()),
            "auth": self.auth,
            "oauthScope": self.oauth_scope,
            "command": self.command,
            "args": self.args,
            "env": sorted(self.env.items()),
            "cwd": self.cwd,
            "trustAnnotations": self.trust_annotations,
            "initTimeoutSeconds": self.init_timeout_seconds,
            "callTimeoutSeconds": self.call_timeout_seconds,
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_file_entry(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.kind,
            "enabled": self.enabled,
        }
        if self.kind == "http":
            payload["url"] = self.url
            if self.headers:
                payload["headers"] = dict(self.headers)
            if self.auth != "none":
                payload["auth"] = self.auth
            if self.oauth_scope:
                payload["scope"] = self.oauth_scope
        else:
            payload["command"] = self.command
            if self.args:
                payload["args"] = list(self.args)
            if self.env:
                payload["env"] = dict(self.env)
            if self.cwd:
                payload["cwd"] = self.cwd
        if self.trust_annotations:
            payload["trustAnnotations"] = True
        if self.init_timeout_seconds != 30.0:
            payload["initTimeoutSeconds"] = self.init_timeout_seconds
        if self.call_timeout_seconds != 120.0:
            payload["callTimeoutSeconds"] = self.call_timeout_seconds
        return payload


@dataclass(slots=True, frozen=True)
class McpConfig:
    path: Path
    servers: tuple[McpServerConfig, ...] = ()
    issues: tuple[McpConfigIssue, ...] = ()

    def get(self, name: str) -> McpServerConfig | None:
        return next(
            (server for server in self.servers if server.name == name),
            None,
        )


def load_mcp_config(
    root: Path,
    *,
    path: str | Path | None = None,
) -> McpConfig:
    config_path = (
        Path(path).expanduser().resolve()
        if path is not None
        else default_mcp_config_path().expanduser().resolve()
    )
    if not config_path.exists():
        return McpConfig(path=config_path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return McpConfig(
            path=config_path,
            issues=(
                McpConfigIssue(
                    message=f"Unable to parse {config_path}: {exc}"
                ),
            ),
        )

    if not isinstance(raw, dict) or not isinstance(
        raw.get("mcpServers"),
        dict,
    ):
        return McpConfig(
            path=config_path,
            issues=(
                McpConfigIssue(
                    message=(
                        "The top-level MCP config must be "
                        '{"mcpServers": {...}}.'
                    )
                ),
            ),
        )

    servers: list[McpServerConfig] = []
    issues: list[McpConfigIssue] = []
    for name, value in raw["mcpServers"].items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > MAX_SERVER_NAME_CHARS
        ):
            issues.append(
                McpConfigIssue(
                    message=(
                        f"MCP server names must be 1-{MAX_SERVER_NAME_CHARS} "
                        "characters."
                    ),
                    name=str(name),
                )
            )
            continue
        parsed = _parse_server(root, name.strip(), value)
        if isinstance(parsed, McpConfigIssue):
            issues.append(parsed)
        else:
            servers.append(parsed)

    servers.sort(key=lambda server: server.name.casefold())
    return McpConfig(
        path=config_path,
        servers=tuple(servers),
        issues=tuple(issues),
    )


class McpConfigStore:
    """Read and atomically update the global MCP config file."""

    def __init__(
        self,
        root: str | Path,
        *,
        path: str | Path | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else default_mcp_config_path().expanduser().resolve()
        )

    def ensure_file(self) -> Path:
        if self.path.exists():
            return self.path
        self._write_document({"mcpServers": {}})
        return self.path

    def load(self) -> McpConfig:
        return load_mcp_config(self.root, path=self.path)

    def upsert(self, server: McpServerConfig) -> None:
        document = self._read_document()
        servers = document.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError(
                'The top-level "mcpServers" value must be an object.'
            )
        servers[server.name] = server.to_file_entry()
        self._write_document(document)

    def set_enabled(self, name: str, enabled: bool) -> McpServerConfig:
        config = self.load()
        server = config.get(name)
        if server is None:
            raise ValueError(f"Unknown MCP server: {name}")
        updated = replace(server, enabled=enabled)
        self.upsert(updated)
        return updated

    def remove(self, name: str) -> None:
        document = self._read_document()
        servers = document.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError(
                'The top-level "mcpServers" value must be an object.'
            )
        if name not in servers:
            raise ValueError(f"Unknown MCP server: {name}")
        del servers[name]
        self._write_document(document)

    def _read_document(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"mcpServers": {}}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Unable to parse {self.path}: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise ValueError("The MCP config must be a JSON object.")
        return document

    def _write_document(self, document: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.tmp-{os.getpid()}"
        )
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def _parse_server(
    root: Path,
    name: str,
    value: object,
) -> McpServerConfig | McpConfigIssue:
    if not isinstance(value, dict):
        return McpConfigIssue(
            name=name,
            message=f"MCP server {name!r} must be an object.",
        )

    url = value.get("url")
    command = value.get("command")
    if isinstance(url, str) and url.strip() and command is None:
        return _parse_http_server(name, value, url.strip())
    if isinstance(command, str) and command.strip() and url is None:
        return _parse_stdio_server(root, name, value, command.strip())
    return McpConfigIssue(
        name=name,
        message=(
            f'MCP server {name!r} must provide exactly one of "url" '
            'or "command".'
        ),
    )


def _parse_http_server(
    name: str,
    value: dict[str, Any],
    url: str,
) -> McpServerConfig | McpConfigIssue:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return McpConfigIssue(
            name=name,
            message=f'MCP server {name!r} has an invalid "url".',
        )
    headers = _string_map(value.get("headers"), "headers")
    if isinstance(headers, McpConfigIssue):
        return replace(headers, name=name)
    auth = value.get("auth", "none")
    if auth not in ("none", "oauth"):
        return McpConfigIssue(
            name=name,
            message=f'MCP server {name!r} "auth" must be "none" or "oauth".',
        )
    if auth == "oauth" and any(
        key.lower() == "authorization" for key in headers
    ):
        return McpConfigIssue(
            name=name,
            message=(
                f'MCP server {name!r} cannot combine "auth": "oauth" '
                "with a static Authorization header."
            ),
        )
    scope = value.get("scope", value.get("oauthScope"))
    if scope is not None and not isinstance(scope, str):
        return McpConfigIssue(
            name=name,
            message=f'MCP server {name!r} "scope" must be a string.',
        )
    return McpServerConfig(
        name=name,
        kind="http",
        enabled=_enabled(value),
        url=url,
        headers=headers,
        auth=auth,
        oauth_scope=scope.strip() if isinstance(scope, str) and scope.strip() else None,
        trust_annotations=_trust_annotations(value),
        init_timeout_seconds=_timeout(
            value,
            "initTimeoutSeconds",
            30.0,
        ),
        call_timeout_seconds=_timeout(
            value,
            "callTimeoutSeconds",
            120.0,
        ),
    )


def _parse_stdio_server(
    root: Path,
    name: str,
    value: dict[str, Any],
    command: str,
) -> McpServerConfig | McpConfigIssue:
    args = value.get("args", [])
    if not isinstance(args, list) or not all(
        isinstance(item, str) for item in args
    ):
        return McpConfigIssue(
            name=name,
            message=f'MCP server {name!r} "args" must be an array of strings.',
        )
    env = _string_map(value.get("env"), "env")
    if isinstance(env, McpConfigIssue):
        return replace(env, name=name)
    cwd = value.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        return McpConfigIssue(
            name=name,
            message=f'MCP server {name!r} "cwd" must be a string.',
        )
    if isinstance(cwd, str) and cwd.strip():
        candidate = Path(cwd).expanduser()
        resolved = (
            candidate.resolve()
            if candidate.is_absolute()
            else (root / candidate).resolve()
        )
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            return McpConfigIssue(
                name=name,
                message=f'MCP server {name!r} "cwd" escapes the workspace.',
            )
        cwd = str(resolved)
    return McpServerConfig(
        name=name,
        kind="stdio",
        enabled=_enabled(value),
        command=command,
        args=tuple(args),
        env=env,
        cwd=cwd,
        trust_annotations=_trust_annotations(value),
        init_timeout_seconds=_timeout(
            value,
            "initTimeoutSeconds",
            30.0,
        ),
        call_timeout_seconds=_timeout(
            value,
            "callTimeoutSeconds",
            120.0,
        ),
    )


def _enabled(value: dict[str, Any]) -> bool:
    enabled = value.get("enabled", True)
    return enabled if isinstance(enabled, bool) else True


def _trust_annotations(value: dict[str, Any]) -> bool:
    trusted = value.get(
        "trustAnnotations",
        value.get("trust_annotations", False),
    )
    return trusted if isinstance(trusted, bool) else False


def _timeout(
    value: dict[str, Any],
    key: str,
    default: float,
) -> float:
    candidate = value.get(key, default)
    if (
        isinstance(candidate, (int, float))
        and not isinstance(candidate, bool)
        and 1.0 <= float(candidate) <= 600.0
    ):
        return float(candidate)
    return default


def _string_map(
    value: object,
    field_name: str,
) -> dict[str, str] | McpConfigIssue:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(key, str)
        and isinstance(item, str)
        for key, item in value.items()
    ):
        return McpConfigIssue(
            message=f'"{field_name}" must be an object of string values.'
        )
    return dict(value)


__all__ = [
    "MAX_SERVER_NAME_CHARS",
    "McpConfig",
    "McpConfigIssue",
    "McpConfigStore",
    "McpServerConfig",
    "load_mcp_config",
]
