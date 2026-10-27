# An item list served over MCP

This example builds a small Model Context Protocol (MCP) server in Rust, served over stdio or
Streamable HTTP. A client connects to the server to discover and call three tools: list items,
add an item and remove an item.
What a connection may do comes from the OAuth grant its access token carries: the grant's
scopes decide which tools it can use, and its subject decides which items it can remove.

[Clapham](https://github.com/thirtytwobits/clapham) supplies the MCP server, verifies the
token and enforces those permissions. It is the only London Project piece this example uses.
The application supplies the tools and keeps the items in memory. Each server process starts
with two items, both added by the subject `operator`; its list lasts until the process exits.

## Run the walkthrough

You need Rust 1.88 or later and Python 3.11 or later. The walkthrough is a Python client that
starts a development authorisation server, launches the Rust server once for each of three
development tokens, makes a fixed sequence of MCP calls and prints the replies. It does this
over stdio and then over Streamable HTTP.

On macOS or Linux, run these commands from the repository root:

```console
cd examples/items-mcp
cargo build --locked
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python walkthrough.py target/debug/items-mcp
```

On Windows, run these commands from the repository root in PowerShell:

```powershell
cd examples/items-mcp
cargo build --locked
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python walkthrough.py target\debug\items-mcp.exe
```

A successful run ends with:

```text
Walkthrough passed with the official MCP Python SDK.
```

## Walkthrough results

[`development-grants.json`](development-grants.json) defines the three tokens:

| Token | Subject | Scopes |
|---|---|---|
| `assistant` | `operator` | `items:read items:write` |
| `interactive` | `operator` | `items:read items:write items:remove` |
| `colleague` | `colleague` | `items:read items:write items:remove` |

The `assistant` and `interactive` grants belong to the same subject. An assistant connection
carries the narrower grant, so a model using it cannot remove items, while the same person's
interactive client can.

With the `assistant` grant, the server lists `items.add` and `items.list`. The client reads
the two initial items, adds a third and reads the list again. It then makes three calls that
the server refuses:

| Call | Reply | Reason |
|---|---|---|
| Add an item without text | `invalid_input` | The input does not match the tool's schema. |
| Add the same ID again | `item_exists` | An item already has that ID. |
| Remove an item | `insufficient_scope` | The grant lacks `items:remove`. |

The client calls `items.remove` by name although the server left it out of the tool list.
Clapham checks the grant's scopes on every call, so the call is refused and the items are
unchanged. Over Streamable HTTP the refusal is an HTTP 403 whose challenge,
`Bearer error="insufficient_scope", scope="items:remove"`, names the scope a client would ask
its authorisation server for.

With the `interactive` grant, the server lists all three tools. Removing item `a` succeeds;
removing an unknown ID returns `item_not_found`.

With the `colleague` grant, the server also lists all three tools, but removing item `a`
returns `not_owner`: the grant has the scope, and the item belongs to another subject. The
colleague can add an item and remove it again.

## Authorisation

The server is an OAuth resource server. [`configuration.json`](configuration.json) names its
issuer and audience, and the server verifies tokens at the authorisation server's
[RFC 7662](https://www.rfc-editor.org/rfc/rfc7662) introspection endpoint. Over stdio it
verifies the launch token once and binds the grant to the connection; a token that is inactive,
or issued for another issuer or audience, stops the server before it answers. Over Streamable
HTTP it verifies each request's bearer token before reading the request, and refuses one it
cannot verify with HTTP 401. Tool arguments and request metadata cannot change the grant.

[`authorisation_server.py`](authorisation_server.py) stands in for an application's
authorisation server. It answers introspection on loopback for the development tokens only.
A deployment uses its own authorisation server, which issues, refreshes and revokes tokens.

## Connect another MCP client

The walkthrough uses the [official MCP Python SDK 2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0).
Another client must support MCP 2026-07-28 and the stdio transport; for Streamable HTTP, see
the next section. Start the development
authorisation server from `examples/items-mcp` and leave it running:

```console
python authorisation_server.py
```

Then configure the client to launch the built executable, replacing the paths with absolute
paths:

```json
{
  "command": "/absolute/path/to/london/examples/items-mcp/target/debug/items-mcp",
  "args": [
    "--configuration", "/absolute/path/to/london/examples/items-mcp/configuration.json"
  ],
  "env": {
    "ITEMS_ACCESS_TOKEN": "assistant",
    "ITEMS_INTROSPECTION_URL": "http://127.0.0.1:8400/introspect",
    "ITEMS_CLIENT_ID": "items-mcp",
    "ITEMS_CLIENT_SECRET": "development-only"
  }
}
```

On Windows, the executable is `target\debug\items-mcp.exe` within the example directory.
The executable's `--help` describes its launch options. The launcher chooses the token, so it
chooses the grant: use `assistant` for a connection a model drives.

## Serve over Streamable HTTP

With `--http`, one server process serves every client, and each request carries its own token.
Start the development authorisation server as above, then, from `examples/items-mcp`:

```console
ITEMS_INTROSPECTION_URL=http://127.0.0.1:8400/introspect \
ITEMS_CLIENT_ID=items-mcp ITEMS_CLIENT_SECRET=development-only \
target/debug/items-mcp --configuration configuration.json --http 127.0.0.1:8401
```

In PowerShell, set each variable first, as in `$env:ITEMS_CLIENT_ID = "items-mcp"`, and run
`target\debug\items-mcp.exe`. The server prints its MCP endpoint, `http://127.0.0.1:8401/mcp`,
and serves until interrupted.
A client sends `Authorization: Bearer assistant`, or another development token, with each
request. The clients share one list: an item one grant adds, another sees.

A request without a token the authorisation server verifies receives HTTP 401 with the
challenge `Bearer resource_metadata="https://items.example.com/.well-known/oauth-protected-resource"`.
The server answers that metadata at `/.well-known/oauth-protected-resource`, naming the
authorisation server. The server speaks plain HTTP, so it refuses an address that is not a
loopback address; a deployment serves it behind TLS.

## Operations and configuration

[`src/application.rs`](src/application.rs) defines each tool as a Clapham typed operation: Rust
input and output types from which Clapham generates the tools' schemas, the scope the tool
requires and the refusals it can return. `items.remove` also names the `items.owned` resource
policy, which compares the item's owner with the grant's subject before the handler runs.
These requirements belong to the operations, so configuration cannot remove them.

`configuration.json` selects the operations to expose and can add restrictions. For example,
change the `items.add` entry to

```json
{ "source": "items", "name": "items.add", "require": { "scopes": ["items:remove"] } }
```

and relaunch: the `assistant` grant no longer lists or calls `items.add`. Removing an entry
withdraws that tool. Each launch reads the file afresh.

## Server and client

[`src/main.rs`](src/main.rs) reads the launch configuration and serves MCP. Over stdio it
verifies the token, uses standard input and output for MCP messages and stops when the client
disconnects. Over Streamable HTTP it mounts Clapham's MCP endpoint and protected-resource
metadata on a loopback listener. Diagnostics go to standard error.
[`walkthrough.py`](walkthrough.py) shows how a client starts the server and calls its tools
over each transport.

The dependencies in [`Cargo.toml`](Cargo.toml) take both Clapham crates from the release named
in [`london.toml`](../../london.toml). For Clapham's APIs, see its
[application guide](https://github.com/thirtytwobits/clapham/blob/v0.3.2/docs/composing.md),
[MCP adapters](https://github.com/thirtytwobits/clapham/blob/v0.3.2/docs/mcp.md) and
[call contract](https://github.com/thirtytwobits/clapham/blob/v0.3.2/docs/calls.md).

## Why Clapham

Clapham separates what a grant may do from what the application does. The handlers in
`application.rs` only read and change the item map; Clapham verifies the token, filters
discovery by scope, validates inputs and results, and runs the ownership policy before
dispatch. Every refusal arrives with a stable code, category and evidence of whether the
handler ran.

The same operations also supply Clapham's generated CLI, which calls them over MCP and applies
the same checks. For a few tools exposed only through MCP and without per-grant permissions, a
direct MCP SDK is likely simpler.

## Verification

From `examples/items-mcp`, run:

```console
cargo test --locked
python3 -m unittest discover -s tests -p 'test_*.py'
```

The Rust tests run each development grant's calls over an in-memory connection and over the
executable's stdio and Streamable HTTP, with a scripted introspection endpoint. They check
discovery, scopes, ownership, invalid inputs, refused tokens and configuration, shutdown, one
HTTP server verifying each request under its own token, the protected-resource metadata and the
loopback restriction. The Python test
checks that the dependencies match `london.toml` and use Clapham as the only London Project
piece. CI also runs the walkthrough against the official Python SDK.
