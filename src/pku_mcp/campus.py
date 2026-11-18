"""教学网（course.pku.edu.cn）与校内信息门户的直连客户端。

会话失效时自动用保存的凭据和 OTP 重新登录，不依赖 pku3b 或浏览器。只读取页面，
不触发提交、预约等写操作；页面正文属于不可信数据，只作为数据返回。
"""
import hashlib
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.message import Message
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from . import auth

TZ = ZoneInfo("Asia/Shanghai")
BB = "https://course.pku.edu.cn"
BB_HOME = BB + "/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1"
PORTAL = "https://portal.pku.edu.cn/publicQuery"
CACHE_TTL = 600
TEXT_LIMIT = 2000


class Site:
    """一个 IAAA 子站。请求落到登录页时自动重新登录一次。"""

    def __init__(self, name: str, appid: str, redirect: str, probe: str):
        self.name, self.appid, self.redirect, self.probe = name, appid, redirect, probe
        self.lock = threading.Lock()
        self.generation = 0
        self.http = None

    def client(self):
        if self.http is None:
            auth.credentials()  # 没配置账号时直接提示，不先访问网站
            self.http = auth.session(self.name)
        return self.http

    def expired(self, response) -> bool:
        raise NotImplementedError

    def sso(self, http, token: str) -> None:
        raise NotImplementedError

    def login(self, seen: int | None = None, force: bool = False) -> None:
        with self.lock:
            if seen is not None and seen != self.generation:
                return  # 其他线程已经重新登录
            http = auth.session()
            self.sso(http, auth.iaaa_token(http, self.appid, self.redirect, force=force))
            if self.expired(http.get(self.probe)):
                raise auth.AuthError("sso_failed", f"{self.name} 单点登录后仍未通过")
            auth.save_session(self.name, http)
            self.http, self.generation = http, self.generation + 1

    def get(self, url: str, **kwargs):
        seen = self.generation
        response = self.client().get(url, **kwargs)
        if self.expired(response):
            self.login(seen)
            response = self.client().get(url, **kwargs)
            if self.expired(response):
                raise auth.AuthError("session_rejected", f"{self.name} 重新登录后仍被拒绝")
        response.raise_for_status()
        return response

    def page(self, url: str, **kwargs) -> BeautifulSoup:
        return BeautifulSoup(self.get(url, **kwargs).text, "html.parser")

    def valid(self) -> bool:
        return not self.expired(self.client().get(self.probe))


class Blackboard(Site):
    def expired(self, response) -> bool:
        return response.url.host != "course.pku.edu.cn" or response.url.path.startswith("/webapps/login")

    def sso(self, http, token: str) -> None:
        http.get(BB + "/webapps/bb-sso-BBLEARN/execute/authValidate/campusLogin",
                 params={"_rand": f"{random.random():.20f}", "token": token})


class Portal(Site):
    def expired(self, response) -> bool:
        return response.status_code in (401, 403) or response.url.host != "portal.pku.edu.cn"

    def sso(self, http, token: str) -> None:
        http.get(PORTAL + "/ssoLogin.do", params={"token": token})

    def json(self, path: str, **params) -> dict:
        data = self.get(f"{PORTAL}/ctrl/{path}", params=params).json()
        if not data.get("success"):
            raise RuntimeError(f"门户接口 {path} 返回失败")
        return data


bb = Blackboard("blackboard", "blackboard",
                "http://course.pku.edu.cn/webapps/bb-sso-BBLEARN/execute/authValidate/campusLogin", BB_HOME)
portal = Portal("portal", "portalPublicQuery", PORTAL + "/ssoLogin.do",
                PORTAL + "/ctrl/topic/myCourseTable/getXndXqList.do")


# ---- 通用解析 -------------------------------------------------------------

def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def clean(node) -> str:
    if node is None:
        return ""
    node = BeautifulSoup(str(node), "html.parser")
    for tag in node.select("script, style, .hideme"):
        tag.decompose()
    return "\n".join(line.strip() for line in node.get_text("\n").splitlines() if line.strip())


