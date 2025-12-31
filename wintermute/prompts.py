"""Prompt template helpers."""

from __future__ import annotations


DEFAULT_PROJECT_PROMPT_TEMPLATE = (
    "You are working on a GitHub issue.\n"
    "Project: {project_name} ({project_slug})\n"
    "Repo: {owner}/{repo}\n"
    "Issue #{issue_number}: {title}\n"
    "URL: {url}\n\n"
    "Instructions:\n"
    "- A working branch has been created for you.\n"
    "- Branch name: {branch_name}.\n"
    "- Implement the fix, commit changes with a clear message, and push the branch.\n"
    "- Post status updates and questions in the Slack thread for this issue.\n"
    "- If more details are needed, ask in Slack and then exit.\n"
    "- For GitHub comments, prefix lines with 'PUBLIC:'; they will be stored for approval.\n"
    "- For internal notes, prefix lines with 'NOTE:' so they stay private.\n\n"
    "Issue description:\n"
    "{description}\n\n"
    "Issue comments:\n"
    "{comments}\n\n"
    "Internal notes:\n"
    "{internal_notes}\n"
)


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_prompt_template(
    template: str | None, default: str, context: dict[str, str]
) -> str:
    if template and template.strip():
        try:
            return template.format_map(_SafeDict(context))
        except Exception:
            return default
    return default
