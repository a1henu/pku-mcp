import json
import os
import re
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent / ".local"
HOME = "https://course.pku.edu.cn/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1"


def sync() -> None:
    from playwright.sync_api import Error, sync_playwright

    ROOT.mkdir(mode=0o700, exist_ok=True)
    profile = ROOT / "campus-browser"
    profile.mkdir(mode=0o700, exist_ok=True)
    try:
        with sync_playwright() as browser:
            context = browser.chromium.launch_persistent_context(str(profile), headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(HOME, wait_until="domcontentloaded")
                input("请在浏览器自行登录教学网（需要时输入 OTP）；看到教学网后回终端按 Enter：")
                page.goto(HOME, wait_until="domcontentloaded")
                origin = urlsplit(page.url)
                if origin.scheme != "https" or origin.netloc != "course.pku.edu.cn":
                    raise SystemExit("未进入教学网，未更新快照。")
                courses = {}
                for module in page.locator("div.portlet").all():
                    header = module.locator("span.moduleTitle").first
                    if not header.count() or not re.search("当前|Current Semester Courses", header.inner_text()):
                        continue
                    for link in module.locator("ul.courseListing li a[href]").all():
                        match = re.search(r"key=(_\d+_\d+),", link.get_attribute("href") or "")
                        if match:
                            courses[match[1]] = {"course_id": match[1], "title": link.inner_text().strip()}
            finally:
                context.close()
    except Error:
        raise SystemExit("浏览器读取失败。请检查 Chromium 安装、网络和登录状态；快照未更新。") from None
    if not courses:
        raise SystemExit("未识别本学期课程模块，未更新快照。")
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=ROOT, delete=False) as temp:
        json.dump({"verified_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                   "items": list(courses.values())}, temp, ensure_ascii=False)
    os.replace(temp.name, ROOT / "campus-courses.json")
    print(f"教学网读取通过：{len(courses)} 门课程。")


if __name__ == "__main__":
    sync()
