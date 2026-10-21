# The London Project

The London Project is an experimental set of modular technologies for assembling agentic and
agent-enabled applications. Its three pieces are released separately. This repository
documents them as one set and holds examples that compose them.

| Piece | Owns |
|---|---|
| [Clerkenwell](https://github.com/thirtytwobits/clerkenwell) | documents people and agents both edit: schema, store, conflict policy, principals, sync |
| [Walthamstow](https://github.com/thirtytwobits/walthamstow) | the engine kernel: errors and reason codes, event bus, daemon lifecycle, journals, a file-backed workspace, explicit upgrades |
| [Battersea](https://github.com/thirtytwobits/battersea) | dataflow: flows, runtime, models and providers, tools and MCP, configuration, telemetry |

An agent works from stale reads, retries, races people, and must not decide some things. The
London Project treats it as a second writer the system can refuse safely, not as a trusted one.

## Examples

| Example | Pieces | What it is |
|---|---|---|
| [`examples/micro`](examples/micro/README.md) | Clerkenwell, Walthamstow, one local model | a to-do list a person and a model both edit |
| [`examples/chat`](examples/chat/README.md) | all three | a chatbot whose conversations are documents a person and an agent both write |

Both are designed and not yet built. Each README states its design and its open questions.

## Documentation

| Document | Covers |
|---|---|
| [`docs/overview.md`](docs/overview.md) | the layers, the dependency direction, the principles |
| [`docs/clerkenwell.md`](docs/clerkenwell.md) | what Clerkenwell owns and what it leaves to the application |
| [`docs/walthamstow.md`](docs/walthamstow.md) | what Walthamstow owns and what it leaves to the application |
| [`docs/battersea.md`](docs/battersea.md) | what Battersea owns and what it leaves to the application |
| [`docs/composing.md`](docs/composing.md) | how an application joins the pieces, seam by seam |
| [`docs/glossary.md`](docs/glossary.md) | terms the pieces share, and the ones that collide |

## Releases

[`release-set.json`](release-set.json) names one release of each piece. The examples build
against that set. A release of the London Project is a tag on `main` whose notes list the set.

## Licence

MIT. See [`LICENSE`](LICENSE).
