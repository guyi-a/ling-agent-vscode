"""JSON-RPC sidecar entry points for the Ling VS Code extension."""

from .server import PROTOCOL_VERSION, JsonRpcServer

__all__ = ["JsonRpcServer", "PROTOCOL_VERSION"]
