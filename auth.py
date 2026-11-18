"""北大统一身份认证（IAAA）：本机凭据、动态口令与会话复用。

密码与 OTP 种子存系统钥匙串（无可用钥匙串时退回 0600 文件），Cookie 存 PKU_MCP_HOME/sessions。
本模块的任何返回值和异常信息都不包含密码、种子或 Cookie。
"""
import base64
import hmac
import json
import os
import random
import shutil
import struct
import subprocess
import threading
import time
from http.cookiejar import Cookie
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

HOME = Path(os.environ.get("PKU_MCP_HOME") or Path(__file__).resolve().parent / ".local").expanduser()
STATE = HOME / "auth-state.json"
SERVICE = "pku-mcp"
IAAA = "https://iaaa.pku.edu.cn/iaaa"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")
# IAAA 连续失败会锁定账号半小时（E21），失败后暂停自动登录。
COOLDOWN = {"E21": 1800}
DEFAULT_COOLDOWN = 300
PKU_OTP = ["pku-otp", "code", "--json", "--min-valid", "10"]
_login_lock = threading.Lock()


class AuthError(RuntimeError):
    def __init__(self, status: str, message: str = ""):
        super().__init__(f"{status}: {message}" if message else status)
        self.status = status
        self.message = message


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_private(path: Path, data) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(temp, path)


def config() -> dict:
    return read_json(HOME / "config.json", {})


def save_config(**changes) -> None:
    write_private(HOME / "config.json", {key: value for key, value in {**config(), **changes}.items()
                                         if value is not None})


# ---- 机密存储 -------------------------------------------------------------

def _keyring():
    if os.environ.get("PKU_MCP_SECRET_STORE") == "file":
        return None
    import keyring
    from keyring.backends import fail
    return None if isinstance(keyring.get_keyring(), fail.Keyring) else keyring


def secret_storage() -> str:
    store = _keyring()
    return f"system keyring ({store.get_keyring().name})" if store else f"file ({HOME / 'secrets.json'})"


def get_secret(name: str) -> str | None:
    store = _keyring()
    if store:
        return store.get_password(SERVICE, name)
    return read_json(HOME / "secrets.json", {}).get(name)


def set_secret(name: str, value: str) -> None:
    store = _keyring()
    if store:
        store.set_password(SERVICE, name, value)
    else:
        write_private(HOME / "secrets.json", {**read_json(HOME / "secrets.json", {}), name: value})


def delete_secret(name: str) -> None:
    store = _keyring()
    if store:
        from keyring.errors import PasswordDeleteError
        try:
            store.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass
    else:
        secrets = read_json(HOME / "secrets.json", {})
        secrets.pop(name, None)
        write_private(HOME / "secrets.json", secrets)


def credentials() -> tuple[str, str]:
    username = config().get("username")
    password = get_secret("password") if username else None
    if not password:
        raise AuthError("credentials_missing", "在终端运行 uv run python cli.py setup 保存学号和密码")
    return username, password


# ---- 动态口令 -------------------------------------------------------------