def clip(text: str) -> dict:
    return {"text": text[:TEXT_LIMIT], **({"text_truncated": True} if len(text) > TEXT_LIMIT else {})}


def cn_time(text: str | None) -> str | None:
    """解析「2026年10月25日 星期日 下午11:59」「上午08时54分00秒」这类时间为 ISO 格式。"""
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*星期.)?\s*(上午|下午)\s*(\d{1,2})[:时](\d{2})",
                  text or "")
    if not m:
        return None
    year, month, day, half, hour, minute = m.groups()
    hour = int(hour) % 12 + (12 if half == "下午" else 0)
    return datetime(int(year), int(month), int(day), hour, int(minute), tzinfo=TZ).isoformat()


def links(node) -> tuple[list, list]:
    """返回（教学网文件，外部链接）。文件只认 course.pku.edu.cn 的 bbcswebdav 地址。"""
    files, external, seen = [], [], set()
    for a in node.select("a[href], img[src]") if node else []:
        url = urljoin(BB, a.get("href") or a.get("src"))
        if url in seen or urlsplit(url).scheme not in ("http", "https"):
            continue
        seen.add(url)
        name = a.get_text(" ", strip=True).lstrip("\xa0") or a.get("alt") or unquote(Path(urlsplit(url).path).name)
        if urlsplit(url).hostname == "course.pku.edu.cn" and "/bbcswebdav/" in url:
            files.append({"name": name, "url": url})
        elif a.name == "a":
            external.append({"title": name, "url": url})
    return files, external


def cache(key: str, refresh: bool, build) -> dict:
    """十分钟内复用上次结果。刷新失败时不覆盖，退回上次成功的结果并标注 stale。"""
    path = auth.HOME / "cache" / f"{key}.json"
    cached = auth.read_json(path)
    if cached and not refresh and time.time() - cached["cached_at"] < CACHE_TTL:
        return cached["data"]
    try:
        data = build()
    except Exception as e:
        if not cached:
            raise
        return {**cached["data"], "stale": {"error": f"{type(e).__name__}: {e}"}}
    auth.write_private(path, {"cached_at": time.time(), "data": data})
    return data


# ---- 教学网 ---------------------------------------------------------------

def courses(include_history: bool = False, refresh: bool = False) -> dict:
    def build():
        found = []
        for module in bb.page(BB_HOME).select("div.portlet"):
            title = clean(module.select_one("span.moduleTitle"))
            current = "当前" in title or "Current" in title
            if not current and "历史" not in title and "History" not in title:
                continue
            for a in module.select("ul.courseListing li a[href]"):
                key = re.search(r"key=(_\d+_\d+)", a["href"])
                text = a.get_text(" ", strip=True)
                parts = re.match(r"[^:]+:\s*(?P<name>.*?)\s*\((?P<term>[^()]*学期)\)$", text)
                if key:
                    found.append({"course_id": key[1], "name": parts["name"] if parts else text,
                                  "term": parts["term"] if parts else None, "current": current})
        if not any(course["current"] for course in found):
            raise RuntimeError("教学网首页没有找到「当前学期课程」模块")
        return {"fetched_at": now(), "items": found}

    data = cache("courses", refresh, build)
    hidden = set(auth.config().get("hidden_courses", []))
    return {**data, "items": [{**course, "hidden": course["course_id"] in hidden}
                              for course in data["items"] if include_history or course["current"]]}


def course_menu(course_id: str) -> tuple[str, BeautifulSoup]:
    url = BB + "/webapps/blackboard/execute/announcement?" + urlencode({
        "method": "search", "context": "course_entry", "course_id": course_id,
        "handle": "announcements_entry", "mode": "view"})
    return url, bb.page(url)


