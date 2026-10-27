"""Exercise the London Project's items server with each development grant, using the official MCP Python SDK."""

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

import httpx2
from mcp import Client, MCPError, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

import authorisation_server

SEEDS = [
    {"id": "a", "text": "Read a message", "owner": "operator"},
    {"id": "b", "text": "Write a reply", "owner": "operator"},
]


def show(label, result):
    print(f"{label}: {json.dumps(result, sort_keys=True)}")


async def call(client, name, arguments, code=None, category=None):
    result = await client.call_tool(name, arguments)
    value = result.structured_content
    if code is None:
        assert not result.is_error, value
    else:
        assert result.is_error, value
        assert value["code"] == code, value
        assert value["category"] == category, value
    show(name, value)
    return value


async def tools(client, expected):
    names = [tool.name for tool in (await client.list_tools()).tools]
    assert names == expected, names
    show("tools/list", names)


async def scope_refused(client, refusals):
    """Call items.remove, which the assistant grant lacks the scope for.

    Over stdio the refusal is a tool error. Over Streamable HTTP the server answers 403 before
    the request reaches MCP, and the SDK reports it as an MCP error.
    """
    if refusals is None:
        await call(client, "items.remove", {"id": "a"}, "insufficient_scope", "refused")
        return
    try:
        await client.call_tool("items.remove", {"id": "a"})
    except MCPError:
        status, challenge = refusals.pop()
        assert status == 403, status
        assert challenge == 'Bearer error="insufficient_scope", scope="items:remove"', challenge
        show("items.remove", {"status": status, "www-authenticate": challenge})
    else:
        raise AssertionError("items.remove was not refused")


async def assistant(client, refusals):
    await tools(client, ["items.add", "items.list"])
    assert await call(client, "items.list", {}) == {"items": SEEDS}
    item = {"id": "c", "text": "Add an item", "owner": "operator"}
    assert await call(client, "items.add", {"id": "c", "text": "Add an item"}) == item
    listed = await call(client, "items.list", {})
    assert listed == {"items": [*SEEDS, item]}
    await call(client, "items.add", {"id": "d"}, "invalid_input", "invalid_input")
    await call(client, "items.add", {"id": "c", "text": "Overwrite"}, "item_exists", "conflict")
    await scope_refused(client, refusals)
    assert await call(client, "items.list", {}) == listed


async def interactive(client, refusals):
    await tools(client, ["items.add", "items.list", "items.remove"])
    assert await call(client, "items.remove", {"id": "a"}) == SEEDS[0]
    await call(client, "items.remove", {"id": "absent"}, "item_not_found", "not_found")
    assert await call(client, "items.list", {}) == {"items": SEEDS[1:]}


async def colleague(client, refusals):
    await tools(client, ["items.add", "items.list", "items.remove"])
    await call(client, "items.remove", {"id": "a"}, "not_owner", "refused")
    item = {"id": "c", "text": "Add an item", "owner": "colleague"}
    assert await call(client, "items.add", {"id": "c", "text": "Add an item"}) == item
    assert await call(client, "items.remove", {"id": "c"}) == item
    assert await call(client, "items.list", {}) == {"items": SEEDS}


SESSIONS = [("assistant", assistant), ("interactive", interactive), ("colleague", colleague)]


async def over_stdio(binary, configuration, launch):
    for token, session in SESSIONS:
        print(f"\n{token} grant over stdio")
        parameters = StdioServerParameters(
            command=str(binary),
            args=["--configuration", str(configuration)],
            env={**launch, "ITEMS_ACCESS_TOKEN": token},
        )
        async with Client(parameters, mode="2026-07-28") as client:
            assert client.protocol_version == "2026-07-28"
            await session(client, None)


async def over_http(binary, configuration, launch):
    for token, session in SESSIONS:
        print(f"\n{token} grant over Streamable HTTP")
        server = subprocess.Popen(
            [binary, "--configuration", configuration, "--http", "127.0.0.1:0"],
            env=launch,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            endpoint = server.stdout.readline().strip()
            refusals = []

            async def record(response, refusals=refusals):
                if response.status_code >= 400:
                    refusals.append((response.status_code, response.headers.get("www-authenticate")))

            headers = {"Authorization": f"Bearer {token}"}
            async with httpx2.AsyncClient(headers=headers, event_hooks={"response": [record]}) as http:
                transport = streamable_http_client(endpoint, http_client=http)
                async with Client(transport, mode="2026-07-28") as client:
                    assert client.protocol_version == "2026-07-28"
                    await session(client, refusals)
        finally:
            server.terminate()
            server.wait()


async def main(binary, configuration):
    server, introspection = authorisation_server.start()
    client_id, secret = authorisation_server.resource_server()
    launch = {
        **os.environ,
        "ITEMS_INTROSPECTION_URL": introspection,
        "ITEMS_CLIENT_ID": client_id,
        "ITEMS_CLIENT_SECRET": secret,
    }
    try:
        await over_stdio(binary, configuration, launch)
        await over_http(binary, configuration, launch)
    finally:
        server.shutdown()
    print("\nWalkthrough passed with the official MCP Python SDK.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="Path to the built items-mcp executable")
    parser.add_argument(
        "--configuration", type=Path,
        default=Path(__file__).resolve().parent / "configuration.json",
        help="The server's configuration (default: configuration.json beside this script)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.binary.resolve(strict=True), args.configuration.resolve(strict=True)))
