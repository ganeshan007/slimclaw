"""Notion MCP host server.

Runs on the host at port 3002. The container-side notion-mcp-bridge.js
calls this server to execute Notion API operations. NOTION_API_KEY is
read once at startup and held in memory — it never enters containers.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

NOTION_PROXY_PORT = 3002
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _notion_request(method: str, path: str, body: dict | None, api_key: str) -> dict:
    """Make a synchronous Notion API call (run in executor)."""
    url = f"{NOTION_API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode()
        except Exception:
            pass
        return {"error": e.reason, "status": e.code, "body": body_text}
    except Exception as e:
        return {"error": str(e)}


def _dispatch(tool: str, tool_input: dict, api_key: str) -> dict:
    if tool == "notion_search":
        return _notion_request("POST", "/search", {"query": tool_input.get("query", "")}, api_key)

    elif tool == "notion_get_page":
        return _notion_request("GET", f"/pages/{tool_input['page_id']}", None, api_key)

    elif tool == "notion_get_blocks":
        return _notion_request("GET", f"/blocks/{tool_input['page_id']}/children", None, api_key)

    elif tool == "notion_create_page":
        parent_page_id = tool_input.get("parent_page_id")
        if parent_page_id:
            parent = {"page_id": parent_page_id}
        else:
            parent = {"type": "workspace", "workspace": True}
        payload: dict = {
            "parent": parent,
            "properties": {
                "title": {"title": [{"text": {"content": tool_input.get("title", "")}}]}
            },
        }
        if "body" in tool_input:
            payload["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": tool_input["body"]}}]},
                }
            ]
        return _notion_request("POST", "/pages", payload, api_key)

    elif tool == "notion_query_database":
        payload = {}
        if "filter" in tool_input:
            payload["filter"] = tool_input["filter"]
        if "sorts" in tool_input:
            payload["sorts"] = tool_input["sorts"]
        return _notion_request(
            "POST", f"/databases/{tool_input['database_id']}/query", payload, api_key
        )

    elif tool == "notion_append_blocks":
        return _notion_request(
            "PATCH",
            f"/blocks/{tool_input['page_id']}/children",
            {"children": tool_input["children"]},
            api_key,
        )

    elif tool == "notion_update_page":
        return _notion_request(
            "PATCH",
            f"/pages/{tool_input['page_id']}",
            {"properties": tool_input["properties"]},
            api_key,
        )

    else:
        return {"error": f"Unknown tool: {tool}"}


async def _handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    api_key: str,
) -> None:
    try:
        # Read HTTP request headers
        header_buf = b""
        while b"\r\n\r\n" not in header_buf:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=10)
            if not chunk:
                return
            header_buf += chunk

        header_end = header_buf.index(b"\r\n\r\n") + 4
        headers_raw = header_buf[:header_end].decode(errors="replace")
        body_buf = header_buf[header_end:]

        # Parse Content-Length to read the full body
        content_length = 0
        for line in headers_raw.split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
                break

        while len(body_buf) < content_length:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=10)
            if not chunk:
                break
            body_buf += chunk

        payload = json.loads(body_buf.decode())
        tool = payload.get("tool", "")
        tool_input = payload.get("input", {})

        # Execute blocking Notion API call in thread pool
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _dispatch, tool, tool_input, api_key),
            timeout=35,
        )

        resp_body = json.dumps(result).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: close\r\n"
            b"Content-Length: " + str(len(resp_body)).encode() + b"\r\n"
            b"\r\n" + resp_body
        )
        await writer.drain()

    except Exception as e:
        err_body = json.dumps({"error": str(e)}).encode()
        try:
            writer.write(
                b"HTTP/1.1 500 Internal Server Error\r\n"
                b"Content-Type: application/json\r\n"
                b"Connection: close\r\n"
                b"Content-Length: " + str(len(err_body)).encode() + b"\r\n"
                b"\r\n" + err_body
            )
            await writer.drain()
        except Exception:
            pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start_notion_mcp_server(api_key: str) -> asyncio.Server:
    """Start the Notion MCP host server on port 3002.

    The container-side notion-mcp-bridge.js calls POST /call with
    {"tool": "notion_search", "input": {...}}. This server executes
    the Notion API call using the real key and returns the result.

    Returns the server object — call server.close() to stop it.
    """
    server = await asyncio.start_server(
        lambda r, w: _handle_request(r, w, api_key),
        "0.0.0.0",
        NOTION_PROXY_PORT,
    )
    return server
