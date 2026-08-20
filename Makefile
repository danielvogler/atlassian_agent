.DEFAULT_GOAL := help

# Override on the command line, e.g. `make page PAGE_URL=https://…/x/abc123`.
PAGE_URL ?= https://confluence.example.com/x/example
SENTENCE ?= This is a test sentence from the Atlassian agent.

.PHONY: help setup hooks mcp mcp-tools page family append append-apply \
        test lint format format-check typecheck check status

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv, install dependencies, install git hooks
	uv sync
	uv run pre-commit install --install-hooks
	@test -f .env || (cp .env.example .env && echo "Created .env — fill in your tokens")

hooks: ## Run every pre-commit hook over the whole repository
	uv run pre-commit run --all-files

mcp: ## Start the MCP server over stdio
	scripts/run-atlassian-agent-mcp.sh

mcp-tools: ## List the registered MCP tools without calling Atlassian
	uv run python scripts/list_mcp_tools.py

page: ## Read PAGE_URL and print its metadata
	uv run atlassian-agent page "$(PAGE_URL)"

family: ## Read PAGE_URL and print the page/subpage tree
	uv run atlassian-agent family "$(PAGE_URL)"

append: ## Dry-run appending SENTENCE to PAGE_URL
	uv run atlassian-agent append-sentence "$(PAGE_URL)" "$(SENTENCE)"

append-apply: ## Publish SENTENCE to PAGE_URL (writes to Confluence)
	uv run atlassian-agent append-sentence --apply "$(PAGE_URL)" "$(SENTENCE)"

test: ## Run the test suite
	uv run pytest

lint: ## Run Ruff lint
	uv run ruff check .

format: ## Format with Ruff
	uv run ruff format .

format-check: ## Check formatting without writing
	uv run ruff format --check .

typecheck: ## Run mypy
	uv run mypy

check: lint format-check typecheck test mcp-tools ## Everything CI runs

status: ## Show git status
	git status --short
