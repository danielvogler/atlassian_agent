# AGENTS.md

The single source of truth for anyone working in this repository, human or
agent. `CLAUDE.md` and `GEMINI.md` are pointers here and hold no content of
their own.

**There are two jobs in this file.** Using the tools against a real Jira or
Confluence is §A. Changing the code is §B. Read the one you are here for.

---

# §A — Using the tools

## A1. What you are holding

An MCP server exposing thirteen tools against a self-hosted Jira and
Confluence. Nine read. Four write, and **every one of the writes is a dry run
until somebody says otherwise**. That is the whole design, and §A3 is the part
you must not improvise around.

## A2. Setup

```bash
make setup      # venv, dependencies, git hooks, .env from the template
```

Then fill in `.env` — a Confluence base URL and personal access token, and the
same pair for Jira if the `jira_*` tools are wanted. Both are sent as
`Authorization: Bearer <token>`.

`.env` is gitignored, a pre-commit hook refuses to commit it, and the local
file tools refuse to read it. **Never print a token value, echo it into a
transcript, paste it into an issue, or send it to a model.** If one is exposed,
it is rotated in Atlassian, not deleted from the log.

Confirm the wiring without touching Atlassian:

```bash
make mcp-tools    # lists all thirteen tools; needs no credentials
```

Then confirm the credentials with one read:

```bash
make page PAGE_URL=https://confluence.example.com/x/abc123
```

## A3. The rules for writing

**A write tool called without `apply=true` returns a diff and changes
nothing.** It is not an error and it is not a failure to retry differently. It
is the answer: *this is what I would do*.

Before you pass `apply=true`:

1. **Show the operator the diff the dry run returned**, and the page or issue
   it targets by name and ID. Not a summary of it — the diff.
2. **Wait for them to approve that exact change against that exact target.**
3. Only then re-run with `apply=true`.

Approval does not carry. An operator who approved a comment on `PROJ-41` has
not approved one on `PROJ-42`, and approval given ten minutes ago for a diff
you have since regenerated is approval of a different diff. Ask again.

**Confluence page updates additionally require the version you read.**
`confluence_update_page` refuses if the page moved underneath you, because the
storage body you are editing is no longer the one on the server, and publishing
it silently reverts whatever happened in between. When it refuses: re-read,
re-apply your change to the new body, show the new diff, ask again.

A dry run against a page you cannot resolve, or an issue that does not exist,
fails at the read. That is on purpose — the guard is worthless if a typo
becomes a new page.

## A4. The tools

Read tools — safe to call freely:

| Tool | What it gives you |
|---|---|
| `confluence_get_page` | Title, ID, version, and the raw storage body |
| `confluence_get_page_family` | A page plus descendants (depth ≤ 4) with text previews, for choosing where to edit |
| `jira_search` | JQL search |
| `jira_get_issue` | One issue by key |
| `jira_get_structure` | Jira Structure metadata |
| `jira_get_structure_forest` | Structure rows: row ID, depth, item identity |
| `jira_get_structure_values` | Text-formatted values for selected Structure rows |

Write tools — dry run unless `apply=true`:

| Tool | Note |
|---|---|
| `confluence_update_page` | Also requires `expected_version` from the read |
| `confluence_append_sentence` | Appends one paragraph; returns `unchanged` if the sentence is already on the page |
| `jira_create_issue` | |
| `jira_update_issue_fields` | |
| `jira_add_comment` | |
| `jira_transition_issue` | |

`confluence_get_page_family` before `confluence_update_page` is the usual
sequence: it is how you find the right page rather than the one whose URL you
happened to be given.

Reads take a Confluence page URL, a `/x/` tiny link, or a numeric ID. Tiny
links are resolved by following them, and the URL host must match
`CONFLUENCE_URL` — an agent handed a link to somewhere else does not send the
token there.

## A5. Reading the results

Every tool returns a dict with a `status`:

