"""离线测试：不需要北大账号，也不访问北大网站。"""
import base64
import io
import json
import os
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
import httpx
import pytest
import zxingcpp
from bs4 import BeautifulSoup
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from pku_mcp import auth, campus, setup_page, sync

TOOLS = {"auth_setup", "auth_status", "otp_code", "course_list", "course_hide", "assignment_list",
         "announcement_list", "content_list", "attachment_download", "semester_sync", "schedule_list", "grade_list"}
PROMPTS = {"init_semester", "weekly_plan", "homework"}
SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def qr_png(text: str) -> bytes:
    qr = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode).to_image(scale=4)
    out = io.BytesIO()
    Image.frombytes("L", tuple(qr.shape[::-1]), bytes(memoryview(qr))).save(out, "PNG")
    return out.getvalue()


# ---- 动态口令 -------------------------------------------------------------

def test_totp_matches_rfc6238_vectors():
    seeds = {"SHA1": b"12345678901234567890", "SHA256": b"12345678901234567890123456789012",
             "SHA512": b"1234567890123456789012345678901234567890123456789012345678901234"}
    vectors = {59: ("94287082", "46119246", "90693936"), 1111111109: ("07081804", "68084774", "25091201"),
               20000000000: ("65353130", "77737706", "47863826")}
    for at, expected in vectors.items():
        for algorithm, code in zip(seeds, expected):
            otp = {"secret": base64.b32encode(seeds[algorithm]).decode(), "digits": 8, "period": 30,
                   "algorithm": algorithm}
            assert auth.totp(otp, at) == code


def test_parse_otp_uri_and_rejects_bad_input():
    otp = auth.parse_otp("otpauth://totp/iaaa.pku.edu.cn:2300000000?secret=GEZDGNBVGY3TQOJQ&issuer=iaaa.pku.edu.cn")
    assert otp == {"secret": "GEZDGNBVGY3TQOJQ", "digits": 6, "period": 30, "algorithm": "SHA1",
                   "label": "iaaa.pku.edu.cn:2300000000", "issuer": "iaaa.pku.edu.cn"}
    assert auth.parse_otp("gezd gnbv-gy3t qojq")["secret"] == "GEZDGNBVGY3TQOJQ"
    assert auth.parse_otp('{"secret": "GEZDGNBVGY3TQOJQ", "algorithm": "sha1", "digits": 6, "period": 30, '
                          '"issuer": "iaaa.pku.edu.cn", "label": "a"}')["algorithm"] == "SHA1"
    for bad in ("otpauth://hotp/x?secret=GEZDGNBVGY3TQOJQ", "not base32 !", "otpauth://totp/x?secret=A&secret=B",
                '{"code": 1}'):
        with pytest.raises(ValueError):
            auth.parse_otp(bad)


def test_qr_screenshot_decodes_to_otpauth_uri():
    uri = f"otpauth://totp/PKU:test?secret={SEED}&issuer=PKU"
    assert auth.read_qr(Image.open(io.BytesIO(qr_png(uri)))) == uri
    with pytest.raises(ValueError):
        auth.read_qr(Image.open(io.BytesIO(qr_png("https://example.com"))))


# ---- 页面解析 -------------------------------------------------------------

def test_chinese_times():
    assert campus.cn_time("2026年10月25日 星期日 下午11:59") == "2026-10-25T23:59:00+08:00"
    assert campus.cn_time("发布时间: 2026年9月6日 星期日 上午08时54分00秒 CST") == "2026-09-06T08:54:00+08:00"
    assert campus.cn_time("2026年12月1日 上午12:05") == "2026-12-01T00:05:00+08:00"
    assert campus.cn_time("2026年12月1日") is None


def test_timetable_cell_with_alternating_weeks():
    cell = campus.parse_cell("<font color='red'><b>示例课(主)<br>上课信息：1-16周 单周 理教101  教师：甲 <br>"
                             "考试信息：20270105 星期二 下午 <br>另一课(主)<br>上课信息：1-16周 双周 二教202  教师：乙,丙 "
                             "备注：第16周随堂考<br>考试信息： </b></font>")
    assert cell == [
        {"name": "示例课", "role": "主", "weeks": "1-16周", "parity": "单周", "location": "理教101", "teachers": "甲",
         "exam": {"raw": "20270105 星期二 下午", "date": "2027-01-05", "session": "下午"}},
        {"name": "另一课", "role": "主", "weeks": "1-16周", "parity": "双周", "location": "二教202",
         "teachers": "乙,丙", "note": "第16周随堂考"}]


