"""Compatibility facade for MCP and diagnostic CLI tools."""

from __future__ import annotations

from atlassian_agent.common import render_diff
from atlassian_agent.confluence import (
    add_confluence_comment,
    add_confluence_labels,
    append_confluence_sentence,
    create_confluence_page,
    get_confluence_attachments,
    get_confluence_comments,
    get_confluence_labels,
    get_confluence_page,
    get_confluence_page_family,
    get_confluence_page_history,
    search_confluence,
    update_confluence_page,
)
from atlassian_agent.jira import (
    jira_add_comment,
    jira_create_issue,
    jira_get_issue,
    jira_get_structure,
    jira_get_structure_forest,
    jira_get_structure_values,
    jira_get_transitions,
    jira_search,
    jira_transition_issue,
    jira_update_issue_fields,
)
from atlassian_agent.local_files import (
    read_local_file,
    read_source_file,
    write_local_file,
)
from atlassian_agent.runtime import set_apply_mode

__all__ = [
    "add_confluence_comment",
    "add_confluence_labels",
    "append_confluence_sentence",
    "create_confluence_page",
    "get_confluence_attachments",
    "get_confluence_comments",
    "get_confluence_labels",
    "get_confluence_page",
    "get_confluence_page_family",
    "get_confluence_page_history",
    "jira_add_comment",
    "jira_create_issue",
    "jira_get_issue",
    "jira_get_structure",
    "jira_get_structure_forest",
    "jira_get_structure_values",
    "jira_get_transitions",
    "jira_search",
    "jira_transition_issue",
    "jira_update_issue_fields",
    "read_local_file",
    "read_source_file",
    "render_diff",
    "search_confluence",
    "set_apply_mode",
    "update_confluence_page",
    "write_local_file",
]
