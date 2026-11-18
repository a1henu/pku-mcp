import json
import os
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pku-mcp")
TASK_FIELDS = (
    "course", "course_id", "content_id", "title", "due_at", "due_date",
    "deadline_raw", "requirements", "local_progress", "submission_record_status",
    "last_submission_at", "source_url", "verified_at",
)


def read(relative: str) -> dict:
    root = Path(os.environ["PKU_WORKSPACE"]).expanduser().resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Snapshot path is outside the workspace")
    return json.loads(path.read_text(encoding="utf-8"))


def state() -> dict:
    return read(".workspace/tasks.json")


@mcp.tool(structured_output=True)
def auth_status() -> dict[str, Any]:
    """Show recorded sync and authentication status; never test or expose credentials."""
    current = state()
    source = read(read(".workspace/latest.json")["snapshot"])
    attempt = current.get("last_refresh_attempt") or {}
    return {
        "mode": "snapshot", "live_auth_verified": False,
        "last_recorded_attempt": {key: attempt.get(key) for key in ("status", "attempted_at")},
        "source_snapshot_at": source.get("fetched_at"),
        "task_review_source_at": current.get("last_full_teaching_web_verified_at"),
    }


@mcp.tool(structured_output=True)
def due_list(include_submitted: bool = False) -> dict[str, Any]:
    """List reviewed personal tasks, preserving unknown deadlines and submission states."""
    current = state()
    excluded = {
        item["id"] for item in read(".workspace/courses.json")["courses"]
        if item.get("enrollment_status") == "withdrawn"
        or item.get("include_in_personal_schedule") is False
    }
    items = [
        {key: task[key] for key in TASK_FIELDS if key in task}
        for task in current["tasks"]
        if task.get("include_in_personal_schedule") is not False
        and task.get("course_id") not in excluded
        and (include_submitted or task.get("submission_record_status") != "submitted")
    ]
    items.sort(key=lambda item: item.get("due_at") or item.get("due_date") or "9999")
    return {**auth_status(), "coverage": "reviewed_tasks_only", "items": items}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


@mcp.tool(structured_output=True)
def schedule_list(weekday: str | None = None) -> dict[str, Any]:
    """List recorded timetable excerpts, optionally by mon/tue/wed/thu/fri/sat/sun."""
    if weekday is not None and weekday not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
        raise ValueError("Expected a lowercase three-letter weekday")
    snapshot = read(state()["last_coursetable_snapshot"])
    rows = []
    for entry in snapshot["entries"]:
        if weekday is not None and entry["weekday"] != weekday:
            continue
        parser = PlainText()
        parser.feed(entry["course_html"])
        parser.close()
        rows.append({"weekday": entry["weekday"], "period": entry["period"],
                     "text": "".join(parser.parts).strip()})
    return {"mode": "snapshot", "coverage": "excerpt_only",
            "verified_at": snapshot.get("verified_at"), "items": rows}


if __name__ == "__main__":
    mcp.run(transport="stdio")
