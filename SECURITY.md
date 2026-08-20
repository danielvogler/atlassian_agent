# Security

## Reporting

Report a vulnerability privately through
[GitHub security advisories](https://github.com/danielvogler/atlassian_agent/security/advisories/new).
Please do not open a public issue for anything exploitable.

## What this repository is careful about

It holds Atlassian personal access tokens in an agent's reach, so the guards
are the product rather than a nicety:

- **Credentials come from the environment only.** No defaults, no fallbacks, no
  literals. `.env` is gitignored, a pre-commit hook refuses to commit it, and
  the local file tools refuse to read it.
- **Tokens are never printed or logged.** Test fakes redact the
  `Authorization` header so a failing assertion cannot leak one.
- **Every write is a dry run** until `apply=true` is passed, and Confluence
  page updates additionally require the version that was read, so a concurrent
  edit cannot be silently reverted.
- **User-supplied URLs are host-checked** against the configured base URL before
  a token is sent to them.
- **Local file tools are workspace-scoped**: relative paths only, no escaping
  the repository root, and no reading `.env`, `.env.local` or `.envrc`.
- **No hostname is hardcoded anywhere in the repository.** Tests resolve base
  URLs from the environment or `.env` and fall back to example hosts, so an
  internal Atlassian URL never reaches git. A leaked base URL is not a
  credential, but it is reconnaissance, and it outlives every later cleanup.
- **gitleaks scans the full history** on every push, because a token committed
  once and reverted is still leaked.

If a token is exposed, rotate it in Atlassian. Removing it from a log is not a
remediation.