def test_links_split_course_files_and_external_links():
    files, external = campus.links(BeautifulSoup(
        '<div><a href="/bbcswebdav/pid-1/xid-2_1">讲义.pdf</a><a href="https://github.com/x">模板</a>'
        '<img src="https://course.pku.edu.cn/bbcswebdav/xid-3_1" alt="图"><a href="javascript:void(0)">x</a></div>',
        "html.parser"))
    assert files == [{"name": "讲义.pdf", "url": "https://course.pku.edu.cn/bbcswebdav/pid-1/xid-2_1"},
                     {"name": "图", "url": "https://course.pku.edu.cn/bbcswebdav/xid-3_1"}]
    assert external == [{"title": "模板", "url": "https://github.com/x"}]


# ---- MCP stdio -------------------------------------------------------------

async def mcp_session(home: Path, check):
    params = StdioServerParameters(command=sys.executable, args=["-m", "pku_mcp", "serve"],
                                   env={"PKU_MCP_HOME": str(home)})
    async with stdio_client(params) as streams, ClientSession(*streams) as client:
        await client.initialize()

        async def call(name, args=None):
            result = await client.call_tool(name, args or {})
            assert not result.isError and result.structuredContent is not None, (name, result.content)
            return result.structuredContent

        await check(client, call)


def test_mcp_tools_guide_a_fresh_user_to_setup():
    async def check(client, call):
        assert {t.name for t in (await client.list_tools()).tools} == TOOLS
        assert {p.name for p in (await client.list_prompts()).prompts} == PROMPTS
        prompt = await client.get_prompt("init_semester", {"folder": "~/课程"})
        assert "semester_sync" in prompt.messages[0].content.text
        status = await call("auth_status")
        assert status["password_stored"] is False and "auth_setup" in status["next_step"]
        missing = await call("course_list")
        assert missing["status"] == "credentials_missing" and "auth_setup" in missing["next_step"]
        assert (await call("otp_code"))["status"] == "otp_missing"
        assert (await call("course_hide", {"course_id": "_1_1"}))["hidden_courses"] == ["_1_1"]

    with TemporaryDirectory() as home:
        anyio.run(mcp_session, Path(home), check)


def test_mcp_otp_code_uses_saved_seed_without_leaking_it():
    secret = {"secret": SEED, "digits": 6, "period": 30, "algorithm": "SHA1"}

    async def check(client, call):
        code = await call("otp_code")
        assert code["code"] == auth.totp(secret)
        assert SEED not in json.dumps(await call("auth_status"))

    with TemporaryDirectory() as home:
        (Path(home) / "secrets.json").write_text(json.dumps({"otp": json.dumps(secret)}))
        anyio.run(mcp_session, Path(home), check)


# ---- 本机配置页 -------------------------------------------------------------

def test_setup_command_prints_url_immediately(tmp_path):
    """Agent 常把 pku-mcp setup 放后台运行，地址必须立刻输出，不能等命令结束。"""
    import subprocess
    import threading
    proc = subprocess.Popen([sys.executable, "-m", "pku_mcp", "setup", "--no-browser"], stdout=subprocess.PIPE,
                            env={**os.environ, "PKU_MCP_HOME": str(tmp_path)}, text=True, encoding="utf-8")
    try:
        lines = []
        reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(15)
        assert lines, "pku-mcp setup 没有立即输出配置页地址"
        line = lines[0]
        assert re.match(r"配置页：http://127\.0\.0\.1:\d+/\S{20,}", line), line
        assert httpx.get(line.removeprefix("配置页：").strip()).status_code == 200
    finally:
        proc.kill()
        proc.wait()


