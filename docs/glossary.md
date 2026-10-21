# Glossary

Each piece defines its own terms; Clerkenwell's are in its
[README](https://github.com/thirtytwobits/clerkenwell#terms). This page lists the terms the
pieces share, and the ones that mean different things in different pieces.

## Shared

| Term | Meaning |
|---|---|
| principal | who makes a change: an identity the application authenticated, and its kind (`human`, `agent`, `service` or `system`) |
| release | a tag on a piece's repository, with its crates at the tag and its npm packages attached |
| release set | one release of each piece, named in [`release-set.json`](../release-set.json) |

## Collisions

| Term | Clerkenwell | Walthamstow | Battersea |
|---|---|---|---|
| document | a collaborative document the store holds | a typed file in the workspace | a flow document |
| event | the change-event envelope a document announces its commits in (`clerkenwell-events`) | a message on the kernel's event bus | a typed execution event from the runtime |
| code | a refusal code a client acts on | a reason code on a `KernelError` | an issue, error or tool code from validation, the runtime or the tool runner |

Say which piece a term belongs to wherever the context leaves it open.
