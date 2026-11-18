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


def sync(interactive: bool = False) -> dict:
    from playwright.sync_api import Error, sync_playwright

    ROOT.mkdir(mode=0o700, exist_ok=True)
    profile = ROOT / "campus-browser"
    if not interactive and not profile.is_dir():
        return {"status": "reauth_required"}
    profile.mkdir(mode=0o700, exist_ok=True)
    try:
        with sync_playwright() as browser:
            context = browser.chromium.launch_persistent_context(str(profile), headless=not interactive,
                                                                 timeout=15000)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(15000)
                page.goto(HOME, wait_until="domcontentloaded")
                if interactive:
                    input("请在浏览器自行登录教学网（需要时输入 OTP）；看到教学网后回终端按 Enter：")
                    page.goto(HOME, wait_until="domcontentloaded")
                origin = urlsplit(page.url)
                if origin.scheme != "https" or origin.netloc != "course.pku.edu.cn":
                    return {"status": "reauth_required"}
                if "/webapps/login" in origin.path or page.locator('input[type="password"]').count():
                    return {"status": "reauth_required"}
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
        return {"status": "browser_error"}
    if not courses:
        return {"status": "course_module_not_found"}
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=ROOT, delete=False) as temp:
        json.dump({"verified_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                   "items": list(courses.values())}, temp, ensure_ascii=False)
    os.replace(temp.name, ROOT / "campus-courses.json")
    return {"status": "refreshed", "count": len(courses)}


if __name__ == "__main__":
    result = sync(interactive=True)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "refreshed" else 1)