def assignment_detail(course_id: str, content_id: str) -> dict:
    url = BB + "/webapps/assignment/uploadAssignment?" + urlencode(
        {"mode": "view", "content_id": content_id, "course_id": course_id})
    soup = bb.page(url)
    text = " ".join(clean(soup.select_one("#contentPanel") or soup.body).split())
    raw = re.search(r"到期日期\s*(\d{4}年\d{1,2}月\d{1,2}日(?:\s*星期.)?(?:\s*(?:上午|下午)\s*\d{1,2}:\d{2})?)", text)
    attempts = [" ".join(x.get_text(" ", strip=True).split()) for x in soup.select("h3#currentAttempt_label")]
    last = re.search(r"(\d{2})-(\d{1,2})-(\d{1,2})\s*(上午|下午)\s*(\d{1,2}):(\d{2})", attempts[-1] if attempts else "")
    grade = soup.select_one("input#aggregateGrade")
    detail = {"deadline_raw": raw[1] if raw else None, "due_at": cn_time(raw[1] if raw else None),
              "submitted": bool(attempts), "attempts": attempts,
              "last_submitted_at": cn_time("20{}年{}月{}日 {}{}:{}".format(*last.groups())) if last else None,
              "points_possible": clean(soup.select_one("#aggregateGrade_pointsPossible")).lstrip("/") or None,
              "url": url}
    if grade is not None and grade.get("value", "").strip() not in ("", "-"):
        detail["grade"] = grade["value"].strip()
    return detail


def course_contents(course_id: str, refresh: bool = False) -> dict:
    """遍历课程菜单里的所有内容区与子文件夹；作业条目附带截止时间和提交状态。"""
    def build():
        source, menu = course_menu(course_id)
        queue = [(clean(a), urljoin(BB, a["href"])) for a in menu.select("#courseMenuPalette_contents > li > a[href]")
                 if "listContent.jsp" in a["href"]]
        items, visited, errors = [], set(), []
        while queue:
            trail, url = queue.pop(0)
            folder = parse_qs(urlsplit(url).query).get("content_id", [url])[0]
            if folder in visited:
                continue
            visited.add(folder)
            try:
                soup = bb.page(url)
            except auth.AuthError:
                raise
            except Exception as e:  # noqa: BLE001 - 单个文件夹失败不影响其他条目
                errors.append({"url": url, "error": type(e).__name__})
                continue
            for li in soup.select("#content_listContainer > li"):
                item = li.find("div", class_="item", recursive=False)
                if item is None or not item.get("id"):
                    continue
                icon = li.find("img", recursive=False)
                details = li.find("div", class_="details", recursive=False)
                files, external = links(details)
                record = {"content_id": item["id"], "title": clean(item), "kind": icon.get("alt") if icon else None,
                          "trail": trail, **clip(clean(details.select_one(".vtbegenerated") if details else None)),
                          "attachments": files, "links": external, "source_url": url}
                for a in item.select("a[href]"):
                    href = urljoin(BB, a["href"])
                    if "listContent.jsp" in href:
                        queue.append((f"{trail} / {record['title']}", href))
                    elif urlsplit(href).hostname == "course.pku.edu.cn":
                        record["url"] = href
                items.append(record)
        homework = [item for item in items if item["kind"] == "作业"]
        with ThreadPoolExecutor(4) as pool:
            for item, detail in zip(homework, pool.map(
                    lambda item: assignment_detail(course_id, item["content_id"]), homework)):
                item.update(detail)
        return {"course_id": course_id, "fetched_at": now(), "source_url": source, "items": items, "errors": errors}

    return cache(f"course-{course_id}", refresh, build)


ASSIGNMENT_FIELDS = ("content_id", "title", "due_at", "deadline_raw", "submitted", "last_submitted_at", "attempts", "grade",
                     "points_possible", "text", "text_truncated", "attachments", "links", "trail", "url")


