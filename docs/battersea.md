# Battersea

[Battersea](https://github.com/thirtytwobits/battersea) is an embeddable dataflow framework.
It depends on nothing else in the London Project.

## What it owns

- Flows: flow documents, ports, templates, catalogues and graph validation (`battersea-flow`).
- The runtime: node handlers, token and signal scheduling, the activation lifecycle and
  optional durable execution (`battersea-runtime`).
- Models: content-block messages, provider registration, request admission and the shared tool
  loop (`battersea-model`), with OpenAI, Anthropic, Google, Runway, Replicate and mock
  transports (`battersea-providers`).
- Tools as Model Context Protocol records (`battersea-tools`) and tools bound from MCP
  connections (`battersea-mcp`).
- Backend configuration, telemetry with usage budgets, generic nodes, a prompt-root scanner
  and Pianola recorded test rolls.
- The TypeScript packages `@battersea/flow` and `@battersea/editor`.

## What the application supplies

- Node handlers, token schemas, parameter schemas and product validators.
- Its prompts, product tools, storage and assets.
- Its state, effects and durable acceptance, through the runtime's host interfaces.

## Depending on it

Crates by tag and npm packages from a release.

## Its own documentation

- [Handbook and API references](https://thirtytwobits.github.io/battersea/)
- [README](https://github.com/thirtytwobits/battersea#readme)
- [Contracts](https://github.com/thirtytwobits/battersea/tree/main/contracts)
- [Changelog](https://github.com/thirtytwobits/battersea/blob/main/CHANGELOG.md)
