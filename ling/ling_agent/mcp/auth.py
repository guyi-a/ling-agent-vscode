"""OAuth PKCE loopback flow and encrypted credential storage."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from mcp.client.auth import OAuthClientProvider, OAuthFlowError, TokenStorage
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from .config import McpServerConfig

_CREDENTIAL_HEADER_DPAPI = b"LING-MCP-DPAPI-1\n"
_CREDENTIAL_HEADER_LOCAL = b"LING-MCP-LOCAL-1\n"
_CREDENTIAL_HEADER_KEYCHAIN = b"LING-MCP-KEYCHAIN-2\n"
_DEFAULT_KEYCHAIN_SERVICE = "ling-agent.mcp-oauth"
_KEYCHAIN_VALUE_PREFIX = "LING-MCP-KEYCHAIN-KEY-1:"
_KEYCHAIN_KEY_BYTES = 32
_KEYCHAIN_NONCE_BYTES = 12
_KEYCHAIN_AAD = b"ling-mcp-oauth"


class McpCredentialError(RuntimeError):
    pass


class McpTokenStore(TokenStorage):
    """Persist OAuth registration and tokens without putting them in mcp.json."""

    def __init__(
        self,
        auth_root: Path,
        server: McpServerConfig,
    ) -> None:
        identity = f"{server.name}\n{server.url or ''}".encode("utf-8")
        self._account = hashlib.sha256(identity).hexdigest()
        self.path = auth_root.resolve() / f"{self._account}.bin"
        self._lock = asyncio.Lock()

    async def get_tokens(self) -> OAuthToken | None:
        payload = await self._read()
        raw = payload.get("tokens")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthToken.model_validate(raw)
        except Exception:
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        async with self._lock:
            payload = await self._read_unlocked()
            payload["tokens"] = tokens.model_dump(
                mode="json",
                exclude_none=True,
            )
            self._write(payload)

    async def get_client_info(
        self,
    ) -> OAuthClientInformationFull | None:
        payload = await self._read()
        raw = payload.get("clientInfo")
        if not isinstance(raw, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(raw)
        except Exception:
            return None

    async def set_client_info(
        self,
        client_info: OAuthClientInformationFull,
    ) -> None:
        async with self._lock:
            payload = await self._read_unlocked()
            payload["clientInfo"] = client_info.model_dump(
                mode="json",
                exclude_none=True,
            )
            self._write(payload)

    async def clear(self) -> None:
        async with self._lock:
            if sys.platform == "darwin":
                _keychain_delete(self._account)
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                return
            try:
                self.path.unlink()
            except FileNotFoundError:
                return

    async def authorized(self) -> bool:
        tokens = await self.get_tokens()
        return bool(tokens and tokens.access_token)

    async def _read(self) -> dict[str, object]:
        async with self._lock:
            return await self._read_unlocked()

    async def _read_unlocked(self) -> dict[str, object]:
        if sys.platform == "darwin":
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                return {}
            try:
                plain = _decrypt_keychain_payload(
                    raw,
                    _keychain_read(self._account),
                )
                value = json.loads(plain.decode("utf-8"))
            except Exception as exc:
                raise McpCredentialError(
                    f"Unable to read stored MCP credentials: {exc}"
                ) from exc
            return value if isinstance(value, dict) else {}
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        try:
            plain = _unprotect(raw)
            value = json.loads(plain.decode("utf-8"))
        except Exception as exc:
            raise McpCredentialError(
                f"Unable to read stored MCP credentials: {exc}"
            ) from exc
        return value if isinstance(value, dict) else {}

    def _write(self, payload: dict[str, object]) -> None:
        plain = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if sys.platform == "darwin":
            key = _keychain_read(self._account)
            if key is None:
                key = secrets.token_bytes(_KEYCHAIN_KEY_BYTES)
                _keychain_write(self._account, key)
            raw = _encrypt_keychain_payload(key, plain)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f"{self.path.name}.tmp-{os.getpid()}"
            )
            temporary.write_bytes(raw)
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary, self.path)
            return
        raw = _protect(plain)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.tmp-{os.getpid()}"
        )
        temporary.write_bytes(raw)
        os.replace(temporary, self.path)
        if os.name != "nt":
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    @staticmethod
    def remove_stored(path: Path) -> None:
        if sys.platform == "darwin":
            try:
                _keychain_delete(path.stem)
            except McpCredentialError:
                pass
        try:
            path.unlink()
        except OSError:
            pass


class OAuthLoopback:
    """One-shot localhost callback server used by the authorization code flow."""

    def __init__(self, timeout_seconds: float = 300.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._server: asyncio.AbstractServer | None = None
        self._future: asyncio.Future[tuple[str, str | None]] | None = None
        self.redirect_uri = ""
        self.authorization_url: str | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._future = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(
            self._handle_callback,
            host="127.0.0.1",
            port=0,
        )
        socket = self._server.sockets[0]
        port = socket.getsockname()[1]
        self.redirect_uri = (
            f"http://127.0.0.1:{port}/oauth/callback"
        )

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def redirect(self, authorization_url: str) -> None:
        self.authorization_url = authorization_url
        opened = await asyncio.to_thread(
            webbrowser.open,
            authorization_url,
        )
        if not opened:
            raise OAuthFlowError(
                "Unable to open the browser for MCP authorization"
            )

    async def wait_for_code(self) -> tuple[str, str | None]:
        if self._future is None:
            raise OAuthFlowError("OAuth callback server is not running")
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._future),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise OAuthFlowError(
                "Timed out waiting for MCP authorization"
            ) from exc

    async def _handle_callback(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_request = await reader.readline()
            request = raw_request.decode("ascii", errors="replace").strip()
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            if not request:
                await _write_http(writer, 400, "Invalid callback request.")
                return
            parts = request.split(" ")
            if len(parts) < 2 or parts[0] != "GET":
                await _write_http(writer, 405, "Method not allowed.")
                return
            parsed = urlparse(parts[1])
            if parsed.path != "/oauth/callback":
                await _write_http(writer, 404, "Not found.")
                return
            query = parse_qs(parsed.query)
            if error := query.get("error"):
                message = (
                    query.get("error_description", [error[0]])[0]
                )
                raise OAuthFlowError(
                    f"MCP authorization failed: {message}"
                )
            code = query.get("code", [None])[0]
            state = query.get("state", [None])[0]
            if not code:
                raise OAuthFlowError(
                    "MCP authorization callback had no code"
                )
            if self._future is not None and not self._future.done():
                self._future.set_result((code, state))
            await _write_http(
                writer,
                200,
                "Ling MCP authorization complete. You can close this tab.",
            )
        except OAuthFlowError as exc:
            if self._future is not None and not self._future.done():
                self._future.set_exception(exc)
            await _write_http(writer, 400, str(exc))
        finally:
            writer.close()
            await writer.wait_closed()


def create_oauth_provider(
    server: McpServerConfig,
    store: McpTokenStore,
    loopback: OAuthLoopback | None,
) -> OAuthClientProvider:
    redirect_uris = (
        [loopback.redirect_uri]
        if loopback is not None and loopback.redirect_uri
        else None
    )
    metadata = OAuthClientMetadata(
        redirect_uris=redirect_uris,
        token_endpoint_auth_method="none",
        client_name="Ling",
        software_id="ling-agent",
        software_version="0.1.0",
        scope=server.oauth_scope,
    )
    return OAuthClientProvider(
        server.url or "",
        metadata,
        store,
        redirect_handler=(
            loopback.redirect if loopback is not None else None
        ),
        callback_handler=(
            loopback.wait_for_code if loopback is not None else None
        ),
    )


def _protect(data: bytes) -> bytes:
    if os.name == "nt":
        try:
            import win32crypt
        except ImportError as exc:
            raise McpCredentialError(
                "Windows DPAPI is unavailable; refusing to store OAuth "
                "credentials in plaintext."
            ) from exc
        protected = win32crypt.CryptProtectData(
            data,
            "Ling MCP OAuth credential",
            None,
            None,
            None,
            0,
        )
        return _CREDENTIAL_HEADER_DPAPI + base64.b64encode(protected)
    return _CREDENTIAL_HEADER_LOCAL + base64.b64encode(data)


def _keychain_write(account: str, data: bytes) -> None:
    secret = _KEYCHAIN_VALUE_PREFIX + base64.b64encode(data).decode("ascii")
    result = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-a",
            account,
            "-s",
            _keychain_service(),
            "-U",
            "-w",
        ],
        input=f"{secret}\n{secret}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise McpCredentialError(
            "Unable to store MCP credentials in macOS Keychain"
            + (f": {detail}" if detail else "")
        )


def _keychain_read(account: str) -> bytes | None:
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            _keychain_service(),
            "-w",
        ],
        capture_output=True,
        check=False,
    )
    if (
        result.returncode == 44
        or b"could not be found" in result.stderr.lower()
    ):
        return None
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise McpCredentialError(
            "Unable to read MCP credentials from macOS Keychain"
            + (f": {detail}" if detail else "")
        )
    value = result.stdout.rstrip(b"\r\n")
    prefix = _KEYCHAIN_VALUE_PREFIX.encode("ascii")
    if not value.startswith(prefix):
        # Ignore values from pre-release builds that used the raw JSON format.
        return None
    try:
        return base64.b64decode(value[len(prefix) :], validate=True)
    except ValueError as exc:
        raise McpCredentialError(
            "Stored MCP Keychain value is malformed"
        ) from exc


def _keychain_delete(account: str) -> None:
    result = subprocess.run(
        [
            "/usr/bin/security",
            "delete-generic-password",
            "-a",
            account,
            "-s",
            _keychain_service(),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode in (0, 44):
        return
    if b"could not be found" in result.stderr.lower():
        return
    detail = result.stderr.decode("utf-8", errors="replace").strip()
    raise McpCredentialError(
        "Unable to remove MCP credentials from macOS Keychain"
        + (f": {detail}" if detail else "")
    )


def _keychain_service() -> str:
    return (
        os.environ.get("LING_MCP_KEYCHAIN_SERVICE", "").strip()
        or _DEFAULT_KEYCHAIN_SERVICE
    )


def _encrypt_keychain_payload(key: bytes, plain: bytes) -> bytes:
    if len(key) != _KEYCHAIN_KEY_BYTES:
        raise McpCredentialError("Stored MCP Keychain key is malformed")
    nonce = secrets.token_bytes(_KEYCHAIN_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plain, _KEYCHAIN_AAD)
    return _CREDENTIAL_HEADER_KEYCHAIN + nonce + ciphertext


def _decrypt_keychain_payload(
    raw: bytes,
    key: bytes | None,
) -> bytes:
    if not raw.startswith(_CREDENTIAL_HEADER_KEYCHAIN):
        raise McpCredentialError("Unknown macOS credential format")
    if key is None or len(key) != _KEYCHAIN_KEY_BYTES:
        raise McpCredentialError("Missing macOS Keychain credential key")
    offset = len(_CREDENTIAL_HEADER_KEYCHAIN)
    nonce = raw[offset : offset + _KEYCHAIN_NONCE_BYTES]
    if len(nonce) != _KEYCHAIN_NONCE_BYTES:
        raise McpCredentialError("Stored MCP credential is truncated")
    ciphertext = raw[offset + _KEYCHAIN_NONCE_BYTES :]
    return AESGCM(key).decrypt(nonce, ciphertext, _KEYCHAIN_AAD)


def _unprotect(raw: bytes) -> bytes:
    if raw.startswith(_CREDENTIAL_HEADER_DPAPI):
        if os.name != "nt":
            raise McpCredentialError(
                "DPAPI credential was moved from Windows."
            )
        try:
            import win32crypt
        except ImportError as exc:
            raise McpCredentialError(
                "Windows DPAPI is unavailable."
            ) from exc
        protected = base64.b64decode(
            raw[len(_CREDENTIAL_HEADER_DPAPI) :]
        )
        return win32crypt.CryptUnprotectData(
            protected,
            None,
            None,
            None,
            0,
        )[1]
    if raw.startswith(_CREDENTIAL_HEADER_LOCAL):
        return base64.b64decode(raw[len(_CREDENTIAL_HEADER_LOCAL) :])
    raise McpCredentialError("Unknown MCP credential format.")


async def _write_http(
    writer: asyncio.StreamWriter,
    status: int,
    message: str,
) -> None:
    reason = "OK" if status == 200 else "Error"
    body = (
        "<!doctype html><meta charset=\"utf-8\">"
        f"<title>Ling MCP</title><body style=\"font-family:sans-serif;"
        "padding:32px\"><h2>Ling MCP</h2>"
        f"<p>{message}</p></body>"
    ).encode("utf-8")
    writer.write(
        (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        + body
    )
    await writer.drain()


__all__ = [
    "McpCredentialError",
    "McpTokenStore",
    "OAuthLoopback",
    "create_oauth_provider",
]