def assignments(course_id: str | None = None, include_submitted: bool = False,
                include_hidden: bool = False, refresh: bool = False) -> dict:
    targets = [c for c in courses(refresh=refresh)["items"]
               if c["course_id"] == course_id or (course_id is None and (include_hidden or not c["hidden"]))]
    if course_id and not targets:
        raise ValueError(f"当前学期课程里没有 {course_id}")
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda c: course_contents(c["course_id"], refresh), targets))
    items, errors = [], []
    for course, result in zip(targets, results):
        errors += [{**error, "course": course["name"]} for error in result["errors"]]
        for item in result["items"]:
            if item["kind"] == "作业" and (include_submitted or not item.get("submitted")):
                fields = {key: item.get(key) for key in ASSIGNMENT_FIELDS}
                items.append({"course": course["name"], "course_id": course["course_id"],
                              **{key: value for key, value in fields.items() if value not in (None, [])}})
    items.sort(key=lambda item: item.get("due_at") or "9999")
    stale = [{"course": c["name"], **r["stale"]} for c, r in zip(targets, results) if "stale" in r]
    return {"fetched_at": min((r["fetched_at"] for r in results), default=now()), "items": items,
            "courses_checked": [c["name"] for c in targets], "errors": errors, **({"stale": stale} if stale else {}),
            "coverage": "教学网各课程菜单下的内容区与子文件夹；公告里另行布置的任务需再查 announcement_list"}


def announcements(course_id: str | None = None, limit: int = 20, include_history: bool = False,
                  refresh: bool = False) -> dict:
    def build():
        if course_id:
            url, soup = course_menu(course_id)
        else:
            url = BB + "/webapps/blackboard/execute/announcement?" + urlencode(
                {"method": "search", "context": "mybb", "handle": "my_announcements"})
            soup = bb.page(url)
        found = []
        for li in soup.select("#announcementList > li[id]"):
            details = li.select_one(".details")
            body = details.select_one(".vtbegenerated") if details else None
            info = clean(li.select_one(".announcementInfo"))
            posted = clean(details.find("p") if details else None)
            files, external = links(body)
            found.append({"id": li["id"], "title": clean(li.select_one("h3")), "posted_at": cn_time(posted),
                          "posted_raw": posted.removeprefix("发布时间:").strip() or None,
                          "author": (re.search(r"发帖者:\s*(.+)", info) or [None, None])[1],
                          "posted_to": (re.search(r"发布至:\s*(.+)", info) or [None, None])[1],
                          **clip(clean(body)), "attachments": files, "links": external, "source_url": url})
        return {"fetched_at": now(), "items": found}

    data = cache(f"announcements-{course_id or 'all'}", refresh, build)
    found = data["items"]
    if not course_id and not include_history:
        current = {f"{c['name']}({c['term']})".replace(" ", "") for c in courses()["items"] if not c["hidden"]}
        found = [a for a in found if (a["posted_to"] or "").replace(" ", "") in current]
    found = sorted(found, key=lambda a: a["posted_at"] or "", reverse=True)
    return {**data, "items": found[:limit], "total": len(found)}


def files_root() -> Path:
    """下载和学期资料的默认根目录：PKU_FILES_DIR，默认 ~/PKU。"""
    return Path(os.environ.get("PKU_FILES_DIR") or Path.home() / "PKU").expanduser()


def safe_name(name: str, limit: int = 120) -> str:
    return re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", name).strip(" .")[:limit] or "untitled"


