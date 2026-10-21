# London Project Agent Instructions

Every section is a hard constraint.

## Names

The repository is `thirtytwobits/london`; all text calls the work the London Project. There is
no `london` library, crate or package.

## This repository owns no library

It documents Clerkenwell, Walthamstow and Battersea and holds examples that compose them. Each
piece's internals are documented in its own repository; link to them rather than copying them.
A defect in a piece is fixed in that piece's repository and reaches this one as a release.

## One release of each piece

`release-set.json` names the release of each piece the examples build against. Examples take
crates by tag (`{ git = "https://github.com/thirtytwobits/<piece>", tag = "vX.Y.Z" }`) and npm
packages from the tarballs attached to a release. `main` carries no path dependency, copied
source or unreleased revision of a piece.

Every Cargo workspace that builds Clerkenwell carries its `generic-btree` override at the Loro
revision its release names, because workspace patches do not propagate.

## Generic terms only

No downstream product is named in code, docs, issues, commits, fixtures or test data. The
examples speak of lists, items, conversations, messages, workspaces and flows.

## Examples

- An example uses each piece for what that piece owns, through its released surface. A
  workaround in an example is a defect in a piece.
- New behaviour adds or extends a test.
- Tests encode intended behaviour. Never read the implementation to decide what a test expects.
- Tests never sleep, set a timer, read the wall clock or call a real model. A model sits behind
  an interface with a scripted implementation; a test that needs a reply in flight holds it and
  releases it when ready.
- A test passes on its first run or it is broken. Nothing retries a failed test.

## Writing

- Text uses en-GB.
- State what is, precisely and concisely. When something is removed, remove it cleanly; do not
  record its absence.

## GitHub Actions

Before writing or editing a workflow, look up each action's latest release and the runtime its
`action.yml` declares; never pin a version from memory. Dependabot covers `github-actions`.