def test_setup_page_saves_account_and_otp(monkeypatch):
    def offline(*args):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(auth, "second_factor", offline)
    server = setup_page.start(open_browser=False)
    base = server.url
    origin = base.rsplit("/", 1)[0]
    with httpx.Client() as http:
        assert "pku-mcp 本机配置" in http.get(base).text
        assert http.get(origin + "/").status_code == 404
        assert http.get(base, headers={"Host": "evil.example"}).status_code == 404
        assert http.post(base + "/account", json={"username": "x", "password": "y"},
                         headers={"Origin": "http://evil.example"}).status_code == 404

        reply = http.post(base + "/account", json={"username": "2300000000", "password": "s3cret"}).json()
        assert reply == {"ok": True, "otp_required": None}
        assert auth.credentials() == ("2300000000", "s3cret")
        assert "s3cret" not in http.get(base + "/status").text
        assert http.post(base + "/account", json={"username": "", "password": ""}).json()["ok"] is False

        image = "data:image/png;base64," + base64.b64encode(qr_png(f"otpauth://totp/PKU:t?secret={SEED}")).decode()
        reply = http.post(base + "/otp", json={"image": image}).json()
        assert reply["ok"] and re.fullmatch(r"\d{6}", reply["code"]) and auth.otp_status()["configured"]
        assert http.post(base + "/otp", json={"image": "data:image/png;base64,AAAA"}).json()["ok"] is False
        assert http.post(base + "/otp", json={"text": "not base32 !"}).json()["ok"] is False
    if os.name != "nt":
        assert oct(auth.SECRETS.stat().st_mode & 0o777) == "0o600"


# ---- 学期资料整理 -----------------------------------------------------------

def test_semester_sync_layout_and_incremental(monkeypatch, tmp_path):
    course = {"course_id": "_1_1", "name": "示例课", "term": "26-27学年第1学期", "current": True, "hidden": False}
    contents = {"fetched_at": "t", "items": [
        {"content_id": "_2_1", "title": "第一讲", "kind": "文件", "trail": "教学内容", "text": "",
         "attachments": [{"name": "讲义.pdf", "url": "https://course.pku.edu.cn/bbcswebdav/a"}], "links": []},
        {"content_id": "_3_1", "title": "作业1", "kind": "作业", "trail": "课程作业", "text": "",
         "due_at": "2026-11-20T23:59:00+08:00", "submitted": False,
         "attachments": [{"name": "题目.pdf", "url": "https://course.pku.edu.cn/bbcswebdav/b"},
                         {"name": "录像.mp4", "url": "https://course.pku.edu.cn/bbcswebdav/big"}], "links": []}]}
    notes = {"fetched_at": "t", "items": [{"title": "开课通知", "posted_at": "2026-09-01T08:00:00+08:00",
                                           "attachments": [], "links": []}]}
    fetched = []

    def fake_download(url, folder, max_bytes=None):
        if url.endswith("big"):
            return {"skipped": "too_large", "bytes": 500 << 20, "url": url}
        fetched.append(url)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{url[-1]}.pdf"
        path.write_bytes(b"pdf")
        return {"path": str(path), "sha256": "0" * 64, "bytes": 3, "reused": False}

    monkeypatch.setattr(campus, "courses", lambda **kw: {"items": [course]})
    monkeypatch.setattr(campus, "course_contents", lambda cid, refresh=False: contents)
    monkeypatch.setattr(campus, "announcements", lambda cid, limit=20: notes)
    monkeypatch.setattr(campus, "timetable", lambda: {"items": [
        {"weekday": "mon", "name": "示例课", "start_period": 1, "end_period": 2, "time": "08:00-09:50",
         "location": "理教101", "teachers": "甲", "weeks": "1-16周", "parity": "每周"}]})
    monkeypatch.setattr(campus, "download", fake_download)

    assert sync.sync(str(tmp_path), time_budget=-1)["done"] is False  # 时间用完时分批继续
    report = sync.sync(str(tmp_path))
    assert report["done"] and report["downloaded"] == ["示例课/资料/教学内容/第一讲/a.pdf", "示例课/作业/作业1/b.pdf"]
    assert report["skipped_large"] == [{"course": "示例课", "name": "录像.mp4", "mb": 500}]
    index = (tmp_path / "示例课" / "课程信息.md").read_text(encoding="utf-8")
    assert "周一 第1-2节（08:00-09:50）理教101" in index and "| 2026-11-20 23:59 | 作业1 | 未交 |" in index
    assert "[a.pdf](<资料/教学内容/第一讲/a.pdf>)" in index
    assert "[示例课](<示例课/课程信息.md>)" in (tmp_path / "README.md").read_text(encoding="utf-8")
    again = sync.sync(str(tmp_path))
    assert again["downloaded_count"] == 0 and len(fetched) == 2
