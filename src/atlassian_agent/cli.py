"""Small diagnostic CLI for the Atlassian MCP tools."""

from __future__ import annotations

from typing import Annotated

import typer
from dotenv import load_dotenv

from atlassian_agent.tools import (
    append_confluence_sentence,
    get_confluence_page,
    get_confluence_page_family,
)

app = typer.Typer(
    help="Diagnostic CLI for guarded Jira and Confluence tools.",
    no_args_is_help=True,
)


@app.command()
def page(page_url_or_id: str) -> None:
    """Smoke-test Confluence auth by reading a page."""
    load_dotenv()
    page_data = get_confluence_page(page_url_or_id)
    typer.echo(f"Title: {page_data['title']}")
    typer.echo(f"ID: {page_data['id']}")
    typer.echo(f"Version: {page_data['version']}")
    typer.echo(f"Body characters: {len(page_data['body'])}")


@app.command()
def family(
    page_url_or_id: Annotated[
        str, typer.Argument(help="Root Confluence page URL or ID.")
    ],
    depth: Annotated[
        int,
        typer.Option("--depth", help="How many child-page levels to inspect."),
    ] = 2,
) -> None:
    """Print a compact page/subpage tree."""
    load_dotenv()
    data = get_confluence_page_family(page_url_or_id, depth=depth)

    def print_node(node: dict, indent: int = 0) -> None:
        prefix = "  " * indent
        typer.echo(
            f"{prefix}- {node['title']} (id: {node['id']}, version: {node['version']})"
        )
        preview = node.get("body_preview", "")
        if preview:
            typer.echo(f"{prefix}  {preview[:240]}")
        for child in node["children"]:
            print_node(child, indent + 1)

    print_node(data["root"])


@app.command("append-sentence")
def append_sentence(
    page_url_or_id: Annotated[str, typer.Argument(help="Confluence page URL or ID.")],
    sentence: Annotated[str, typer.Argument(help="Sentence to append.")],
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Actually publish the page update."),
    ] = False,
) -> None:
    """Append a simple sentence to a Confluence page."""
    load_dotenv()
    result = append_confluence_sentence(page_url_or_id, sentence, apply=apply)
    typer.echo(f"Status: {result['status']}")
    typer.echo(f"Message: {result['message']}")
    typer.echo(f"Title: {result['title']}")
    typer.echo(f"Page ID: {result['page_id']}")
    typer.echo(f"Version read: {result.get('version', 'unknown')}")
    if result.get("diff"):
        typer.echo("")
        typer.echo("Diff:")
        typer.echo(result["diff"])


if __name__ == "__main__":
    app()
