# Battersea

[Battersea](https://github.com/thirtytwobits/battersea) is an embeddable dataflow framework.
`battersea-tools` and `battersea-ops` build on Clapham; the rest of Battersea depends on no
other London piece.

## What it owns

- Flows: flow documents, ports, templates, catalogues and graph validation (`battersea-flow`).
- The runtime: node handlers, token and signal scheduling, the activation lifecycle and
  optional durable execution (`battersea-runtime`).
- Models: content-block messages, provider registration, request admission and the shared tool
  loop (`battersea-model`), with OpenAI, Anthropic, Google, Runway, Replicate and mock
  transports (`battersea-providers`).
- Tools: a selection of Clapham operations offered to providers as Model Context Protocol tool
  records and run under the run's verified grant (`battersea-tools`). Remote MCP tools arrive as
  operations through Clapham's MCP client.
- Its flow, run, approval and MCP-connection operations, contributed to Clapham
  (`battersea-ops`).
- Backend configuration, telemetry with usage budgets, generic nodes, a prompt-root scanner
  (`battersea-guard`) and Pianola recorded test rolls.
- The TypeScript packages `@battersea/flow` and `@battersea/editor`.

## What the application supplies

- Node handlers, token schemas, parameter schemas and product validators.
- Its prompts, storage and assets, and the Clapham operations its agents may call.
- Its state, effects and durable acceptance, through the runtime's host interfaces.

## Depending on it

Crates by tag and npm packages from a release. An application that takes `battersea-tools` or
`battersea-ops` takes the Clapham release Battersea's release names.

## Its own documentation

- [Handbook and API references](https://thirtytwobits.github.io/battersea/)
- [README](https://github.com/thirtytwobits/battersea#readme)
- [Contracts](https://github.com/thirtytwobits/battersea/tree/main/contracts)
- [Changelog](https://github.com/thirtytwobits/battersea/blob/main/CHANGELOG.md)
