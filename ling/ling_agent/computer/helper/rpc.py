"""NDJSON framing for the computer-use helper."""

from __future__ import annotations

import json
from typing import Any


def encode_message(payload: dict[str, Any]) -> str:
    """Encode one JSON-RPC message as a single NDJSON line."""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(
    request_id: Any,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}
