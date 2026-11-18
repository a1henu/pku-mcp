"""验收脚本。

    uv run python smoke.py          # 离线：TOTP 向量、页面解析、stdio 握手与无账号时的提示（不需要北大账号）
    uv run python smoke.py --live   # 真实账号：经 MCP stdio 调用全部工具（需先 cli.py setup）
    uv run python smoke.py --demo DIR  # 生成课程工作区演示数据（配合 PKU_WORKSPACE 使用 workspace_tasks）
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
TOOLS = {"auth_status", "otp_code", "course_list", "course_hide", "assignment_list", "announcement_list",
         "content_list", "attachment_download", "schedule_list", "grade_list"}


def seed(root: Path) -> None:
    folder = root / ".workspace"
    folder.mkdir()
    files = {
        "latest.json": {"snapshot": ".workspace/source.json"},
        "source.json": {"fetched_at": "2026-11-18T12:00:00+08:00"},
        "courses.json": {"courses": [{"id": "archived", "enrollment_status": "withdrawn"}]},
        "tasks.json": {"tasks": [
            {"title": "示例作业", "due_date": "2026-11-25"},
            {"title": "归档作业", "course_id": "archived"},
            {"title": "已交作业", "submission_record_status": "submitted"},
        ]},
    }
    for name, content in files.items():
        (folder / name).write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def offline_units() -> None:
    sys.path.insert(0, str(HERE))
    import auth
    import campus

    # RFC 6238 附录 B 测试向量
    seeds = {"SHA1": b"12345678901234567890", "SHA256": b"12345678901234567890123456789012",
             "SHA512": b"1234567890123456789012345678901234567890123456789012345678901234"}
    vectors = {59: ("94287082", "46119246", "90693936"), 1111111109: ("07081804", "68084774", "25091201"),
               20000000000: ("65353130", "77737706", "47863826")}
    import base64
    for at, expected in vectors.items():
        for algorithm, code in zip(seeds, expected):
            otp = {"secret": base64.b32encode(seeds[algorithm]).decode(), "digits": 8, "period": 30,
                   "algorithm": algorithm}
            assert auth.totp(otp, at) == code, (algorithm, at)
    otp = auth.parse_otp("otpauth://totp/iaaa.pku.edu.cn:2300000000?secret=GEZDGNBVGY3TQOJQ&issuer=iaaa.pku.edu.cn")
    assert otp == {"secret": "GEZDGNBVGY3TQOJQ", "digits": 6, "period": 30, "algorithm": "SHA1",
                   "label": "iaaa.pku.edu.cn:2300000000", "issuer": "iaaa.pku.edu.cn"}
    for bad in ("otpauth://hotp/x?secret=GEZDGNBVGY3TQOJQ", "not base32 !", "otpauth://totp/x?secret=A&secret=B"):
        try:
            auth.parse_otp(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    # 绑定页二维码 → otpauth URI
    import zxingcpp
    from PIL import Image
    import cli
    uri = "otpauth://totp/PKU:test?secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ&issuer=PKU"
    qr = zxingcpp.create_barcode(uri, zxingcpp.BarcodeFormat.QRCode).to_image(scale=4)
    assert cli.read_qr(Image.frombytes("L", tuple(qr.shape[::-1]), bytes(memoryview(qr)))) == uri

    assert campus.cn_time("2026年10月25日 星期日 下午11:59") == "2026-10-25T23:59:00+08:00"
    assert campus.cn_time("发布时间: 2026年9月6日 星期日 上午08时54分00秒 CST") == "2026-09-06T08:54:00+08:00"
    assert campus.cn_time("2026年12月1日 上午12:05") == "2026-12-01T00:05:00+08:00"
    assert campus.cn_time("2026年12月1日") is None
    cell = campus.parse_cell("<font color='red'><b>示例课(主)<br>上课信息：1-16周 单周 理教101  教师：甲 <br>"
                             "考试信息：20270105 星期二 下午 <br>另一课(主)<br>上课信息：1-16周 双周 二教202  教师：乙,丙 "
                             "备注：第16周随堂考<br>考试信息： </b></font>")
    assert cell == [
        {"name": "示例课", "role": "主", "weeks": "1-16周", "parity": "单周", "location": "理教101", "teachers": "甲",
         "exam": {"raw": "20270105 星期二 下午", "date": "2027-01-05", "session": "下午"}},
        {"name": "另一课", "role": "主", "weeks": "1-16周", "parity": "双周", "location": "二教202", "teachers": "乙,丙",
         "note": "第16周随堂考"}], cell
    from bs4 import BeautifulSoup
    files, external = campus.links(BeautifulSoup(
        '<div><a href="/bbcswebdav/pid-1/xid-2_1">讲义.pdf</a><a href="https://github.com/x">模板</a>'
        '<img src="https://course.pku.edu.cn/bbcswebdav/xid-3_1" alt="图"><a href="javascript:void(0)">x</a></div>',
        "html.parser"))
    assert files == [{"name": "讲义.pdf", "url": "https://course.pku.edu.cn/bbcswebdav/pid-1/xid-2_1"},
                     {"name": "图", "url": "https://course.pku.edu.cn/bbcswebdav/xid-3_1"}], files
    assert external == [{"title": "模板", "url": "https://github.com/x"}], external


async def session(env: dict, check):
    params = StdioServerParameters(command=sys.executable, args=[str(HERE / "server.py")], env=env)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as client:
            await client.initialize()

            async def call(name, args=None):
                result = await client.call_tool(name, args or {})
                assert not result.isError and result.structuredContent is not None, (name, result.content)
                return result.structuredContent

            await check(client, call)


async def offline_stdio(home: Path, workspace: Path) -> None:
    env = {"PKU_MCP_HOME": str(home), "PKU_MCP_SECRET_STORE": "file", "PKU_OTP_COMMAND": "",
           "PKU_WORKSPACE": str(workspace)}

    async def check(client, call):
        assert {t.name for t in (await client.list_tools()).tools} == TOOLS | {"workspace_tasks"}
        status = await call("auth_status")
        assert status["password_stored"] is False and status["otp"] == {"source": "none"}, status
        missing = await call("course_list")
        assert missing["status"] == "credentials_missing" and "cli.py setup" in missing["next_step"], missing
        assert (await call("otp_code"))["status"] == "otp_missing"
        tasks = await call("workspace_tasks")
        assert [t["title"] for t in tasks["items"]] == ["示例作业"], tasks
        assert (await call("course_hide", {"course_id": "_1_1"}))["hidden_courses"] == ["_1_1"]

    await session(env, check)
    # 写入合成 OTP 种子后，经 MCP 取到的口令应与本地计算一致。
    sys.path.insert(0, str(HERE))
    import auth
    secret = {"secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", "digits": 6, "period": 30, "algorithm": "SHA1"}
    (home / "secrets.json").write_text(json.dumps({"otp": json.dumps(secret)}))

    async def check_otp(client, call):
        code = await call("otp_code")
        assert code["source"] == "builtin" and code["code"] == auth.totp(secret), code
        assert "GEZDGNBV" not in json.dumps(await call("auth_status"))

    await session(env, check_otp)


async def live(downloads: str) -> None:
    env = {**{k: v for k, v in os.environ.items() if k != "PKU_WORKSPACE"}, "PKU_DOWNLOAD_DIR": downloads}

    async def check(client, call):
        assert TOOLS <= {t.name for t in (await client.list_tools()).tools}
        status = await call("auth_status", {"check_login": True})
        assert all(v in ("valid", "relogged_in") for v in status["sessions"].values()), status
        print("认证：", status["username"], status["otp"]["source"], status["sessions"])
        assert re.fullmatch(r"\d{6,8}", (await call("otp_code"))["code"])
        courses = (await call("course_list", {"refresh": True}))["items"]
        assert courses, "没有当前学期课程"
        work = await call("assignment_list", {"include_submitted": True, "refresh": True})
        assert "status" not in work and not work["errors"], work.get("errors") or work
        print(f"课程 {len(courses)} 门；作业 {len(work['items'])} 项，未交 "
              f"{sum(not i['submitted'] for i in work['items'])} 项")
        notes = await call("announcement_list", {"limit": 50})
        table = await call("schedule_list")
        grades = await call("grade_list")
        print(f"公告 {notes['total']} 条；课表 {table['term_name']} {len(table['items'])} 段；成绩 {len(grades['terms'])} 个学期")
        contents = await call("content_list", {"course_id": courses[0]["course_id"]})
        files = [f for item in contents["items"] + notes["items"] for f in item["attachments"]]
        if files:
            got = await call("attachment_download", {"url": files[0]["url"], "subdir": "smoke"})
            again = await call("attachment_download", {"url": files[0]["url"], "subdir": "smoke"})
            assert "path" in got and again["reused"] and again["path"] == got["path"], (got, again)
            print("附件下载：", Path(got["path"]).name, got["bytes"], "字节；重复下载复用同一文件")

    await session(env, check)
    print("PASS: 真实账号经 MCP stdio 完成自动登录（含 OTP）与全部只读工具")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--demo":
        root = Path(sys.argv[2]).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        seed(root)
        print(root)
    elif sys.argv[1:] == ["--live"]:
        with TemporaryDirectory() as downloads:
            asyncio.run(live(downloads))
    elif len(sys.argv) == 1:
        offline_units()
        with TemporaryDirectory() as home, TemporaryDirectory() as workspace:
            seed(Path(workspace))
            asyncio.run(offline_stdio(Path(home), Path(workspace)))
        print("PASS: TOTP 向量、OTP 二维码解析、页面解析、stdio 工具发现、无凭据提示、内置 OTP、工作区任务")
    else:
        raise SystemExit(__doc__)
