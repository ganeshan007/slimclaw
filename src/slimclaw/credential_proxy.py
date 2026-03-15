"""Anthropic credential proxy.

Runs on the host at port 3001. Containers send requests with a placeholder
API key; this proxy replaces it with the real key and forwards to Anthropic.
Real secrets never enter containers.
"""
from __future__ import annotations

import asyncio
import ssl

PROXY_PORT = 3001
PLACEHOLDER_TOKEN = "slimclaw-proxy-token"
ANTHROPIC_HOST = "api.anthropic.com"
ANTHROPIC_PORT = 443


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    api_key: str,
) -> None:
    remote_writer: asyncio.StreamWriter | None = None
    try:
        # Read until end of HTTP headers
        header_buf = b""
        while b"\r\n\r\n" not in header_buf:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=30)
            if not chunk:
                return
            header_buf += chunk
            if len(header_buf) > 131072:
                return  # Header too large

        header_end = header_buf.index(b"\r\n\r\n") + 4
        headers_raw = header_buf[:header_end]
        body_start = header_buf[header_end:]

        # Security: only forward requests carrying our placeholder token
        placeholder = f"Bearer {PLACEHOLDER_TOKEN}".encode()
        if placeholder not in headers_raw:
            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return

        # Replace placeholder with real key, rewrite Host, force Connection: close
        headers_raw = headers_raw.replace(placeholder, f"Bearer {api_key}".encode())
        header_block = headers_raw.rstrip(b"\r\n")
        lines = header_block.split(b"\r\n")
        new_lines: list[bytes] = []
        has_connection = False
        for line in lines:
            lower = line.lower()
            if lower.startswith(b"host:"):
                new_lines.append(b"Host: " + ANTHROPIC_HOST.encode())
            elif lower.startswith(b"connection:"):
                new_lines.append(b"Connection: close")
                has_connection = True
            else:
                new_lines.append(line)
        if not has_connection:
            new_lines.append(b"Connection: close")
        headers_raw = b"\r\n".join(new_lines) + b"\r\n\r\n"

        # Open TLS connection to Anthropic
        ssl_ctx = ssl.create_default_context()
        remote_reader, remote_writer = await asyncio.open_connection(
            ANTHROPIC_HOST, ANTHROPIC_PORT, ssl=ssl_ctx, server_hostname=ANTHROPIC_HOST
        )

        # Forward modified request headers + any already-read body bytes
        remote_writer.write(headers_raw + body_start)
        await remote_writer.drain()

        # Bidirectional streaming: client→anthropic and anthropic→client
        await asyncio.gather(
            _pipe(reader, remote_writer),
            _pipe(remote_reader, writer),
            return_exceptions=True,
        )

    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError, OSError):
        pass
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass
        if remote_writer is not None:
            try:
                remote_writer.close()
            except Exception:
                pass


async def start_credential_proxy(api_key: str) -> asyncio.Server:
    """Start the Anthropic credential proxy on port 3001.

    Containers receive ANTHROPIC_API_KEY=slimclaw-proxy-token and
    ANTHROPIC_BASE_URL=http://host.docker.internal:3001. This server
    intercepts those requests, substitutes the real key, and forwards
    to api.anthropic.com over TLS.

    Returns the server object — call server.close() to stop it.
    """
    server = await asyncio.start_server(
        lambda r, w: _handle_connection(r, w, api_key),
        "0.0.0.0",
        PROXY_PORT,
    )
    return server
