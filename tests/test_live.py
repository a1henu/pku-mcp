"""真实账号端到端测试：PKU_MCP_LIVE=1 uv run pytest tests/test_live.py -s

经 MCP stdio 调用全部只读工具，使用本机已配置的账号（~/.pku-mcp）。下载只写进临时目录。
"""
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.skipif(os.environ.get("PKU_MCP_LIVE") != "1", reason="设置 PKU_MCP_LIVE=1 才用真实账号测试")


async def run(files: str):
    env = {"PKU_MCP_HOME": os.environ["PKU_MCP_LIVE_HOME"], "PKU_FILES_DIR": files}
    params = StdioServerParameters(command=sys.executable, args=["-m", "pku_mcp", "serve"], env=env)
    async with stdio_client(params) as streams, ClientSession(*streams) as client:
        await client.initialize()

        async def call(name, args=None):
            result = await client.call_tool(name, args or {})
            assert not result.isError and result.structuredContent is not None, (name, result.content)
            assert set(result.structuredContent) != {"status", "message", "next_step"}, result.structuredContent
            return result.structuredContent

        status = await call("auth_status", {"check_login": True})
        assert all(v in ("valid", "relogged_in") for v in status["sessions"].values()), status
        print("\n认证：", status["sessions"], "OTP 已配置" if status["otp"]["configured"] else "无 OTP")
        assert re.fullmatch(r"\d{6,8}", (await call("otp_code"))["code"])
        courses = (await call("course_list", {"refresh": True}))["items"]
        work = await call("assignment_list", {"include_submitted": True, "refresh": True})
        assert courses and not work["errors"]
        notes = await call("announcement_list", {"limit": 50})
        table = await call("schedule_list")
        grades = await call("grade_list")
        print(f"课程 {len(courses)} 门；作业 {len(work['items'])} 项（未交 {sum(not i['submitted'] for i in work['items'])}）；"
              f"公告 {notes['total']} 条；课表 {len(table['items'])} 段；成绩 {len(grades['terms'])} 个学期")
        contents = await call("content_list", {"course_id": courses[0]["course_id"]})
        files = [f for item in contents["items"] + notes["items"] for f in item["attachments"]]
        if files:
            first = await call("attachment_download", {"url": files[0]["url"]})
            again = await call("attachment_download", {"url": files[0]["url"]})
            assert again["reused"] and again["path"] == first["path"]
        target = next(c for c in courses if not c["hidden"])
        while not (report := await call("semester_sync", {"course_id": target["course_id"], "max_file_mb": 5}))["done"]:
            pass
        index = Path(report["root"]) / target["name"] / "课程信息.md"
        assert index.is_file() and not report["failed"], report
        print(f"学期整理（{target['name']}，≤5MB）：新下载 {report['downloaded_count']} 个，"
              f"跳过大文件 {len(report['skipped_large'])} 个，课表{'已' if report['timetable'] else '未'}写入索引")


def test_live_account_end_to_end():
    with TemporaryDirectory() as files:
        anyio.run(run, files)
