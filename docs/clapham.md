# Clapham

[Clapham](https://github.com/thirtytwobits/clapham) is the operations contract every London
piece contributes to, and the CLI, MCP server and MCP client an application assembles from it.
It depends on nothing else in the London Project.

## What it owns

- `clapham`: operation descriptors, typed operations, effects, refusals and their categories,
  OAuth authority, contributions, configuration, composition, selections, the gate, resource
  policies and calls; the published operation, refusal, catalogue and configuration schemas.
- `clapham-cli`: a command line built at run time from the composed set's descriptors, run
  in-process or against a daemon through the MCP client.
- `clapham-mcp-server`: an MCP server over stdio and Streamable HTTP, with OAuth introspection
  and the records and catalogue extensions.
- `clapham-mcp-client`: an MCP client that binds remote tools as operations.
- `clapham-conformance`: a lists-and-items contribution and the conformance harness every front
  end passes.

## What the application supplies

- Its own operations, as a contribution under its own namespace, and the composition of every
  piece's contribution.
- Its gate and the resource policies its operations name.
- The issuer and audience its grants are bound to, and the verification of each caller's grant.
- Its listener, TLS, credentials, persistence, workspace ownership and shutdown.

## Depending on it

Crates by tag. Every Clapham library requires Rust 1.88. Battersea and `walthamstow-ops` each
name the Clapham release they build on, so an application takes that release.

## Its own documentation

- [README](https://github.com/thirtytwobits/clapham/blob/v0.3.0/README.md)
- [Operation contract](https://github.com/thirtytwobits/clapham/blob/v0.3.0/docs/contract.md)
- [Composing an application](https://github.com/thirtytwobits/clapham/blob/v0.3.0/docs/composing.md)
- [MCP](https://github.com/thirtytwobits/clapham/blob/v0.3.0/docs/mcp.md)
- [Conformance](https://github.com/thirtytwobits/clapham/blob/v0.3.0/docs/conformance.md)
- [API reference](https://thirtytwobits.github.io/clapham/v0.3.0/)
- [Changelog](https://github.com/thirtytwobits/clapham/blob/v0.3.0/CHANGELOG.md)
