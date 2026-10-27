# Glossary

Each piece defines its own terms; Clerkenwell's are in its
[README](https://github.com/thirtytwobits/clerkenwell#terms). This page lists the terms the
pieces share, and the ones that mean different things in different pieces.

## Shared

| Term | Meaning |
|---|---|
| principal | who makes a change: an identity the application authenticated, and its kind (`human`, `agent`, `service` or `system`) |
| release | a tag on a piece's repository, with its crates at the tag and its npm packages attached |
| release set | one release of each released piece: the `tag` of each piece in [`london.toml`](../london.toml) |
| operation | what a piece or the application offers to be run, described once by its descriptor and run by its handler; an operator runs it from the CLI, an agent calls it over MCP and a flow calls it as a tool |
| effect | an operation's declared class, `query`, `mutation`, `administrative` or `destructive`, which sets its MCP hints and whether the CLI requires `--yes` |
| namespace | the leading segment of an operation's name, claimed by one contribution |
| contribution | a namespace and the operations under it, each with its handler |
| composed set | the contributions an application composes, in order, into one immutable set of operations |
| selection | a subset of the composed set, by name: what a front end presents to one caller and what a flow offers a model |

## Collisions

| Term | Clerkenwell | Walthamstow | Battersea | Clapham |
|---|---|---|---|---|
| document | a collaborative document the store holds | a typed file in the workspace | a flow document | |
| event | the change-event envelope a document announces its commits in (`clerkenwell-events`) | a message on the kernel's event bus | a typed execution event from the runtime | |
| code | a refusal code a client acts on | a reason code on a `KernelError` | an issue, error or tool code from validation, the runtime or the tool runner | |
| tool | | | a record the provider is offered | an operation's MCP form |
| command | a definition entry | | | a CLI subcommand |
| resource | | a file in the workspace | | an MCP resource |

Say which piece a term belongs to wherever the context leaves it open.
