"""学期资料整理：把教学网每门课的资料、作业附件和公告附件下载到本机，按课程分文件夹并生成索引。

可以反复运行：已下载的链接记在学期目录的 .pku-mcp-sync.json，只补新文件；从不覆盖或删除已有文件。
索引文件（README.md、各课 课程信息.md）由本模块生成，重新同步会刷新它们。
"""
import json
import os
import time
from pathlib import Path

from . import auth, campus

MANIFEST = ".pku-mcp-sync.json"
WEEKDAY = {"mon": "周一", "tue": "周二", "wed": "周三", "thu": "周四", "fri": "周五", "sat": "周六", "sun": "周日"}


def semester_root(dest: str | None, term: str | None) -> Path:
    return Path(dest).expanduser() if dest else campus.files_root() / campus.safe_name(term or "本学期")


def when(item: dict) -> str:
    return item["due_at"][:16].replace("T", " ") if item.get("due_at") else item.get("deadline_raw") or "未公布"


def state(item: dict) -> str:
    if not item.get("submitted"):
        return "未交（已过截止）" if item.get("due_at") and item["due_at"] < campus.now() else "未交"
    text = "已交" + (f"（{item['last_submitted_at'][:16].replace('T', ' ')}）" if item.get("last_submitted_at") else "")
    if item.get("grade"):
        text += f"，成绩 {item['grade']}" + (f"/{item['points_possible']}" if item.get("points_possible") else "")
    return text


def plan(course: dict) -> tuple[list[dict], dict, dict]:
    """列出一门课要下载的文件（链接、相对目录），同时返回内容和公告数据。"""
    contents = campus.course_contents(course["course_id"])
    notes = campus.announcements(course["course_id"], limit=200)
    folder = campus.safe_name(course["name"])
    files = []
    for item in contents["items"]:
        if item["kind"] == "作业":
            where = [folder, "作业", campus.safe_name(item["title"], 60)]
        else:
            where = [folder, "资料", *(campus.safe_name(p, 40) for p in item["trail"].split(" / ")),
                     campus.safe_name(item["title"], 60)]
        files += [{"url": f["url"], "dir": "/".join(where), "name": f["name"]} for f in item["attachments"]]
    for note in notes["items"]:
        where = [folder, "公告", campus.safe_name(f"{(note['posted_at'] or '')[:10]} {note['title']}".strip(), 60)]
        files += [{"url": f["url"], "dir": "/".join(where), "name": f["name"]} for f in note["attachments"]]
    return files, contents, notes


def classes(name: str, table: list[dict]) -> list[dict]:
    return [c for c in table if c["name"] == name]


def course_index(course: dict, contents: dict, notes: dict, table: list[dict], local: dict, stamp: str) -> str:
    lines = [f"# {course['name']}（{course['term']}）", "",
             f"> pku-mcp 生成于 {stamp}，重新同步会刷新本文件；自己的笔记请另建文件。", ""]
    sessions = classes(course["name"], table)
    if sessions:
        lines += ["## 上课", ""]
        lines += [f"- {WEEKDAY[c['weekday']]} 第{c['start_period']}-{c['end_period']}节（{c['time']}）"
                  f"{c.get('location', '')}，{c.get('teachers', '')}，{c.get('weeks', '')} {c.get('parity', '')}"
                  + (f"；备注：{c['note']}" if c.get("note") else "") for c in sessions]
        exams = {c["exam"]["raw"] for c in sessions if c.get("exam")}
        lines += [f"- 考试：{exam}" for exam in sorted(exams)] + [""]
    homework = sorted((i for i in contents["items"] if i["kind"] == "作业"), key=lambda i: i.get("due_at") or "9999")
    if homework:
        lines += ["## 作业", "", "| 截止 | 作业 | 状态 | 附件 |", "| --- | --- | --- | --- |"]
        for item in homework:
            files = "、".join(f"[{Path(local[f['url']]).name}](<{local[f['url']]}>)" for f in item["attachments"] if f["url"] in local)
            lines.append(f"| {when(item)} | {item['title']} | {state(item)} | {files} |")
        lines.append("")
    if notes["items"]:
        lines += ["## 公告", ""]
        lines += [f"- {(n['posted_at'] or '')[:10]} {n['title']}" for n in notes["items"][:15]] + [""]
    materials = [i for i in contents["items"] if i["kind"] != "作业" and any(f["url"] in local for f in i["attachments"])]
    if materials:
        lines += ["## 资料", ""]
        for item in materials:
            files = "、".join(f"[{Path(local[f['url']]).name}](<{local[f['url']]}>)" for f in item["attachments"] if f["url"] in local)
            lines.append(f"- {item['trail']} / {item['title']}：{files}")
        lines.append("")
    return "\n".join(lines)


