"""Chaoxing (Xuexitong) MCP server — direct HTTP with a persistent cookie session.

Tools:
  xt_login()                 AES login, cookie jar persisted to disk
  xt_courses()               all courses [{courseId, clazzId, cpi, name}]
  xt_homework(course)        homework list for one course (name substring or id)
  xt_homework_all()          scan every course that has a cached enc
  xt_seed_enc(course_id, stuenc, work_enc)  add a course's enc pair (one-time,
                             captured from a browser session; values are stable)
  xt_page_text(url)          fetch any chaoxing page as cleaned text

Login (reverse-engineered from passport2 login.js, 2026-09):
  POST https://passport2.chaoxing.com/fanyalogin with AES-CBC(phone) and
  AES-CBC(password); key = iv = "u2oh6Vu^HWe4_AES", PKCS7, Base64 out.

The homework list endpoint requires per-course `stuenc` + `enc` query params.
These are stable per (course, clazz, student), so they live in enc_map.json
next to this file; seed them once from a browser via xt_seed_enc.

Account source (first match wins):
  1. env XT_PHONE and XT_PASSWORD
  2. env XT_ACCOUNT_FILE  — a markdown file with lines like
        | 账号（手机号） | 13800000000 |
        | 密码 | yourpassword |
  3. ../账号信息.md relative to this file (original local layout)

Protocol: newline-delimited JSON-RPC 2.0 over stdio (MCP stdio transport).
"""
import base64
import json
import os
import re
import sys
import time

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "cookies.json")
ENC_FILE = os.path.join(HERE, "enc_map.json")
KEY = b"u2oh6Vu^HWe4_AES"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")


def aes_enc(text: str) -> str:
    cipher = AES.new(KEY, AES.MODE_CBC, KEY)
    return base64.b64encode(cipher.encrypt(pad(text.encode(), 16))).decode()


def load_account():
    """Return (phone, password). Env vars win; else parse an account markdown file."""
    phone, pwd = os.environ.get("XT_PHONE"), os.environ.get("XT_PASSWORD")
    if phone and pwd:
        return phone, pwd
    path = os.environ.get("XT_ACCOUNT_FILE") or os.path.join(
        os.path.dirname(HERE), "账号信息.md")
    txt = open(os.path.normpath(path), encoding="utf-8").read()
    phone = re.search(r"账号（手机号）\s*\|\s*(\d+)", txt)
    pwd = re.search(r"密码\s*\|\s*(\S+)", txt)
    if not phone or not pwd:
        raise RuntimeError(
            "no account: set XT_PHONE/XT_PASSWORD or XT_ACCOUNT_FILE "
            "(markdown with '账号（手机号） | ...' and '密码 | ...' rows)")
    return phone.group(1), pwd.group(1)


_session = None


def new_session():
    s = requests.Session()
    s.trust_env = False  # chaoxing is domestic; a dead local proxy must not interfere
    s.headers.update({"User-Agent": UA})
    if os.path.exists(COOKIE_FILE):
        try:
            jar = json.load(open(COOKIE_FILE, encoding="utf-8"))
            for dom, cookies in jar.items():
                for k, v in cookies.items():
                    s.cookies.set(k, v, domain=dom)
        except Exception:
            pass
    return s


