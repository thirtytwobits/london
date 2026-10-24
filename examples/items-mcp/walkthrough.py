"""Exercise the London Project's items server using the official MCP Python SDK."""

import argparse
import asyncio
import json
from pathlib import Path

from mcp import Client, StdioServerParameters


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


async def main(binary):
    for principal in ("agent", "human"):
        print(f"\n{principal} session")
        parameters = StdioServerParameters(
            command=str(binary), args=["--principal", principal]
        )
        async with Client(parameters, mode="2026-07-28") as client:
            assert client.protocol_version == "2026-07-28"
            tools = await client.list_tools()
            names = [tool.name for tool in tools.tools]
            expected = ["items.add", "items.list"]
            if principal == "human":
                expected.append("items.remove")
            assert names == expected, names
            show("tools/list", names)
            seeded = await call(client, "items.list", {})
            assert seeded == {
                "items": [
                    {"id": "a", "text": "Read a message"},
                    {"id": "b", "text": "Write a reply"},
                ]
            }
            if principal == "agent":
                item = {"id": "c", "text": "Add an item"}
                assert await call(client, "items.add", item) == item
                listed = await call(client, "items.list", {})
                assert listed == {"items": [*seeded["items"], item]}
                await call(client, "items.add", {"id": "d"}, "invalid_input", "invalid_input")
                await call(client, "items.add", item, "item_exists", "conflict")
                await call(client, "items.remove", {"id": "a"}, "not_admitted", "refused")
                assert await call(client, "items.list", {}) == listed
            else:
                assert await call(client, "items.remove", {"id": "a"}) == seeded["items"][0]
                await call(client, "items.remove", {"id": "absent"}, "item_not_found", "not_found")
                assert await call(client, "items.list", {}) == {"items": [seeded["items"][1]]}
    print("\nWalkthrough passed with the official MCP Python SDK.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="Path to the built items-mcp executable")
    args = parser.parse_args()
    asyncio.run(main(args.binary.resolve(strict=True)))