def overview(term: str, rows: list[tuple[dict, dict]], table: list[dict], stamp: str) -> str:
    lines = [f"# {term}", "", f"> pku-mcp 生成于 {stamp}，重新同步会刷新本文件。", "",
             "## 课程", "", "| 课程 | 上课 | 考试 | 未交作业 |", "| --- | --- | --- | --- |"]
    upcoming = []
    for course, contents in rows:
        sessions = classes(course["name"], table)
        when_where = "；".join(f"{WEEKDAY[c['weekday']]}{c['start_period']}-{c['end_period']}节 {c.get('location', '')}"
                               for c in sessions) or "—"
        exams = "；".join(sorted({c["exam"]["raw"] for c in sessions if c.get("exam")})) or "—"
        open_items = [i for i in contents["items"] if i["kind"] == "作业" and not i.get("submitted")]
        upcoming += [(course, i) for i in open_items]
        folder = campus.safe_name(course["name"])
        lines.append(f"| [{course['name']}](<{folder}/课程信息.md>) | {when_where} | {exams} | {len(open_items)} |")
    lines += ["", "## 未交作业", "", "| 截止 | 课程 | 作业 | 状态 |", "| --- | --- | --- | --- |"]
    lines += [f"| {when(i)} | {c['name']} | {i['title']} | {state(i)} |"
              for c, i in sorted(upcoming, key=lambda pair: pair[1].get("due_at") or "9999")]
    return "\n".join(lines + [""])


def write(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def sync(dest: str | None = None, course_id: str | None = None, include_hidden: bool = False,
         max_file_mb: int = 100, time_budget: float = 45) -> dict:
    started = time.time()
    courses = [c for c in campus.courses()["items"]
               if c["course_id"] == course_id or (course_id is None and (include_hidden or not c["hidden"]))]
    if not courses:
        raise ValueError(f"当前学期课程里没有 {course_id}" if course_id else "当前学期没有课程")
    term = courses[0]["term"]
    root = semester_root(dest, term)
    root.mkdir(parents=True, exist_ok=True)
    manifest = auth.read_json(root / MANIFEST, {"files": {}})
    try:
        table = campus.timetable()["items"]
    except Exception:  # noqa: BLE001 - 门户读不到（如 OTP 未配置）时仍整理教学网资料
        table = []
    stamp = campus.now()[:16].replace("T", " ")
    report = {"root": str(root), "downloaded": [], "skipped_large": [], "failed": []}
    rows, pending = [], False
    for course in courses:
        files, contents, notes = plan(course)
        for f in files:
            known = manifest["files"].get(f["url"])
            if known and ((root / known["path"]).exists() if "path" in known else known["bytes"] > max_file_mb << 20):
                continue  # 已下载；或上次因过大跳过且上限没调高
            if time.time() - started > time_budget:
                pending = True
                break
            try:
                got = campus.download(f["url"], root / f["dir"], max_file_mb << 20)
            except auth.AuthError:
                raise
            except Exception as e:  # noqa: BLE001 - 单个文件失败不影响其他文件
                report["failed"].append({"course": course["name"], "name": f["name"], "error": f"{type(e).__name__}: {e}"})
                continue
            if got.get("skipped"):
                manifest["files"][f["url"]] = {"skipped": got["skipped"], "bytes": got["bytes"]}
                report["skipped_large"].append({"course": course["name"], "name": f["name"], "mb": got["bytes"] >> 20})
            else:
                path = Path(got["path"]).relative_to(root).as_posix()
                manifest["files"][f["url"]] = {"path": path, "sha256": got["sha256"], "bytes": got["bytes"]}
                if not got["reused"]:
                    report["downloaded"].append(path)
            write(root / MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
        local = {url: Path(entry["path"]).relative_to(campus.safe_name(course["name"])).as_posix()
                 for url, entry in manifest["files"].items() if "path" in entry
                 and entry["path"].startswith(campus.safe_name(course["name"]) + "/")}
        (root / campus.safe_name(course["name"])).mkdir(exist_ok=True)
        write(root / campus.safe_name(course["name"]) / "课程信息.md",
              course_index(course, contents, notes, table, local, stamp))
        rows.append((course, contents))
        if pending:
            break
    if not pending and course_id is None:
        write(root / "README.md", overview(term, rows, table, stamp))
    count = len(report["downloaded"])
    report.update(done=not pending, downloaded_count=count, downloaded=report["downloaded"][:30],
                  files_known=sum("path" in e for e in manifest["files"].values()),
                  courses=[c["name"] for c, _ in rows], timetable=bool(table))
    if pending:
        report["next_step"] = "还没下载完：用相同参数再调用一次 semester_sync 继续（已下载的不会重复下载）"
    return report
