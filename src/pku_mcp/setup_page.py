"""本机配置网页：在浏览器里填学号密码、粘贴手机令牌二维码、测试登录。

只监听 127.0.0.1 的随机端口，地址带随机 token，校验 Host/Origin，闲置 15 分钟自动关闭。
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
MAX_BODY = 8 << 20
BIND_URL = "https://iaaa.pku.edu.cn/iaaa/login2OTP.jsp"
HELP_URL = "https://iaaa.pku.edu.cn/iaaa/resources/help/otpHelp.html"

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>pku-mcp 配置</title>
<style>
body{font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:720px;margin:32px auto;padding:0 16px;color:#1f2328}
h1{font-size:22px}h2{font-size:17px;margin:0 0 8px}section{border:1px solid #d0d7de;border-radius:8px;padding:16px;margin:16px 0}
label{display:block;margin:8px 0 4px}input[type=text],input[type=password]{width:100%;box-sizing:border-box;padding:8px;font-size:15px}
button{margin-top:10px;padding:8px 16px;font-size:15px;cursor:pointer}ol{padding-left:20px}
#paste{border:2px dashed #8c959f;border-radius:8px;padding:20px;text-align:center;color:#57606a;margin:8px 0}
.ok{color:#1a7f37}.bad{color:#cf222e}.code{font:bold 28px monospace;letter-spacing:4px}.muted{color:#57606a;font-size:13px}
</style></head><body>
<h1>pku-mcp 本机配置</h1>
<p class="muted">本页只在你的电脑上运行（127.0.0.1）。密码和手机令牌密钥保存在 <code id="home"></code>，不会发给 AI 对话。</p>
<p id="summary"></p>

<section><h2>① 北大账号</h2>
<label>学号 / 职工号</label><input id="username" type="text" autocomplete="username">
<label>密码（登录门户、教学网用的那个）</label><input id="password" type="password" autocomplete="current-password">
<button onclick="saveAccount()">保存</button><p id="account-result"></p></section>

<section><h2>② 手机令牌（OTP）</h2>
<p id="otp-state"></p>
<p>没开手机令牌的同学跳过这一步。开了的话，门户、成绩等页面登录时要 6 位动态口令；把令牌的绑定二维码交给本工具，它就能像手机 App 一样自动生成口令：</p>
<ol>
<li>打开 <a href="BIND_URL" target="_blank">北大手机令牌绑定页</a>（<a href="HELP_URL" target="_blank">官方说明</a>），登录后按提示操作，直到出现<b>令牌绑定二维码</b>（不是 App 下载二维码）。重新绑定可能让旧令牌失效，以页面说明为准。</li>
<li>建议先用手机上的令牌 App 扫这个二维码，作为备用。</li>
<li>截图二维码：macOS 按 <kbd>Control+Shift+Command+4</kbd> 框选，Windows 按 <kbd>Win+Shift+S</kbd>。回到本页，按 <kbd>⌘V</kbd> / <kbd>Ctrl+V</kbd> 粘贴；也可以选择图片文件，或在下方粘贴密钥文本。</li>
<li>下面会显示 6 位口令。北大页面要求输入口令时填它，直到页面显示绑定成功。截图用完可以删掉。</li>
</ol>
<div id="paste">在此页面按 ⌘V / Ctrl+V 粘贴二维码截图</div>
<input id="file" type="file" accept="image/*">
<label>或粘贴 otpauth:// 链接 / Base32 密钥</label><input id="otp-text" type="password" autocomplete="off">
<button onclick="saveOtpText()">保存密钥</button>
<p id="otp-result"></p></section>

<section><h2>③ 测试登录</h2>
<p>会实际登录教学网和校内门户各一次。</p>
<button onclick="check()">测试登录</button><p id="check-result"></p></section>

<script>
const base = location.pathname.replace(/\\/$/, "");
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function api(path, body) {
  const r = await fetch(base + path, body === undefined ? {} : {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  return r.json();
}
function show(id, ok, text) { $(id).className = ok ? "ok" : "bad"; $(id).innerHTML = text; }
async function refresh() {
  const s = await api("/status");
  $("home").textContent = s.home;
  if (s.username) $("username").value = s.username;
  $("summary").innerHTML = `账号：${s.password_stored ? "已保存 " + esc(s.username) : "未保存"}；手机令牌：${s.otp.configured ? "已绑定" : "未绑定"}`;
  $("otp-state").innerHTML = s.otp.configured ? `<span class="ok">本机已有令牌密钥（${esc(s.otp.label || "未命名")}）。重新粘贴会替换它。</span>` : "本机还没有令牌密钥。";
}
async function saveAccount() {
  const r = await api("/account", {username: $("username").value, password: $("password").value});
  $("password").value = "";
  if (!r.ok) return show("account-result", false, esc(r.error));
  const need = r.otp_required === true ? "你的账号登录门户需要手机令牌，请完成第 ② 步。" :
               r.otp_required === false ? "你的账号不需要手机令牌，可以直接做第 ③ 步。" : "暂时查不到是否需要手机令牌。";
  show("account-result", true, "已保存。" + need); refresh();
}
async function sendOtp(body) {
  const r = await api("/otp", body);
  if (!r.ok) return show("otp-result", false, esc(r.error));
  show("otp-result", true, `已保存令牌密钥（${esc(r.label || "未命名")}）。当前口令 <span class="code">${esc(r.code)}</span>，还剩 ${r.remaining_seconds} 秒。`);
  refresh();
}
function saveOtpText() { sendOtp({text: $("otp-text").value}); $("otp-text").value = ""; }
function sendImage(blob) { const fr = new FileReader(); fr.onload = () => sendOtp({image: fr.result}); fr.readAsDataURL(blob); }
document.addEventListener("paste", e => {
  for (const item of e.clipboardData.items) if (item.type.startsWith("image/")) { e.preventDefault(); return sendImage(item.getAsFile()); }
});
$("file").addEventListener("change", e => e.target.files[0] && sendImage(e.target.files[0]));
async function check() {
  show("check-result", true, "正在登录……");
  const r = await api("/check", {});
  const names = {blackboard: "教学网", portal: "校内门户"};
  const lines = Object.entries(r.sites).map(([k, v]) => v.ok ? `<span class="ok">✓ ${names[k]}</span>` :
    `<span class="bad">✗ ${names[k]}：${esc(v.message || v.status)}</span>`);
  const done = Object.values(r.sites).every(v => v.ok);
  $("check-result").innerHTML = lines.join("<br>") +
    (done ? '<br><b class="ok">配置完成，可以关掉本页，回到 AI 对话了。</b>' : "");
}
refresh();
</script></body></html>
""".replace("BIND_URL", BIND_URL).replace("HELP_URL", HELP_URL)


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.token = secrets.token_urlsafe(24)
        self.last_active = time.time()
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
    if body.get("image"):
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
    while time.time() - server.last_active < IDLE_TIMEOUT:
        time.sleep(10)
    with _lock:
        if _server is server:
            _server = None
    server.shutdown()
    server.server_close()