def download(url: str, folder: Path, max_bytes: int | None = None) -> dict:
    """下载教学网文件到 folder：不覆盖同名文件，内容相同则复用已有文件；超过 max_bytes 时跳过。"""
    url = urljoin(BB, url)
    parts = urlsplit(url)
    if parts.hostname != "course.pku.edu.cn" or not parts.path.startswith(("/bbcswebdav/", "/webapps/")):
        raise ValueError("只下载 course.pku.edu.cn 上的教学网文件")
    folder = Path(folder).expanduser()
    seen = bb.generation
    for _ in range(2):
        with bb.client().stream("GET", url) as response:
            if bb.expired(response):
                bb.login(seen)
                continue
            response.raise_for_status()
            size = int(response.headers.get("content-length") or 0)
            if max_bytes and size > max_bytes:
                return {"skipped": "too_large", "bytes": size, "url": url}
            header = Message()
            header["content-disposition"] = response.headers.get("content-disposition", "")
            name = safe_name(header.get_filename() or unquote(Path(response.url.path).name) or "attachment", 150)
            if response.headers.get("content-type", "").startswith("text/html") and not name.endswith((".html", ".htm")):
                raise RuntimeError("服务器返回网页而不是文件，可能无权访问或链接已失效")
            folder.mkdir(parents=True, exist_ok=True)
            temp = folder / f".{name}.part"
            digest = hashlib.sha256()
            with temp.open("wb") as f:
                for chunk in response.iter_bytes(1 << 20):
                    digest.update(chunk)
                    f.write(chunk)
        sha, size = digest.hexdigest(), temp.stat().st_size
        for existing in folder.iterdir():
            if existing.is_file() and not existing.name.startswith(".") and existing.stat().st_size == size \
                    and hashlib.sha256(existing.read_bytes()).hexdigest() == sha:
                temp.unlink()
                return {"path": str(existing), "sha256": sha, "bytes": size, "reused": True}
        target = folder / name
        if target.exists():
            target = folder / f"{target.stem}-{sha[:8]}{target.suffix}"
        temp.replace(target)
        return {"path": str(target), "sha256": sha, "bytes": size, "reused": False}
    raise auth.AuthError("session_rejected", "重新登录后仍无法下载")


# ---- 校内信息门户：课表与成绩 ---------------------------------------------

PERIODS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]
# 北大本科作息时间（第 1–12 节）。
CLOCK = ["08:00-08:50", "09:00-09:50", "10:10-11:00", "11:10-12:00", "13:00-13:50", "14:00-14:50",
         "15:10-16:00", "16:10-17:00", "17:10-18:00", "18:40-19:30", "19:40-20:30", "20:40-21:30"]
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_cell(html: str) -> list[dict]:
    """把课表单元格拆成课程；同一格可能有单双周不同的两门课。"""
    text = BeautifulSoup(re.sub(r"<br\s*/?>", "\n", html), "html.parser").get_text()
    blocks = []
    for line in filter(None, (line.strip() for line in text.splitlines())):
        if line.startswith("上课信息：") and blocks:
            info = line.removeprefix("上课信息：").strip()
            m = re.match(r"(?P<weeks>\S+周)\s+(?P<parity>\S+)\s+(?P<location>.*?)\s*教师：(?P<teachers>.*?)"
                         r"(?:\s*备注：(?P<note>.*))?$", info)
            blocks[-1].update({k: v.strip() for k, v in m.groupdict().items() if v} if m else {"info": info})
        elif line.startswith("考试信息：") and blocks:
            exam = line.removeprefix("考试信息：").strip()
            m = re.match(r"(\d{4})(\d{2})(\d{2})\s*(?:星期\S)?\s*(\S+)?", exam)
            if exam:
                blocks[-1]["exam"] = {"raw": exam, **({"date": f"{m[1]}-{m[2]}-{m[3]}", "session": m[4]} if m else {})}
        else:
            name = re.match(r"(.*?)\s*(?:\(([^()]*)\))?$", line)
            blocks.append({"name": name[1], **({"role": name[2]} if name[2] else {})})
    return blocks