| `status` | Meaning |
|---|---|
| `success` | It happened |
| `dry_run` | Nothing happened; `diff` shows what would |
| `unchanged` | Already in the desired state; nothing to do |
| `error` | `message` says why — a missing variable, a version conflict, an HTTP failure |

`error` is a returned value, not an exception: the MCP layer catches
everything so a failed call does not kill the session. Read the `message`
rather than retrying blind.

---

# §B — Changing the code

## B1. Layout

```
src/atlassian_agent/
  mcp_server.py   Registers the tools, names them, tags them, catches everything
  confluence.py   Confluence reads and guarded writes  → CONFLUENCE_MCP_TOOLS
  jira.py         Jira and Jira Structure              → JIRA_MCP_TOOLS
  local_files.py  Workspace-scoped file reads/writes
  runtime.py      The process-wide apply flag
  common.py       Diff rendering, error shaping
  cli.py          Diagnostic CLI (Typer) — smoke tests, not the main interface
scripts/          MCP launcher, tool lister
tests/            Faked HTTP; no test may touch a network
```

The MCP tool list is the two `*_MCP_TOOLS` tuples and nothing else. A function
that is exported from `tools.py` but absent from a tuple is not reachable from
an agent session — that is how `local_files` and `get_confluence_child_pages`
currently sit, deliberately.

`mcp_server.py` derives each tool's public name, its tags, and its
`readOnlyHint` / `destructiveHint` annotations from the function name. Adding a
read tool called `get_confluence_labels` gets you `confluence_get_labels`,
tagged `confluence`+`read`, marked read-only, for free. Adding one called
`fetch_labels` gets you none of that. **Follow the naming or set the
annotations by hand** — a write tool that an agent's client believes is
read-only is the single worst bug this repository can ship.

## B2. Adding a write tool

Take an existing one as the template, and keep all four properties:

- `apply: bool = False` as a **keyword-only** argument.
- Return `status: "dry_run"` with a rendered `diff` when
  `not (apply or apply_enabled())`, before any mutating call is made.
- Do the reads needed to build that diff — a dry run that cannot show what
  would change is not a dry run.
- For anything with a version or revision, take the expected one and refuse on
  mismatch.

Then add it to the module's `*_MCP_TOOLS` tuple and to `tools.py`'s `__all__`.

## B3. Checks

```bash
make check      # lint, format, mypy, tests, and the MCP tools list — what CI runs
```

Everything must pass before a change is handed over. `make check` includes
`mcp-tools` because a tool that fails to register still lints and still tests
green; the failure only shows up in someone's agent session.

Tests fake the HTTP layer. **No test may make a network call or need
credentials** — CI has none, and a suite that depends on a live Jira has
stopped testing this repository. Cover the guard, not just the happy path: the
dry-run branch, the version-mismatch refusal, and the missing-variable error
are the behaviour worth protecting.

**No hostname is hardcoded in a test.** Base URLs come from `JIRA_BASE_URL` and
`CONFLUENCE_BASE_URL` in `tests/test_tools.py`, which read the environment or
`.env` and fall back to example hosts — so the suite runs against the real
configured host locally and against the fallbacks in CI, and an internal
Atlassian URL never reaches git. Only the `*_URL` keys are read; tokens are
never loaded into the test process. A host written into a fixture is a leak
that outlives every later cleanup, and it is never what the assertion is
about.

## B4. Conventions

- `uv` for everything. `ruff` formats and lints, `mypy` type-checks, `pytest`
  tests. Type hints on every public signature.
- Commits are `<type>: <description>` — `feat`, `fix`, `refactor`, `docs`,
  `test`, `chore`, `ci`. A commit-msg hook rejects AI `Co-Authored-By`
  trailers.
- Credentials come from the environment. Never a default, never a fallback,
  never a literal — `_required_env` raises rather than guessing.
- Anything that resolves a user-supplied URL validates the host against the
  configured base URL before sending a token to it.
- Keep the README's tool tables and §A4 in step with the `*_MCP_TOOLS` tuples.
  A documented tool that does not exist wastes an agent's session; an
  undocumented one never gets called.