def save_cookies(s):
    jar = {}
    for c in s.cookies:
        jar.setdefault(c.domain, {})[c.name] = c.value
    json.dump(jar, open(COOKIE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def robust(s, method, url, tries=4, **kw):
    """mooc1-1.chaoxing.com often RSTs the first connection; retry wins."""
    last = None
    for i in range(tries):
        try:
            return getattr(s, method)(url, timeout=30, **kw)
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def logged_in(s) -> bool:
    try:
        r = robust(s, "get", "https://i.chaoxing.com/base")
        return "passport2.chaoxing.com" not in str(r.url)
    except Exception:
        return False


def ensure_session():
    global _session
    if _session is not None and logged_in(_session):
        return _session
    if _session is None:
        _session = new_session()
    if not logged_in(_session):
        phone, pwd = load_account()
        _session.get("https://passport2.chaoxing.com/login", timeout=20)
        r = _session.post("https://passport2.chaoxing.com/fanyalogin", data={
            "fid": "-1", "uname": aes_enc(phone), "password": aes_enc(pwd),
            "refer": "http%3A%2F%2Fi.mooc.chaoxing.com",
            "t": "true", "forbidotherlogin": "0", "validate": "",
            "doubleFactorLogin": "0", "independentId": "0", "independentNameId": "0",
        }, timeout=20)
        if r.json().get("status") is not True:
            raise RuntimeError("login failed: " + r.text[:150])
        save_cookies(_session)
    return _session


def load_enc_map():
    if os.path.exists(ENC_FILE):
        return json.load(open(ENC_FILE, encoding="utf-8"))
    return {}


def list_courses(s):
    r = robust(s, "post", "https://mooc1-1.chaoxing.com/visit/courselistdata",
               data={"courseType": "1", "courseFolderId": "0",
                     "courseFolderSize": "-1", "digest": "_all"})
    out = []
    for chunk in r.text.split('<li class="course ')[1:]:
        cid = re.search(r'courseId="(\d+)"', chunk)
        clz = re.search(r'clazzId="(\d+)"', chunk)
        pid = re.search(r'personId="(\d+)"', chunk)
        name = re.search(r'title="([^"]{2,60})"', chunk)
        if cid and clz:
            out.append({"courseId": cid.group(1), "clazzId": clz.group(1),
                        "cpi": pid.group(1) if pid else "",
                        "name": (name.group(1) if name else "?").strip()})
    return out


def parse_work_list(html):
    items = []
    for m in re.finditer(r'<li[^>]*data="([^"]*work/task[^"]*)"[^>]*aria-label="([^"]*)"(.*?)</li>',
                         html, re.S):
        url, label, body = m.group(1), m.group(2), m.group(3)
        title, _, status = label.partition(";")
        title = title.strip()
        status = status.strip() or "?"
        rem = re.search(r"(剩余[\d小时天分钟]+)", re.sub(r"<[^>]+>", "", body))
        wid = re.search(r"workId=(\d+)", url)
        aid = re.search(r"answerId=(\d+)", url)
        items.append({
            "title": title,
            "status": status,
            "remaining": rem.group(1) if rem else "",
            "workId": wid.group(1) if wid else "",
            "answerId": aid.group(1) if aid else "",
            "detail_url": url,
        })
    return items


def tool_login(_args):
    global _session
    _session = new_session()
    phone, pwd = load_account()
    _session.get("https://passport2.chaoxing.com/login", timeout=20)
    r = _session.post("https://passport2.chaoxing.com/fanyalogin", data={
        "fid": "-1", "uname": aes_enc(phone), "password": aes_enc(pwd),
        "refer": "http%3A%2F%2Fi.mooc.chaoxing.com",
        "t": "true", "forbidotherlogin": "0", "validate": "",
        "doubleFactorLogin": "0", "independentId": "0", "independentNameId": "0",
    }, timeout=20)
    if r.json().get("status") is True:
        save_cookies(_session)
        return [{"type": "text", "text": "login ok, cookies saved (%d)" % len(_session.cookies)}]
    return [{"type": "text", "text": "login FAILED: " + r.text[:150]}]


def tool_courses(_args):
    s = ensure_session()
    courses = list_courses(s)
    enc = load_enc_map()
    lines = [f"{len(courses)} courses:"]
    for c in courses:
        mark = " [enc]" if c["courseId"] in enc else ""
        lines.append(f"  {c['name']}  (courseId={c['courseId']}, classId={c['clazzId']}){mark}")
    return [{"type": "text", "text": "\n".join(lines)}]


def tool_homework(args):
    s = ensure_session()
    query = str(args.get("course", "")).strip()
    enc = load_enc_map()
    courses = list_courses(s)
    target = None
    for c in courses:
        if query and (query == c["courseId"] or query in c["name"]):
            target = c
            break
    if target is None:
        return [{"type": "text", "text": f"course '{query}' not found. Use xt_courses to list."}]
    e = enc.get(target["courseId"])
    if not e:
        return [{"type": "text", "text":
                 f"no cached enc for courseId {target['courseId']} ({target['name']}). "
                 "One-time setup: open the course 作业 tab in a browser, copy the "
                 "work/list URL's stuenc & enc params, then call xt_seed_enc."}]
    r = robust(s, "get",
               "https://mooc1.chaoxing.com/mooc2/work/list"
               f"?courseId={target['courseId']}&classId={target['clazzId']}&cpi={target['cpi']}&ut=s"
               f"&stuenc={e['stuenc']}&enc={e['work_enc']}")
    items = parse_work_list(r.text)
    lines = [f"{target['name']} — {len(items)} homework:"]
    for it in items:
        rem = f" ({it['remaining']})" if it["remaining"] else ""
        lines.append(f"  [{it['status']}] {it['title']}{rem}")
        lines.append(f"      {it['detail_url']}")
    return [{"type": "text", "text": "\n".join(lines) or "no homework entries"}]


def tool_homework_all(_args):
    s = ensure_session()
    enc = load_enc_map()
    courses = list_courses(s)
    lines = ["全课程作业扫描（仅限已缓存 enc 的课程）:"]
    found = 0
    for c in courses:
        e = enc.get(c["courseId"])
        if not e:
            continue
        try:
            r = robust(s, "get",
                       "https://mooc1.chaoxing.com/mooc2/work/list"
                       f"?courseId={c['courseId']}&classId={c['clazzId']}&cpi={c['cpi']}&ut=s"
                       f"&stuenc={e['stuenc']}&enc={e['work_enc']}")
            for it in parse_work_list(r.text):
                if it["status"] == "未交":
                    lines.append(f"  [未交] {c['name']}: {it['title']}")
                    found += 1
                else:
                    lines.append(f"  [{it['status']}] {c['name']}: {it['title']}")
        except Exception as ex:
            lines.append(f"  [error] {c['name']}: {str(ex)[:60]}")
    lines.append(f"—— 未交合计: {found}")
    return [{"type": "text", "text": "\n".join(lines)}]


def tool_seed_enc(args):
    enc = load_enc_map()
    cid = str(args.get("course_id", ""))
    enc[cid] = {"stuenc": args.get("stuenc", ""), "work_enc": args.get("work_enc", ""),
                "added": time.strftime("%Y-%m-%d")}
    json.dump(enc, open(ENC_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return [{"type": "text", "text": f"enc cached for courseId {cid}"}]


def tool_page_text(args):
    s = ensure_session()
    url = args.get("url", "")
    r = robust(s, "get", url)
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[ \t\r]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return [{"type": "text", "text": txt.strip()[:6000]}]


TOOLS = {
    "xt_login": {
        "description": "Login to Chaoxing (Xuexitong), persist cookies to disk. "
                       "Credentials come from XT_PHONE/XT_PASSWORD env vars or an "
                       "account markdown file. Other xt_* tools auto-login when the "
                       "session is dead, so calling this explicitly is rarely needed.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "xt_courses": {
        "description": "List all enrolled courses with courseId/classId; marks which "
                       "courses have cached enc (needed for xt_homework).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "xt_homework": {
        "description": "List homework entries (title/status/remaining time/detail URL) "
                       "for one course. Arg: course = course name substring or courseId. "
                       "Requires that course's enc to be seeded via xt_seed_enc "
                       "(one-time per course).",
        "inputSchema": {"type": "object",
                        "properties": {"course": {"type": "string"}},
                        "required": ["course"]},
    },
    "xt_homework_all": {
        "description": "Scan homework across every course that has cached enc; "
                       "highlights 未交 (unsubmitted) items.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "xt_seed_enc": {
        "description": "Cache a course's stuenc/work_enc pair (stable per course+student). "
                       "Get the values once from a browser: open the course 作业 tab, copy "
                       "them from the work/list URL query string.",
        "inputSchema": {"type": "object", "properties": {
            "course_id": {"type": "string"}, "stuenc": {"type": "string"},
            "work_enc": {"type": "string"}},
            "required": ["course_id", "stuenc", "work_enc"]},
    },
    "xt_page_text": {
        "description": "Fetch any chaoxing URL with the logged-in session and return "
                       "cleaned page text (for reading homework requirements etc.).",
        "inputSchema": {"type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"]},
    },
}


def handle(req):
    rid = req.get("id")
    method = req.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "chaoxing-mcp", "version": "1.0.0"}}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": n, "description": d["description"], "inputSchema": d["inputSchema"]}
            for n, d in TOOLS.items()]}}
    if method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {})
        try:
            if name == "xt_login":
                result = {"content": tool_login(args)}
            elif name == "xt_courses":
                result = {"content": tool_courses(args)}
            elif name == "xt_homework":
                result = {"content": tool_homework(args)}
            elif name == "xt_homework_all":
                result = {"content": tool_homework_all(args)}
            elif name == "xt_seed_enc":
                result = {"content": tool_seed_enc(args)}
            elif name == "xt_page_text":
                result = {"content": tool_page_text(args)}
            else:
                return {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32601, "message": "unknown tool %s" % name}}
        except Exception as e:
            result = {"content": [{"type": "text",
                                   "text": "[ERROR] %s: %s" % (type(e).__name__, e)}]}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": "method not found: %s" % method}}
    return None


def main():
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            resp = handle(json.loads(raw))
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": "%s: %s" % (type(e).__name__, e)}}
        if resp:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