def timetable(term: str | None = None, refresh: bool = False) -> dict:
    def build():
        terms = portal.json("topic/myCourseTable/getXndXqList.do")
        chosen = term or terms["nowXnxq"]["xndxq"]
        table = portal.json("topic/myCourseTable/getCourseInfo.do", xndxq=chosen)
        cells = []
        for slot in table["course"]:
            period = PERIODS.index(slot["timeNum"].removeprefix("第").removesuffix("节")) + 1
            for day in WEEKDAYS:
                cells += [{"weekday": day, "period": period, **block} for block in parse_cell(slot[day]["courseName"])]

        def same_class(cell):
            return {k: v for k, v in cell.items() if k not in ("period", "start_period", "end_period")}

        merged = []
        for cell in sorted(cells, key=lambda c: (WEEKDAYS.index(c["weekday"]), c["name"], c["period"])):
            last = merged[-1] if merged else None
            if last and same_class(last) == same_class(cell) and last["end_period"] == cell["period"] - 1:
                last["end_period"] = cell["period"]
            else:
                merged.append({**same_class(cell), "start_period": cell["period"], "end_period": cell["period"]})
        for item in merged:
            item["time"] = f"{CLOCK[item['start_period'] - 1][:5]}-{CLOCK[item['end_period'] - 1][6:]}"
        merged.sort(key=lambda c: (WEEKDAYS.index(c["weekday"]), c["start_period"]))
        names = {t["xndxq"]: t["xndxqValue"] for t in terms["xndxq"]}
        return {"term": chosen, "term_name": names.get(chosen), "fetched_at": now(), "items": merged,
                "remark": table.get("remark") or None}

    return cache(f"timetable-{term or 'current'}", refresh, build)


def grades(refresh: bool = False) -> dict:
    def build():
        data = portal.json("topic/myScore/retrScores.do")
        terms = [{"term": f"{group['xnd']}-{group['xq']}", "courses": [{
            "name": c.get("kcmc"), "score": c.get("xqcj"), "credits": c.get("xf"), "category": c.get("kclbmc"),
            "grading": c.get("cjjlfs"), "course_code": c.get("kch"),
            "teachers": [re.sub(r"^\d+-", "", t.split("$")[0]) for t in (c.get("skjsxm") or "").split(",") if t]}
            for c in group.get("list", [])]} for group in data.get("cjxx") or []]
        gpa = portal.json("topic/myScore/getGPAbyXh.do").get("data") or []
        return {"fetched_at": now(), "overall": data.get("gpa"), "term_gpa": gpa, "terms": terms}

    return cache("grades", refresh, build)


# ---- 登录检查与真实账号验收 -------------------------------------------------

def check_login(force: bool = False) -> dict:
    """重新登录教学网和门户（门户通常需要 OTP），返回每个站点的结果。"""
    result = {}
    for site in (bb, portal):
        try:
            site.login(force=force)
            result[site.name] = {"ok": True}
        except auth.AuthError as e:
            result[site.name] = {"ok": False, "status": e.status, "message": e.message}
        except httpx.HTTPError as e:
            result[site.name] = {"ok": False, "status": "network_error", "message": type(e).__name__}
    return result


def acceptance(force: bool = False) -> int:
    """重新登录教学网与门户并读取各类数据；只打印数量和课程名。"""
    checks = []

    def step(name, run):
        started = time.time()
        try:
            checks.append((True, name, run(), time.time() - started))
        except Exception as e:  # noqa: BLE001
            checks.append((False, name, f"{type(e).__name__}: {e}", time.time() - started))

    step("教学网登录", lambda: bb.login(force=force) or "ok")
    step("门户登录（自动 OTP）", lambda: portal.login(force=force) or "ok")
    step("课程列表", lambda: "、".join(c["name"] for c in courses(refresh=True)["items"]))
    step("作业", lambda: (lambda r: f"{len(r['items'])} 项（含已交），{len(r['errors'])} 个页面读取失败")(
        assignments(include_submitted=True, include_hidden=True, refresh=True)))
    step("公告", lambda: f"本学期 {announcements(refresh=True)['total']} 条")
    step("课表", lambda: (lambda t: f"{t['term_name']} {len(t['items'])} 段")(timetable(refresh=True)))
    step("成绩", lambda: f"{len(grades(refresh=True)['terms'])} 个学期")
    for ok, name, detail, seconds in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}（{seconds:.1f}s）：{detail}")
    return 0 if all(ok for ok, *_ in checks) else 1
