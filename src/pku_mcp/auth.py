"""北大统一身份认证（IAAA）：本机凭据、动态口令与会话复用。

所有状态都在 PKU_MCP_HOME（默认 ~/.pku-mcp）：config.json 存学号等设置，secrets.json 存密码和
OTP 种子（权限 0600），sessions/ 存各子站 Cookie。不依赖系统钥匙串或外部 OTP 程序。
本模块的返回值和异常信息都不包含密码、种子或 Cookie。
"""
import base64
import hmac
import json
import os
import random
import struct
import threading
import time
from http.cookiejar import Cookie
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

HOME = Path(os.environ.get("PKU_MCP_HOME") or Path.home() / ".pku-mcp").expanduser()
STATE = HOME / "auth-state.json"
IAAA = "https://iaaa.pku.edu.cn/iaaa"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36")
# IAAA 连续失败会锁定账号半小时（E21），失败后暂停自动登录。
COOLDOWN = {"E21": 1800}
DEFAULT_COOLDOWN = 300
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


# ---- 机密存储（本机文件，权限 0600） ----------------------------------------

SECRETS = HOME / "secrets.json"


def get_secret(name: str) -> str | None:
    return read_json(SECRETS, {}).get(name)


def set_secret(name: str, value: str | None) -> None:
    secrets = read_json(SECRETS, {})
    if value is None:
        secrets.pop(name, None)
    else:
        secrets[name] = value
    write_private(SECRETS, secrets)


def save_account(username: str, password: str) -> None:
    username, password = username.strip(), password.strip()
    if not username or not password:
        raise ValueError("学号和密码都不能为空")
    set_secret("password", password)
    save_config(username=username)
    clear_sessions()
    _update_state(failure=None)


def credentials() -> tuple[str, str]:
    username = config().get("username")
    password = get_secret("password") if username else None
    if not password:
        raise AuthError("credentials_missing", "还没有保存北大账号")
    return username, password


# ---- 动态口令 -------------------------------------------------------------

def _b32(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def parse_otp(text: str) -> dict:
    """解析 otpauth://totp URI、Base32 密钥，或含 secret 字段的 JSON（其他令牌工具的导出格式）。"""
    text = text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            otp = {"secret": str(data["secret"]), "digits": int(data.get("digits", 6)),
                   "period": int(data.get("period", 30)), "algorithm": str(data.get("algorithm", "SHA1")).upper(),
                   "label": data.get("label"), "issuer": data.get("issuer")}
        except (ValueError, KeyError, TypeError):
            raise ValueError("JSON 里没有可用的 secret 字段") from None
    elif text.startswith("otpauth://"):
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
    otp["secret"] = otp["secret"].replace(" ", "").replace("-", "").upper().rstrip("=")
    if otp["algorithm"] not in {"SHA1", "SHA256", "SHA512"} or not 6 <= otp["digits"] <= 8 or otp["period"] <= 0:
        raise ValueError("不支持的 OTP 参数")
    try:
        if not _b32(otp["secret"]):
            raise ValueError
    except ValueError:
        raise ValueError("密钥不是有效的 Base32") from None
    return {key: value for key, value in otp.items() if value is not None}


def read_qr(image) -> str:
    """从 PIL 图片里找出 otpauth:// 二维码。"""
    import zxingcpp
    for barcode in zxingcpp.read_barcodes(image):
        if barcode.text.startswith("otpauth://"):
            return barcode.text
    raise ValueError("图片里没有识别到 otpauth:// 二维码。请只截取北大页面上的手机令牌绑定二维码")


def save_otp(text: str) -> dict:
    otp = parse_otp(text)
    set_secret("otp", json.dumps(otp))
    return otp


def totp(otp: dict, at: float | None = None) -> str:
    """RFC 6238 TOTP。"""
    counter = int((time.time() if at is None else at) // otp["period"])
    digest = hmac.new(_b32(otp["secret"]), struct.pack(">Q", counter), otp["algorithm"].lower()).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** otp["digits"]).zfill(otp["digits"])


def otp_status() -> dict:
    stored = get_secret("otp")
    if not stored:
        return {"configured": False}
    otp = json.loads(stored)
    return {"configured": True, "algorithm": otp["algorithm"], "digits": otp["digits"],
            "period": otp["period"], "label": otp.get("label")}


def current_otp(min_valid: int = 10) -> dict:
    """返回当前动态口令；剩余有效期不足 min_valid 秒时等下一周期。"""
    stored = get_secret("otp")
    if not stored:
        raise AuthError("otp_missing", "账号开启了手机令牌，但本机还没有绑定 OTP 密钥")
    otp = json.loads(stored)
    remaining = otp["period"] - time.time() % otp["period"]
    if remaining < min_valid:
        time.sleep(remaining + 0.2)
        remaining = otp["period"] - time.time() % otp["period"]
    return {"code": totp(otp), "remaining_seconds": int(remaining), "period": otp["period"]}


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


def second_factor(http: httpx.Client, appid: str, username: str) -> str | None:
    """IAAA 对该账号在该子站要求的二次验证：OTP、否（不需要）或其他。"""
    return http.get(f"{IAAA}/isMobileAuthen.do", params={
        "appId": appid, "userName": username, "_rand": f"{random.random():.20f}"}).json().get("authenMode")


def iaaa_token(http: httpx.Client, appid: str, redirect: str, force: bool = False) -> str:
    """用保存的学号、密码和自动生成的 OTP 换取子站登录 token。"""
    username, password = credentials()
    failure = login_state().get("last_failure")
    if failure and failure["cooldown_seconds"] and not force:
        raise AuthError("login_cooldown", f"上次登录失败（{failure['code']} {failure['message'] or ''}），"
                                          f"{failure['cooldown_seconds']} 秒内不自动重试，避免触发 IAAA 锁定")
    with _login_lock:
        mode = second_factor(http, appid, username)
        if mode not in {"OTP", "否", None}:
            raise AuthError("unsupported_second_factor", f"IAAA 要求 {mode} 验证，需本人在网页完成")
        need_otp = mode == "OTP"
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
            "otp": otp_status(), "home": str(HOME),
            "saved_sessions": sorted(p.stem for p in (HOME / "sessions").glob("*.json")), **login_state()}
