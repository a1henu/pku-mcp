import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def seed(root: Path) -> None:
    folder = root / ".workspace"
    folder.mkdir()
    files = {
        "latest.json": {"snapshot": ".workspace/source.json"},
        "source.json": {"fetched_at": "2026-11-18T12:00:00+08:00"},
        "courses.json": {"courses": [{"id": "archived", "enrollment_status": "withdrawn"}]},
        "schedule.json": {"entries": [{"weekday": "wed", "period": "第一节",
                                         "course_html": "示例课程<br>示例教室"}]},
        "tasks.json": {
            "last_coursetable_snapshot": ".workspace/schedule.json",
            "last_refresh_attempt": {"status": "authentication_failed"},
            "tasks": [
                {"title": "示例作业", "due_date": "2026-11-25"},
                {"title": "归档作业", "course_id": "archived"},
                {"title": "已交作业", "submission_record_status": "submitted"},
            ],
        },
    }
    for name, content in files.items():
        (folder / name).write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


async def verify(root: Path) -> None:
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(Path(__file__).with_name("server.py").resolve())],
                                   env={"PKU_WORKSPACE": str(root)})
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            assert {tool.name for tool in (await session.list_tools()).tools} == {
                "auth_status", "due_list", "schedule_list",
            }

            async def call(name, args):
                result = await session.call_tool(name, args)
                assert not result.isError and result.structuredContent is not None, name
                return result.structuredContent

            status = await call("auth_status", {})
            assert status["last_recorded_attempt"]["status"] == "authentication_failed"
            assert status["source_snapshot_at"] and not status["live_auth_verified"]
            due = await call("due_list", {})
            assert due["items"] == [{"title": "示例作业", "due_date": "2026-11-25"}]
            assert len((await call("due_list", {"include_submitted": True}))["items"]) == 2
            schedule = await call("schedule_list", {"weekday": "wed"})
            assert schedule["coverage"] == "excerpt_only"
            assert schedule["items"][0]["text"] == "示例课程\n示例教室"
            assert not (await call("schedule_list", {"weekday": "thu"}))["items"]
    print("PASS: stdio 握手、工具发现、认证失败快照、待办过滤、课表筛选")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--demo":
        root = Path(sys.argv[2]).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        seed(root)
        print(root)
    elif len(sys.argv) == 1:
        with TemporaryDirectory() as path:
            root = Path(path)
            seed(root)
            asyncio.run(verify(root))
    else:
        raise SystemExit("Usage: python smoke.py [--demo DIRECTORY]")