def _b32(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def parse_otp(text: str) -> dict:
    """解析 otpauth://totp URI 或 Base32 密钥，返回可保存的参数。"""
    text = text.strip()
    if text.startswith("otpauth://"):
        parts = urlsplit(text)
        query = parse_qs(parts.query)
        if parts.netloc != "totp" or len(query.get("secret", [])) != 1:
            raise ValueError("只支持含单个 secret 的 otpauth://totp 二维码")
        otp = {"secret": query["secret"][0], "digits": int(query.get("digits", ["6"])[0]),
               "period": int(query.get("period", ["30"])[0]),
               "algorithm": query.get("algorithm", ["SHA1"])[0].upper(),
               "label": unquote(parts.path.lstrip("/")), "issuer": query.get("issuer", [None])[0]}
    else:
        otp = {"secret": text, "digits": 6, "period": 30, "algorithm": "SHA1"}
    otp["secret"] = otp["secret"].replace(" ", "").upper().rstrip("=")
    if otp["algorithm"] not in {"SHA1", "SHA256", "SHA512"} or not 6 <= otp["digits"] <= 8 or otp["period"] <= 0:
        raise ValueError("不支持的 OTP 参数")
    try:
        if not _b32(otp["secret"]):
            raise ValueError
    except ValueError:
        raise ValueError("密钥不是有效的 Base32") from None
    return {key: value for key, value in otp.items() if value is not None}


def totp(otp: dict, at: float | None = None) -> str:
    """RFC 6238 TOTP。"""
    counter = int((time.time() if at is None else at) // otp["period"])
    digest = hmac.new(_b32(otp["secret"]), struct.pack(">Q", counter), otp["algorithm"].lower()).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** otp["digits"]).zfill(otp["digits"])


def otp_command() -> list[str] | None:
    if command := config().get("otp_command"):
        return command
    if "PKU_OTP_COMMAND" in os.environ:  # 设为空串可关闭自动探测
        return os.environ["PKU_OTP_COMMAND"].split() or None
    return PKU_OTP if shutil.which("pku-otp") else None


def otp_status() -> dict:
    stored = get_secret("otp")
    if stored:
        otp = json.loads(stored)
        return {"source": "builtin", "algorithm": otp["algorithm"], "digits": otp["digits"],
                "period": otp["period"], "label": otp.get("label")}
    command = otp_command()
    return {"source": "command", "command": command[0]} if command else {"source": "none"}


def current_otp(min_valid: int = 10) -> dict:
    """返回当前动态口令；剩余有效期不足 min_valid 秒时等下一周期。"""
    stored = get_secret("otp")
    if stored:
        otp = json.loads(stored)
        remaining = otp["period"] - time.time() % otp["period"]
        if remaining < min_valid:
            time.sleep(remaining + 0.2)
            remaining = otp["period"] - time.time() % otp["period"]
        return {"code": totp(otp), "remaining_seconds": int(remaining), "period": otp["period"], "source": "builtin"}
    command = otp_command()
    if not command:
        raise AuthError("otp_missing", "尚未配置 OTP：运行 uv run python cli.py otp bind")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AuthError("otp_command_failed", type(e).__name__) from None
    output = result.stdout.strip()
    if result.returncode:
        raise AuthError("otp_command_failed", f"{command[0]} 退出码 {result.returncode}")
    try:
        data = json.loads(output)
        code, remaining = data["code"], data.get("remaining_seconds")
        if isinstance(code, int):
            code = str(code).zfill(data.get("digits", 6))
    except (ValueError, TypeError, KeyError):
        code, remaining = output, None
    if not code.isdigit():
        raise AuthError("otp_command_failed", f"{command[0]} 输出不是数字口令")
    return {"code": code, "remaining_seconds": remaining, "period": 30, "source": "command"}


def _fresh_otp() -> str:
    """同一周期的口令只提交一次，避免两个子站连续登录时被判为重放。"""
    used = read_json(STATE, {}).get("otp_used_at", 0)
    otp = current_otp()
    if int(used // otp["period"]) == int(time.time() // otp["period"]):
        time.sleep(otp["period"] - time.time() % otp["period"] + 0.2)
        otp = current_otp()
    return otp["code"]


# ---- IAAA 登录与会话 -------------------------------------------------------

def _update_state(**changes) -> None:
    state = {**read_json(STATE, {}), **changes}
    write_private(STATE, {key: value for key, value in state.items() if value is not None})


def login_state() -> dict:
    state = read_json(STATE, {})
    failure = state.get("failure")
    result = {"last_login_at": state.get("last_login_at")}
    if failure:
        wait = failure["at"] + COOLDOWN.get(failure["code"], DEFAULT_COOLDOWN) - time.time()
        result["last_failure"] = {"code": failure["code"], "message": failure.get("message"),
                                  "cooldown_seconds": max(0, int(wait))}
    return result


def _encrypt(pem: str, password: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_public_key(pem.encode())
    return base64.b64encode(key.encrypt(password.encode(), padding.PKCS1v15())).decode()


def iaaa_token(http: httpx.Client, appid: str, redirect: str, force: bool = False) -> str:
    """用保存的学号、密码和自动生成的 OTP 换取子站登录 token。"""
    username, password = credentials()
    failure = login_state().get("last_failure")
    if failure and failure["cooldown_seconds"] and not force:
        raise AuthError("login_cooldown", f"上次登录失败（{failure['code']} {failure['message'] or ''}），"
                                          f"{failure['cooldown_seconds']} 秒内不自动重试，避免触发 IAAA 锁定")
    with _login_lock:
        mode = http.get(f"{IAAA}/isMobileAuthen.do", params={
            "appId": appid, "userName": username, "_rand": f"{random.random():.20f}"}).json()
        if mode.get("authenMode") not in {"OTP", "否", None}:
            raise AuthError("unsupported_second_factor", f"IAAA 要求 {mode.get('authenMode')} 验证，需本人在网页完成")
        need_otp = mode.get("authenMode") == "OTP"
        pem = http.get(f"{IAAA}/getPublicKey.do").json()["key"]
        for attempt in range(2):
            otp = _fresh_otp() if need_otp else ""
            response = http.post(f"{IAAA}/oauthlogin.do", data={
                "appid": appid, "userName": username, "password": _encrypt(pem, password),
                "randCode": "", "smsCode": "", "otpCode": otp, "redirUrl": redirect})
            if need_otp:
                _update_state(otp_used_at=time.time())
            data = response.json()
            if data.get("success") and data.get("token"):
                _update_state(failure=None, last_login_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                return data["token"]
            error = data.get("errors") or {}
            code = error.get("code", "unknown")
            if code == "E05" and attempt == 0:  # 口令恰好跨周期失效，换下一周期再试一次
                continue
            _update_state(failure={"at": time.time(), "code": code, "message": error.get("msg")})
            raise AuthError("login_failed", f"IAAA {code} {error.get('msg', '')}".strip())
    raise AssertionError("unreachable")


def _pku_only(request: httpx.Request) -> None:
    host = request.url.host
    if host != "pku.edu.cn" and not host.endswith(".pku.edu.cn"):
        raise AuthError("unexpected_host", f"拒绝访问非北大域名 {host}")


def session(name: str | None = None) -> httpx.Client:
    """新建只访问北大域名的客户端；给出 name 时载入该子站保存的 Cookie。"""
    http = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=45, follow_redirects=True,
                        event_hooks={"request": [_pku_only]})
    for c in read_json(HOME / "sessions" / f"{name}.json", []) if name else []:
        dot = c["domain"].startswith(".")
        http.cookies.jar.set_cookie(Cookie(0, c["name"], c["value"], None, False, c["domain"], dot, dot,
                                           c["path"], True, c["secure"], c["expires"], c["expires"] is None,
                                           None, None, {}))
    return http


def save_session(name: str, http: httpx.Client) -> None:
    write_private(HOME / "sessions" / f"{name}.json", [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "secure": c.secure,
         "expires": c.expires} for c in http.cookies.jar])


def clear_sessions() -> None:
    for path in (HOME / "sessions").glob("*.json"):
        path.unlink()


def status() -> dict:
    """本机配置概况：只报告是否已保存，不读出密码、种子或 Cookie。"""
    username = config().get("username")
    return {"username": username, "password_stored": bool(username and get_secret("password")),
            "secret_storage": secret_storage(), "otp": otp_status(),
            "saved_sessions": sorted(p.stem for p in (HOME / "sessions").glob("*.json")), **login_state()}
