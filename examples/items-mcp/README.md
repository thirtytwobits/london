# An item list served over MCP

This London Project example uses Clapham alone to expose an in-memory list over MCP stdio.
An agent can list and add items; a human can also remove them. Each process starts with two
seeded items and retains its state until the connection closes.

| Tool | Input | Result | Permitted callers |
|---|---|---|---|
| `items.list` | `{}` | `{"items":[{"id":"a","text":"Read a message"},…]}` in ID order | Human, agent |
| `items.add` | `{"id":"c","text":"Add an item"}` | The added item | Human, agent |
| `items.remove` | `{"id":"a"}` | The removed item | Human |

IDs and text must be non-empty strings. Additional input properties are refused. An existing
ID returns `item_exists` in category `conflict`; a missing removal target returns
`item_not_found` in category `not_found`.

## Build

Use Rust 1.88 or later. From the repository root:

```console
cargo build --manifest-path examples/items-mcp/Cargo.toml --locked
cargo test --manifest-path examples/items-mcp/Cargo.toml --locked
```

The binary is `examples/items-mcp/target/debug/items-mcp` (`items-mcp.exe` on Windows).
Use its `--help` for launch configuration.

## Walkthrough

The scripted walkthrough uses the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0)
as an independent host. With Python 3.11 or later, from `examples/items-mcp`:

```console
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python walkthrough.py target/debug/items-mcp
```

On Windows, use `.venv\Scripts\python.exe` and `target\debug\items-mcp.exe`.

The walkthrough starts an agent session and a separate human session, awaiting each reply:

1. Agent discovery lists `items.add` and `items.list`.
2. Adding `c` and listing again shows three items.
3. Adding an item without text returns `invalid_input` before dispatch.
4. Adding `c` again returns `item_exists`, preserving the original text.
5. Calling the hidden `items.remove` directly returns `not_admitted`. The list is unchanged.
6. Human discovery also lists `items.remove`. Removing `a` succeeds; removing an absent ID
   returns `item_not_found`.

The Python host uses MCP 2026-07-28. Compatibility is checked with SDK 2.3.0 by the walkthrough
and CI. Other hosts must support that protocol version and stdio transport.

For a host that launches stdio servers, set its command to the absolute path of the built
binary and its arguments to `["--principal", "agent"]`. The trusted launcher chooses the
principal kind. Choosing `human` grants removal rights to that whole connection, including
any model using it. The human walkthrough represents a trusted human client. Request arguments
and metadata cannot change the connection's principal.

## Assembly

[`src/application.rs`](src/application.rs) defines the three descriptors and handlers,
composes them and selects the tool surface. Clapham validates inputs and successful outputs,
filters discovery through its default gate and checks admission on each call. Denied calls
are refused even when the caller already knows the tool's name.

The application owns the map, the launch identity and process lifetime. A mutex keeps each
map operation atomic. `src/main.rs` retains the stdio service until shutdown; stdout carries
MCP and diagnostics go to stderr. Separate processes have separate lists.

Both Clapham crates come from the release named in [`london.toml`](../../london.toml), with a
committed lockfile and only the `stdio` transport enabled. See Clapham's
[assembly guide](https://github.com/thirtytwobits/clapham/blob/v0.1.0/docs/composing.md) and
[MCP server contract](https://github.com/thirtytwobits/clapham/blob/v0.1.0/crates/clapham-mcp-server/README.md)
for its APIs and guarantees.

## Why Clapham

The descriptors declare the schemas, effects, admitted principal kinds and refusal categories
once. Clapham applies that contract to discovery and dispatch and maps results and refusals to
MCP. The tests make the distinction visible: a hidden tool remains protected when called
directly. The same contribution can also supply Clapham's generated CLI.

For a few unrestricted, stateless tools, a direct MCP SDK is likely simpler. Clapham earns its
descriptors and composition when operations need consistent permissions, validation and
actionable errors across front ends.

Applications own persistence, transactional effects and durable approvals. Clapham v0.1.0
returns final MCP results; it discards streaming records. This example uses process-local state
and immediate admission decisions.

## Tests

The agent and human walkthrough contracts run over supplied async streams and the actual
subprocess stdio transport. They check schemas, annotations, ordered results, unchanged state
after refusals, domain error categories, principal spoofing and clean EOF shutdown. Both kinds
use the same principal ID to check that permissions follow the principal kind. Launch tests
also check invalid configuration and help output.

Every test awaits protocol completion without sleeps, timers, wall-clock reads, retries or a
model call. The Python walkthrough adds an independent SDK host to the Rust wire tests.
`python3 -m unittest discover -s tests -p 'test_*.py'` checks that the locked dependency graph
uses the release in `london.toml` and keeps Clapham as the only London Project piece. Run the
release check from this directory after building the example.
