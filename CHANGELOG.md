# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0]

Initial release.

### Added

- MCP server exposing thirteen guarded Atlassian tools: seven Confluence and
  Jira reads, three Jira Structure reads, and four writes.
- Dry-run-by-default writes: every write tool returns a rendered diff and
  changes nothing unless `apply=true` is passed.
- Optimistic-concurrency guard on Confluence page updates, which refuse to
  publish when the page moved since it was read.
- Host validation on user-supplied Confluence and Jira Structure URLs, so a
  token is never sent to an unconfigured host.
- Workspace-scoped local file tools that refuse absolute paths, refuse to
  escape the repository root, and refuse to touch `.env`.
- Diagnostic Typer CLI (`page`, `family`, `append-sentence`) for smoke tests.

[Unreleased]: https://github.com/danielvogler/atlassian_agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/danielvogler/atlassian_agent/releases/tag/v0.1.0
