# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-09-12

### Added

- Automated PyPI publishing. Pushing a `v*` tag runs the same CI gate main
  gets, builds, verifies, and uploads via PyPI trusted publishing — no token
  exists in the repository, in a GitHub secret, or on a laptop. The workflow
  refuses a tag whose version disagrees with `pyproject.toml` or that has no
  changelog heading, because a version on PyPI can be yanked but never
  replaced. See AGENTS.md §B5.
- `scripts/verify-wheel.sh`, which installs a built wheel into a clean
  virtualenv, resolves both console scripts and registers every MCP tool there.
  Every other test imports from the working tree, where a module missing from
  the wheel is invisible. Run by `make dist-check` and by the release workflow,
  so the local rehearsal and the release cannot drift apart.
- `make build`, `make dist-check` and `make release-check`, the last of which
  rehearses a release end to end and prints the commands that publish it.
- `.github/dependabot.yml`, weekly for GitHub Actions and for the lockfile. The
  actions are pinned to commit SHAs, which is safe from a moved tag and blind
  to a patched vulnerability; something has to bring the new SHA to a PR.
- Packaging contract tests: the declared version is the one the package
  reports, the distribution name is the one PyPI is configured for, and both
  console scripts resolve.

### Changed

- **The distribution is now `atlassian-agent-mcp`.** `atlassian-agent` on PyPI
  belongs to an unrelated project and was never available. The import package,
  the module layout and both console-script names are unchanged.
- The version is declared in `pyproject.toml` alone. `atlassian_agent.__version__`
  reads it back from the installed metadata rather than repeating the literal,
  which is a pair that drifts silently in exactly the release that matters.
- `fastmcp` is bounded `>=4.0,<5`. It was open-ended above 3.4.0, so a fresh
  resolve already picked up 4.0.3 while this repository's lockfile said 3.4.4 —
  the failure mode the `atlassian-python-api` cap exists to prevent. The floor
  is 4 because MCP SDK v2 renamed the tool annotations this server sets.
- Tool annotations are set as `read_only_hint` / `destructive_hint`, the names
  MCP SDK v2 uses. The camelCase spellings still worked but were deprecated,
  and this is the annotation that tells a client which tools are safe.
- `requires-python` no longer caps the interpreter at `<3.14`. A cap does not
  protect this package from a future Python; it stops anyone on that Python
  from installing it, and only a new release can lift it. 3.14 is in the CI
  matrix.
- CI pins every action to a commit rather than a tag, and is callable as a
  reusable workflow so the release runs the identical gate instead of a copy.
- README and AGENTS.md lead with the installed route, and say where credentials
  live in each: `.env` from a clone, the client's `env` block when installed.

### Fixed

- The architecture diagram in the README claimed 9 reads and 4 writes. There
  are 13 and 9.

## [0.2.0] - 2026-08-29

### Added

- `confluence_create_page`, a dry-run-by-default write that creates a page in a
  space or under a parent page. The space is derived from the parent when it is
  omitted, and a title already taken in that space is refused with the existing
  page ID rather than sent to Confluence.
- `confluence_get_page_history`, `confluence_get_comments`,
  `confluence_get_labels` and `confluence_get_attachments` reads.
- `confluence_add_comment` and `confluence_add_labels`, both additive: labels
  are only ever added, and both are dry run by default like every other write.
- `body_format="text"` on `confluence_get_page`, returning the page with markup
  and macros stripped. Read a page as text to understand it and as storage only
  when about to write it back.
- `confluence_search`, a CQL read, so a page can be found by title, text or
  label instead of only by a URL somebody already had.
- `jira_get_transitions`, listing the transitions an issue currently offers and
  the status each one leads to.
- `search` and `create-page` commands on the diagnostic CLI.
- Contract tests that drive the real `atlassian-python-api` with a stubbed
  transport, so a renamed parameter or a moved method fails in CI against the
  version actually installed. Every other test talks to a hand-written fake and
  therefore cannot see a library change at all.
- Tests that fail the build when `README.md` or `AGENTS.md` list a tool that is
  not registered, omit one that is, or state a tool count the registry
  contradicts. AGENTS.md §B4 asked for this; nothing enforced it.

### Changed

- `jira_create_issue` resolves the project and issue type against Jira's create
  metadata before its dry run, and refuses an unknown issue type or a missing
  required field instead of failing at publish time. The dry run now needs
  credentials.
- `jira_transition_issue` reads the issue's available transitions and refuses a
  target status the workflow does not offer. `status` is the status the issue
  ends up in, not the name of the transition. The dry run now needs credentials.

### Fixed

- `confluence_create_page` failed against `atlassian-python-api` v5. That
  release renamed `get_page_by_title`'s first parameter `space` -> `space_key`
  *and* changed its return from the first result (or `None`) to the raw
  `{"results": [...]}` envelope, which is truthy when empty and so reports a
  duplicate that does not exist. The call is now positional and the response is
  normalised, and both are covered on both majors. Identical code behaved
  differently in two environments purely because the dependency was unpinned.
- The duplicate-title probe in `confluence_create_page` no longer lets the
  library log `Can't find '<title>' page` at ERROR. A free title is the wanted
  outcome, and that line landed in transcripts immediately before a successful
  dry run, reading like a failure.
- Plain-text extraction dropped the contents of code macros: `<![CDATA[...]]>`
  has no `>` until its end, so a tag strip ate the code along with the markup.
  This also affected `confluence_get_page_family` previews.
- The two guarded Jira writes that now read first say so when that read fails,
  instead of surfacing a bare missing-variable error that reads like a broken
  tool rather than the guard working.
- `confluence_append_sentence` honours the CLI-wide apply flag like every other
  write; it previously checked only its own `apply` argument, so `--apply`
  silently did nothing.
- Tool counts in `README.md` and `AGENTS.md`, which did not match the registry.

### Documentation

- AGENTS.md §A6 records what is deliberately absent and why: page and issue
  deletion, moving a page, downloading attachment bytes, and the subtractive
  halves of the comment and label tools.

### Changed

- `atlassian-python-api` is capped at `<5`. v5 moves the Confluence methods off
  the class onto the instance and changes signatures and return shapes; the
  open range let two environments resolve different majors.

### Removed

- `get_confluence_child_pages`, dead code that was never registered as a tool
  and is superseded by `confluence_get_page_family`.

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

[Unreleased]: https://github.com/danielvogler/atlassian_agent/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/danielvogler/atlassian_agent/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/danielvogler/atlassian_agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/danielvogler/atlassian_agent/releases/tag/v0.1.0
