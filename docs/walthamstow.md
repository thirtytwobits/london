# Walthamstow

[Walthamstow](https://github.com/thirtytwobits/walthamstow) is an engine kernel for products
built on Clerkenwell: the parts of an engine daemon that do not depend on what the product is.

## What it owns

- `walthamstow-kernel`: errors and reason codes, the event bus and daemon lifecycle, clocks,
  durable writes, journals, collaboration storage and the workspace lock.
- `walthamstow-workspace`: a file-backed workspace of typed collaborative documents:
  directories, document loading, schemas, mounts and publications.
- `walthamstow-upgrades`: explicit, journaled and recoverable upgrades of a workspace's
  documents.
- `walthamstow-ops`: the workspace's status, documents, upgrades and recovery, and the engine's
  journals, as operations contributed to Clapham, and the daemon's endpoint record.

## What the application supplies

- Its entities, validators and events, registered into the kernel.
- Its own error type, which converts from `KernelError`.
- Each entity's document format and the upgrades it registers, which `walthamstow-ops` runs.

## Depending on it

Crates by tag. Walthamstow builds on Clerkenwell, so an application takes the Clerkenwell
release that Walthamstow's release names, and carries Clerkenwell's `generic-btree` override.
`walthamstow-ops` builds on Clapham, and an application that takes it takes the Clapham release
Walthamstow's release names; the other crates take no Clapham.

## Its own documentation

- [README](https://github.com/thirtytwobits/walthamstow#readme)
- [Contributing](https://github.com/thirtytwobits/walthamstow/blob/main/CONTRIBUTING.md)
- [Changelog](https://github.com/thirtytwobits/walthamstow/blob/main/CHANGELOG.md)
