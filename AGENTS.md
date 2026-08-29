# AGENTS.md

The single source of truth for anyone working in this repository, human or
agent. `CLAUDE.md` and `GEMINI.md` are pointers here and hold no content of
their own.

**There are two jobs in this file.** Using the tools against a real Jira or
Confluence is §A. Changing the code is §B. Read the one you are here for.

---

# §A — Using the tools

## A1. What you are holding

An MCP server exposing twenty-two tools against a self-hosted Jira and
Confluence. Thirteen read. Nine write, and **every one of the writes is a dry run
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
make mcp-tools    # lists all twenty-two tools; needs no credentials
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

**Two dry runs read before they answer.** `jira_create_issue` resolves the
project and issue type against Jira's create metadata; `jira_transition_issue`
reads the transitions the issue actually offers. Both therefore need
credentials and a target that exists, and both return `error` rather than
`dry_run` when they cannot see one. That is the guard working, not a fault: a
dry run that cannot see the target cannot tell you what would happen.

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
| `confluence_search` | CQL search — how you find a page you were not handed the URL for |
| `confluence_get_page` | Title, ID, version, and the body — raw storage, or `body_format="text"` with markup stripped |
| `confluence_get_page_family` | A page plus descendants (depth ≤ 4) with text previews, for choosing where to edit |
| `confluence_get_page_history` | Who created the page and who last changed it — the question a refused update raises |
| `confluence_get_comments` | Page comments, where review feedback usually lives |
| `confluence_get_labels` | Labels on a page, which `label = ...` searches depend on |
| `confluence_get_attachments` | Attached files: name, media type, size. Metadata only |
| `jira_search` | JQL search |
| `jira_get_issue` | One issue by key |
| `jira_get_transitions` | The transitions an issue currently offers, and the status each leads to |
| `jira_get_structure` | Jira Structure metadata |
| `jira_get_structure_forest` | Structure rows: row ID, depth, item identity |
| `jira_get_structure_values` | Text-formatted values for selected Structure rows |

Write tools — dry run unless `apply=true`:

| Tool | Note |
|---|---|
| `confluence_add_comment` | Additive and reversible; often the right tool where an edit is reached for |
| `confluence_add_labels` | Adds only; never removes. `unchanged` when every label is already there |
| `confluence_create_page` | Creates a new page in a space, optionally under a parent; refuses a duplicate title |
| `confluence_update_page` | Also requires `expected_version` from the read |
| `confluence_append_sentence` | Appends one paragraph; returns `unchanged` if the sentence is already on the page |
| `jira_create_issue` | Resolves project and issue type against create metadata first |
| `jira_update_issue_fields` | |
| `jira_add_comment` | |
| `jira_transition_issue` | Takes the target *status*, not the transition name; refuses one the workflow lacks |

`confluence_search` then `confluence_get_page_family` before
`confluence_update_page` is the usual sequence: search finds candidate pages by
name or text, the family read shows where each one sits, and only then do you
edit — rather than editing the one whose URL you happened to be given.

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

## A6. What is deliberately absent

Some obvious tools are missing on purpose. If you find yourself wanting one,
this is the reasoning you are arguing against.

**Deleting a page or an issue, and moving a page to a new parent or space.**
Every guard in §A3 rests on the same mechanic: the dry run shows a diff, and
the operator approves *that* diff. Neither of these has one. A delete's diff is
the whole page, which tells the operator nothing they can meaningfully check,
and a move changes no content at all — the diff is empty while the page's URL,
its breadcrumbs, its inherited permissions and every link into it all change.
So the one control this repository has would be showing an operator nothing at
the exact moment it matters most. Both are also the two operations a reader
cannot undo from the page itself: an edit is one click to revert in the version
history, a delete needs an administrator to reach into the trash, and a move
needs someone to know where the page went. Do them in the browser, where
Confluence shows the consequences and your own account owns the decision.

**Downloading attachment bytes.** `confluence_get_attachments` lists what is
attached and stops there. Pulling the bytes means writing a file from a remote
service into the workspace, which is a different class of tool from anything
else here — it is the one operation that would let a page's content become code
on somebody's disk. It needs its own path guard and its own size limit, and
neither is worth inventing before there is a real use for it.

**Removing a label, and editing or deleting a comment.** Additive writes are
here because they are recoverable; the subtractive halves are not, for the same
reason as delete.

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
an agent session — that is how `local_files` currently sits,
deliberately.

`mcp_server.py` derives each tool's public name, its tags, and its
`readOnlyHint` / `destructiveHint` annotations from the function name. Adding a
read tool called `get_confluence_watchers` gets you `confluence_get_watchers`,
tagged `confluence`+`read`, marked read-only, for free. Adding one called
`fetch_watchers` gets you none of that. **Follow the naming or set the
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
