<h1><img src="assets/logo.png" alt="" height="64" align="center"> The London Project</h1>

The London Project is an experimental set of modular technologies for assembling agentic and
agent-enabled applications. Its four pieces are released separately. This repository
documents them as one set and holds examples that use them.

| Piece | Owns |
|---|---|
| [Clerkenwell](https://github.com/thirtytwobits/clerkenwell) | documents people and agents both edit: schema, store, conflict policy, principals, sync |
| [Walthamstow](https://github.com/thirtytwobits/walthamstow) | the engine kernel: errors and reason codes, event bus, daemon lifecycle, journals, a file-backed workspace, explicit upgrades |
| [Battersea](https://github.com/thirtytwobits/battersea) | dataflow: flows, runtime, models and providers, tools and MCP, configuration, telemetry |
| [Clapham](https://github.com/thirtytwobits/clapham) | the operations contract every piece contributes to, and the CLI, MCP server and MCP client an application assembles from it |

An agent works from stale reads, retries, races people, and must not decide some things. The
London Project treats it as a second writer the system can refuse safely, not as a trusted one.

## Examples

| Example | Pieces | What it is |
|---|---|---|
| [`examples/items-mcp`](examples/items-mcp/README.md) | Clapham | an in-memory list served over MCP, with OAuth scopes, an ownership policy and named refusals |
| [`examples/micro`](examples/micro/README.md) | Clerkenwell, Walthamstow, one local model | a to-do list a person and a model both edit |
| [`examples/chat`](examples/chat/README.md) | Clerkenwell, Walthamstow, Battersea | a chatbot whose conversations are documents a person and an agent both write |

The MCP example is runnable. The micro and chat examples are designs; each README states its
open questions.

## Documentation

| Document | Covers |
|---|---|
| [`docs/overview.md`](docs/overview.md) | the layers, the dependency direction, the principles |
| [`docs/clerkenwell.md`](docs/clerkenwell.md) | what Clerkenwell owns and what it leaves to the application |
| [`docs/walthamstow.md`](docs/walthamstow.md) | what Walthamstow owns and what it leaves to the application |
| [`docs/battersea.md`](docs/battersea.md) | what Battersea owns and what it leaves to the application |
| [`docs/clapham.md`](docs/clapham.md) | what Clapham owns and what it leaves to the application |
| [`docs/composing.md`](docs/composing.md) | how an application joins the pieces, seam by seam |
| [`docs/glossary.md`](docs/glossary.md) | terms the pieces share, and the ones that collide |

## Releases

[`london.toml`](london.toml) names each piece and, once the piece is released, the release the
examples build against. A release of the London Project is a tag on `main` whose notes list that
set.

The pieces share one release procedure and tool, kept in [`pieces/`](pieces/README.md) and sent
to each piece that has adopted them.

## Licence

MIT. See [`LICENSE`](LICENSE).

## Why London?

No particular reason. I was born in London...Ontario. I also just needed something to help generate names and I decided London neighbourhoods was as good as anything. No need to read anything else into it.
