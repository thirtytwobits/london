"""Exercise the London Project's items server with each development grant, using the official MCP Python SDK."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import Client, StdioServerParameters

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


async def assistant(client):
    await tools(client, ["items.add", "items.list"])
    assert await call(client, "items.list", {}) == {"items": SEEDS}
    item = {"id": "c", "text": "Add an item", "owner": "operator"}
    assert await call(client, "items.add", {"id": "c", "text": "Add an item"}) == item
    listed = await call(client, "items.list", {})
    assert listed == {"items": [*SEEDS, item]}
    await call(client, "items.add", {"id": "d"}, "invalid_input", "invalid_input")
    await call(client, "items.add", {"id": "c", "text": "Overwrite"}, "item_exists", "conflict")
    await call(client, "items.remove", {"id": "a"}, "insufficient_scope", "refused")
    assert await call(client, "items.list", {}) == listed


async def interactive(client):
    await tools(client, ["items.add", "items.list", "items.remove"])
    assert await call(client, "items.remove", {"id": "a"}) == SEEDS[0]
    await call(client, "items.remove", {"id": "absent"}, "item_not_found", "not_found")
    assert await call(client, "items.list", {}) == {"items": SEEDS[1:]}


async def colleague(client):
    await tools(client, ["items.add", "items.list", "items.remove"])
    await call(client, "items.remove", {"id": "a"}, "not_owner", "refused")
    item = {"id": "c", "text": "Add an item", "owner": "colleague"}
    assert await call(client, "items.add", {"id": "c", "text": "Add an item"}) == item
    assert await call(client, "items.remove", {"id": "c"}) == item
    assert await call(client, "items.list", {}) == {"items": SEEDS}


async def main(binary, configuration):
    server, introspection = authorisation_server.start()
    client_id, secret = authorisation_server.resource_server()
    try:
        for token, session in [("assistant", assistant), ("interactive", interactive), ("colleague", colleague)]:
            print(f"\n{token} grant")
            parameters = StdioServerParameters(
                command=str(binary),
                args=["--configuration", str(configuration)],
                env={
                    **os.environ,
                    "ITEMS_ACCESS_TOKEN": token,
                    "ITEMS_INTROSPECTION_URL": introspection,
                    "ITEMS_CLIENT_ID": client_id,
                    "ITEMS_CLIENT_SECRET": secret,
                },
            )
            async with Client(parameters, mode="2026-07-28") as client:
                assert client.protocol_version == "2026-07-28"
                await session(client)
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
