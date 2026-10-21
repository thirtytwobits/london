# Clerkenwell

[Clerkenwell](https://github.com/thirtytwobits/clerkenwell) is a Rust and TypeScript library
for applications in which people and agents edit the same documents. A server holds the
authoritative copy of each document; clients edit replicas and send their edits to it.

## What it owns

- The definition: a JSON file declaring an application's entities, projections, mutations,
  commands and, per collaborative field, its storage and conflict policy.
- The generator that turns a definition into Rust and TypeScript bindings.
- The store, which fences every write on the version it was read at, judges it by each field's
  conflict policy, and records the principal that made it.
- The session protocol clients sync over, with a reference WebSocket server
  (`clerkenwell-axum`).
- The TypeScript clients `@clerkenwell/client` and `@clerkenwell/react`.

## What the application supplies

- Its definition and the validation the store runs on every document it accepts.
- Authentication: each connection becomes a principal of kind `human`, `agent`, `service` or
  `system`.

## Depending on it

Crates by tag, npm packages from the tarballs attached to a release, and the generator installed
at the tag. Clerkenwell builds on a Loro fork, so every Cargo workspace that builds it carries
its `generic-btree` override.

## Its own documentation

- [README](https://github.com/thirtytwobits/clerkenwell#readme), including its terms
- [Architecture](https://github.com/thirtytwobits/clerkenwell/blob/main/docs/architecture.md)
- [Using a release](https://github.com/thirtytwobits/clerkenwell/blob/main/docs/using-a-release.md)
- [Changelog](https://github.com/thirtytwobits/clerkenwell/blob/main/CHANGELOG.md)
