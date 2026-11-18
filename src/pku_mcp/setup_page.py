"""本机配置网页：在浏览器里填学号密码、粘贴手机令牌二维码、测试登录。

只监听 127.0.0.1 的随机端口，地址带随机 token，校验 Host/Origin。页面开着时自动保活，
关掉页面 15 分钟后（最长 2 小时）自动关闭。
密码和 OTP 密钥只在本机进程内处理，不经过 Agent 对话。
"""
import base64
import binascii
import io
import json
import secrets
import socketserver
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth

IDLE_TIMEOUT = 900
MAX_LIFETIME = 7200
MAX_BODY = 8 << 20
BIND_URL = "https://iaaa.pku.edu.cn/iaaa/login2OTP.jsp"
HELP_URL = "https://iaaa.pku.edu.cn/iaaa/resources/help/otpHelp.html"

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>pku-mcp · 本机配置</title>
<style>
:root{--red:#9B1B20;--red-d:#7C1519;--bg:#F4F5F7;--card:#fff;--line:#E5E7EB;--text:#1F2328;--muted:#656D76;
--ok:#1A7F37;--ok-bg:#E8F5EC;--bad:#C4232D;--bad-bg:#FDEEEE;--soft:#F6F7F9}
@media (prefers-color-scheme:dark){:root{--bg:#0E1014;--card:#161A21;--line:#2A303B;--text:#E6E8EB;--muted:#9AA3AE;
--ok:#3FB950;--ok-bg:#13261A;--bad:#F47067;--bad-bg:#2B1416;--soft:#1C2029}}
*{box-sizing:border-box}
[hidden]{display:none!important}
.keys{white-space:nowrap}
body{margin:0;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;
font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
a{color:var(--red)}
@media (prefers-color-scheme:dark){a{color:#F47067}}
code,kbd,.mono{font-family:"SF Mono","JetBrains Mono",Menlo,Consolas,monospace}
header{background:linear-gradient(135deg,#A3191F 0%,#5F0A0F 100%);color:#fff;padding:44px 20px 96px}
header .wrap,main,footer{max-width:680px;margin:0 auto}
header .wrap{padding:0 16px}
.brand{font:700 30px/1.15 "SF Pro Display",Inter,"Segoe UI","Helvetica Neue",sans-serif;letter-spacing:-.5px}
header p{margin:10px 0 0;opacity:.88;font-size:16px}
.tag{display:inline-flex;align-items:center;gap:7px;margin-top:16px;padding:5px 13px;border-radius:999px;background:rgba(255,255,255,.14);font-size:13px}
main{margin-top:-60px;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 24px 24px;margin-bottom:16px;
box-shadow:0 1px 2px rgba(16,24,40,.04),0 10px 28px rgba(16,24,40,.07)}
.head{display:flex;align-items:center;gap:14px}
.num{flex:none;width:32px;height:32px;border-radius:50%;display:grid;place-items:center;font-weight:600;font-size:14px;
background:var(--soft);color:var(--muted);border:1px solid var(--line)}
.done .num{background:var(--ok);border-color:var(--ok);color:#fff}
.head h2{flex:1;margin:0;font-size:17px}
.pill{font-size:12px;padding:3px 10px;border-radius:999px;background:var(--soft);color:var(--muted);white-space:nowrap}
.pill.ok{background:var(--ok-bg);color:var(--ok)}
.desc{margin:6px 0 0 46px;color:var(--muted)}
.body{margin:16px 0 0 46px}
label{display:block;margin:14px 0 6px;font-size:13px;font-weight:600}
label:first-child{margin-top:0}
input[type=text],input[type=password]{width:100%;height:44px;padding:0 14px;font-size:15px;color:var(--text);background:var(--card);
border:1px solid var(--line);border-radius:10px;outline:none;transition:border-color .15s,box-shadow .15s}
input:focus{border-color:var(--red);box-shadow:0 0 0 3px rgba(155,27,32,.16)}
.row{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}
.btn{display:inline-flex;align-items:center;justify-content:center;height:42px;padding:0 18px;margin:0;border-radius:10px;font:600 15px/1 inherit;
font-family:inherit;cursor:pointer;color:var(--text);background:var(--card);border:1px solid var(--line);transition:background .15s,opacity .15s}
.btn:hover{background:var(--soft)}
.btn.primary{background:var(--red);border-color:var(--red);color:#fff}
.btn.primary:hover{background:var(--red-d)}
.btn[disabled]{opacity:.6;cursor:progress}
ol.steps{margin:0;padding:14px 16px 14px 34px;background:var(--soft);border-radius:12px}
ol.steps li{margin:3px 0;padding-left:2px}
kbd{display:inline-block;min-width:20px;padding:0 5px;font-size:12px;line-height:20px;text-align:center;background:var(--card);
border:1px solid var(--line);border-bottom-width:2px;border-radius:5px}
details{margin-top:16px}
summary{cursor:pointer;color:var(--muted);font-size:14px}
.msg{margin-top:14px;padding:11px 14px;border-radius:10px;font-size:14px}
.msg.ok{background:var(--ok-bg);color:var(--ok)}
.msg.bad{background:var(--bad-bg);color:var(--bad)}
.msg:empty{display:none}
.otp{display:flex;align-items:center;gap:18px;margin-top:14px;padding:16px 18px;border:1px solid var(--line);border-radius:12px}
.otp .code{font-size:32px;font-weight:700;letter-spacing:6px}
.otp .side{flex:1}
.otp small{color:var(--muted)}
.bar{height:6px;margin-top:6px;border-radius:3px;background:var(--soft);overflow:hidden}
.bar i{display:block;height:100%;background:var(--red);transition:width 1s linear}
.hint{margin:14px 0 0;color:var(--muted);font-size:13px}
.sites{list-style:none;margin:14px 0 0;padding:0}
.sites li{display:flex;align-items:center;gap:10px;padding:11px 2px;border-top:1px solid var(--line)}
.sites li:first-child{border-top:0}
.sites span{color:var(--muted);font-size:14px}
.dot{flex:none;width:10px;height:10px;border-radius:50%;background:var(--muted)}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
.banner{display:none;margin-bottom:16px;padding:14px 18px;border-radius:12px;background:var(--bad-bg);color:var(--bad);border:1px solid var(--line)}
.banner.show{display:block}
.finish{display:none;text-align:center;padding:30px 24px}
.finish.show{display:block}
.finish .check{width:48px;height:48px;margin:0 auto 10px;border-radius:50%;background:var(--ok);display:grid;place-items:center}
.finish h2{margin:0;font-size:19px}
.finish p{margin:8px 0 0;color:var(--muted)}
footer{padding:4px 16px 40px;color:var(--muted);font-size:13px;text-align:center}
@media (max-width:560px){.desc,.body{margin-left:0}.otp{flex-wrap:wrap}}
</style></head><body>
<header><div class="wrap">
  <div class="brand">pku-mcp</div>
  <p>三步完成配置，之后 AI 助手就能帮你查作业、看课表、整理课程资料。</p>
  <span class="tag"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>只在你的电脑上运行 · 密码不会发给 AI</span>
</div></header>
<main>
  <div id="closed" class="banner">配置页已经关闭了。请回到 AI 对话让它重新打开，或在终端运行 <code>pku-mcp setup</code>。</div>

  <section class="card" id="s1">
    <div class="head"><span class="num">1</span><h2>北大账号</h2><span class="pill" id="p1">未保存</span></div>
    <p class="desc">填登录门户、教学网用的学号和密码。</p>
    <div class="body">
      <label for="username">学号 / 职工号</label><input id="username" type="text" autocomplete="username" spellcheck="false">
      <label for="password">密码</label><input id="password" type="password" autocomplete="current-password">
      <div class="row"><button class="btn primary" id="b1" onclick="saveAccount()">保存</button></div>
      <div class="msg" id="m1"></div>
    </div>
  </section>

  <section class="card" id="s2">
    <div class="head"><span class="num">2</span><h2>手机令牌</h2><span class="pill" id="p2">未绑定</span></div>
    <p class="desc">没开手机令牌可以跳过。开了的话，把令牌的绑定二维码交给本工具，它就能像手机 App 一样自动生成 6 位口令。</p>
    <div class="body">
      <ol class="steps">
        <li>打开<a href="BIND_URL" target="_blank" rel="noopener">北大手机令牌绑定页</a>，登录后按提示操作，直到出现<b>令牌绑定二维码</b>（不是 App 下载码）。</li>
        <li>建议先用手机上的令牌 App 扫一下，作为备用。</li>
        <li>把二维码截图到剪贴板：macOS 按 <span class="keys"><kbd>⌃</kbd> <kbd>⇧</kbd> <kbd>⌘</kbd> <kbd>4</kbd></span>，Windows 按 <span class="keys"><kbd>Win</kbd> <kbd>⇧</kbd> <kbd>S</kbd></span>。</li>
        <li>点下面的按钮。页面会显示 6 位口令，北大页面要求输入时就填它，直到绑定成功。</li>
      </ol>
      <div class="row">
        <button class="btn primary" id="b2" onclick="sendOtp({clipboard: true}, this)">读取剪贴板里的二维码</button>
        <label class="btn" for="file">选择图片文件</label><input id="file" type="file" accept="image/*" hidden>
      </div>
      <details><summary>没有二维码？粘贴密钥文本</summary>
        <label for="otp-text">otpauth:// 链接或 Base32 密钥</label><input id="otp-text" type="password" autocomplete="off">
        <div class="row"><button class="btn" onclick="saveOtpText(this)">保存密钥</button></div>
      </details>
      <div class="msg" id="m2"></div>
      <div class="otp" id="otp" hidden>
        <span class="code mono" id="code">------</span>
        <div class="side"><small id="left">当前口令</small><div class="bar"><i id="bar"></i></div></div>
      </div>
      <p class="hint">重新绑定可能让旧令牌失效，以北大页面的说明为准（<a href="HELP_URL" target="_blank" rel="noopener">官方说明</a>）。截图用完可以删掉。</p>
    </div>
  </section>

  <section class="card" id="s3">
    <div class="head"><span class="num">3</span><h2>测试登录</h2><span class="pill" id="p3">未测试</span></div>
    <p class="desc">实际登录教学网和校内门户各一次，确认一切正常。</p>
    <div class="body">
      <div class="row"><button class="btn primary" id="b3" onclick="check()">测试登录</button></div>
      <ul class="sites" id="sites" hidden></ul>
    </div>
  </section>

  <section class="card finish" id="finish">
    <div class="check"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></div>
    <h2>配置完成</h2>
    <p>可以关掉本页，回到 AI 对话了。试试问它：「我这周还有哪些作业没交？」</p>
  </section>
</main>
<footer>账号数据保存在 <code id="home"></code>，只有你的系统账户能读。不用了运行 <code>pku-mcp forget</code> 一键删除。</footer>

<script>
const base = location.pathname.endsWith("/") ? location.pathname.slice(0, -1) : location.pathname;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
let ticker = null;

async function api(path, body) {
  try {
    const r = await fetch(base + path, body === undefined ? {} : {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    return await r.json();
  } catch (e) {
    $("closed").classList.add("show");
    return {ok: false, error: "配置页已经关闭，请重新打开"};
  }
}
async function busy(button, text, work) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = text;
  try { return await work(); } finally { button.disabled = false; button.textContent = label; }
}
function msg(id, ok, html) { $(id).className = "msg " + (ok ? "ok" : "bad"); $(id).innerHTML = html; }
function mark(step, done, text) {
  $("s" + step).classList.toggle("done", done);
  $("s" + step).querySelector(".num").textContent = done ? "✓" : step;
  $("p" + step).textContent = text;
  $("p" + step).className = "pill" + (done ? " ok" : "");
}
function showCode(r) {
  $("otp").hidden = false;
  $("code").textContent = r.code;
  let left = r.remaining_seconds;
  const draw = () => {
    $("left").textContent = `当前口令 · 还剩 ${left} 秒`;
    $("bar").style.width = (100 * left / r.period) + "%";
  };
  draw();
  clearInterval(ticker);
  ticker = setInterval(async () => {
    left -= 1;
    if (left > 0) return draw();
    clearInterval(ticker);
    const next = await api("/code");
    if (next.ok) showCode(next);
  }, 1000);
}
async function refresh() {
  const s = await api("/status");
  if (s.ok === false) return;
  $("home").textContent = s.home;
  if (s.username && !$("username").value) $("username").value = s.username;
  mark(1, s.password_stored, s.password_stored ? "已保存" : "未保存");
  if (s.otp.configured) {
    mark(2, true, "已绑定");
    const code = await api("/code");
    if (code.ok) showCode(code);
  }
}
async function saveAccount() {
  const r = await busy($("b1"), "保存中…", () => api("/account", {username: $("username").value, password: $("password").value}));
  $("password").value = "";
  if (!r.ok) return msg("m1", false, esc(r.error));
  const next = r.otp_required === true ? "你的账号登录门户需要手机令牌，请完成第 2 步。" :
               r.otp_required === false ? "你的账号不需要手机令牌，可以直接测试登录。" : "";
  msg("m1", true, "已保存。" + next);
  if (r.otp_required === false) mark(2, true, "不需要");
  refresh();
}
async function sendOtp(body, button) {
  const r = await busy(button || $("b2"), "读取中…", () => api("/otp", body));
  if (!r.ok) return msg("m2", false, esc(r.error));
  msg("m2", true, `已保存令牌密钥（${esc(r.label || "未命名")}）。北大绑定页要求输入口令时，填下面这个：`);
  mark(2, true, "已绑定");
  showCode(r);
}
function saveOtpText(button) { const text = $("otp-text").value; $("otp-text").value = ""; sendOtp({text}, button); }
function sendImage(blob) {
  const reader = new FileReader();
  reader.onload = () => sendOtp({image: reader.result});
  reader.readAsDataURL(blob);
}
document.addEventListener("paste", e => {
  for (const item of e.clipboardData.items) {
    if (item.type.startsWith("image/")) { e.preventDefault(); return sendImage(item.getAsFile()); }
  }
});
$("file").addEventListener("change", e => { if (e.target.files[0]) sendImage(e.target.files[0]); e.target.value = ""; });
async function check() {
  const r = await busy($("b3"), "登录中…门户可能要十几秒", () => api("/check", {}));
  const list = $("sites");
  list.hidden = false;
  if (!r.ok) { list.innerHTML = `<li><i class="dot bad"></i>${esc(r.error)}</li>`; return; }
  const names = {blackboard: "教学网", portal: "校内门户"};
  list.innerHTML = Object.entries(r.sites).map(([k, v]) => v.ok
    ? `<li><i class="dot ok"></i><b>${names[k]}</b><span>登录成功</span></li>`
    : `<li><i class="dot bad"></i><b>${names[k]}</b><span>${esc(v.message || v.status)}</span></li>`).join("");
  const done = Object.values(r.sites).every(v => v.ok);
  mark(3, done, done ? "通过" : "未通过");
  if (done) { $("finish").classList.add("show"); $("finish").scrollIntoView({behavior: "smooth", block: "center"}); }
}
setInterval(() => api("/status"), 60000);  // 页面开着就保活
refresh();
</script></body></html>
""".replace("BIND_URL", BIND_URL).replace("HELP_URL", HELP_URL)


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.token = secrets.token_urlsafe(24)
        self.started = self.last_active = time.time()
        self.completed = threading.Event()

    def server_bind(self):
        # HTTPServer 默认会对地址做反向 DNS（getfqdn），在部分 macOS 上要卡十几秒。
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/{self.token}"


class Handler(BaseHTTPRequestHandler):
    server: SetupServer

    def log_message(self, *args):  # 默认日志会把带 token 的地址写到 stderr
        pass

    def route(self) -> str | None:
        host = f"127.0.0.1:{self.server.server_address[1]}"
        origin = self.headers.get("Origin")
        prefix = f"/{self.server.token}"
        if self.headers.get("Host") != host or (origin and origin != f"http://{host}"):
            return None
        if self.path != prefix and not self.path.startswith(prefix + "/"):
            return None
        self.server.last_active = time.time()
        return self.path[len(prefix):] or "/"

    def reply(self, status: int, body, content_type: str = "application/json; charset=utf-8") -> None:
        data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.route()
        if path == "/":
            self.reply(200, PAGE, "text/html; charset=utf-8")
        elif path == "/status":
            self.reply(200, auth.status())
        elif path == "/code":
            try:
                self.reply(200, {"ok": True, **auth.current_otp(min_valid=0)})
            except auth.AuthError as e:
                self.reply(200, {"ok": False, "error": e.message})
        else:
            self.reply(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.route()
        length = int(self.headers.get("Content-Length") or 0)
        if path is None or length > MAX_BODY or self.headers.get_content_type() != "application/json":
            return self.reply(404, {"ok": False, "error": "not found"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if path == "/account":
                self.reply(200, account(body.get("username", ""), body.get("password", "")))
            elif path == "/otp":
                self.reply(200, otp(body))
            elif path == "/check":
                from . import campus
                sites = campus.check_login()
                if all(site["ok"] for site in sites.values()):
                    self.server.completed.set()
                self.reply(200, {"ok": True, "sites": sites})
            else:
                self.reply(404, {"ok": False, "error": "not found"})
        except (ValueError, auth.AuthError) as e:
            self.reply(200, {"ok": False, "error": str(e)})


def account(username: str, password: str) -> dict:
    auth.save_account(username, password)
    try:
        mode = auth.second_factor(auth.session(), "portalPublicQuery", username.strip())
        required = {"OTP": True, "否": False}.get(mode)
    except Exception:  # noqa: BLE001 - 查不到不影响保存
        required = None
    return {"ok": True, "otp_required": required}


def otp(body: dict) -> dict:
    if body.get("clipboard"):
        text = auth.read_qr(auth.clipboard_image())
    elif body.get("image"):
        from PIL import Image, UnidentifiedImageError
        try:
            image = Image.open(io.BytesIO(base64.b64decode(body["image"].split(",", 1)[-1])))
            image.load()
        except (UnidentifiedImageError, OSError, binascii.Error):
            raise ValueError("无法读取这张图片") from None
        text = auth.read_qr(image)
    else:
        text = body.get("text", "")
    saved = auth.save_otp(text)
    return {"ok": True, "label": saved.get("label"), **auth.current_otp(min_valid=0)}


_server: SetupServer | None = None
_lock = threading.Lock()


def start(open_browser: bool = True) -> SetupServer:
    """启动（或复用）配置页并尝试打开浏览器。"""
    global _server
    with _lock:
        if _server is None:
            _server = SetupServer()
            threading.Thread(target=_server.serve_forever, daemon=True).start()
            threading.Thread(target=_watchdog, args=(_server,), daemon=True).start()
        server = _server
    if open_browser:
        try:
            webbrowser.open(server.url)
        except webbrowser.Error:
            pass
    return server


def wait(server: SetupServer) -> bool:
    """阻塞到测试登录成功（True）或配置页闲置关闭（False）。"""
    while not server.completed.wait(1):
        if _server is not server:
            return False
    return True


def _watchdog(server: SetupServer) -> None:
    global _server
    while time.time() - server.last_active < IDLE_TIMEOUT and time.time() - server.started < MAX_LIFETIME:
        time.sleep(10)
    with _lock:
        if _server is server:
            _server = None
    server.shutdown()
    server.server_close()
