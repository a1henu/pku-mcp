"""本机配置入口：保存凭据、绑定 OTP、查看状态、真实账号验收。

    uv run python cli.py setup [--username 学号] [--from-pku3b]
    uv run python cli.py otp bind [--qr 图片 | --clipboard | --stdin] [--replace]
    uv run python cli.py otp code | otp status | otp unbind
    uv run python cli.py status
    uv run python cli.py check [--force]
    uv run python cli.py logout | forget
"""
import argparse
import getpass
import json
import os
import sys
import tomllib
from pathlib import Path

import auth


def ask_secret(prompt: str, stdin: bool) -> str:
    if stdin or not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    return getpass.getpass(prompt).strip()


def pku3b_credentials() -> tuple[str, str]:
    """读取本机已有 pku3b 配置（只迁移一次，之后不再依赖 pku3b）。"""
    candidates = [os.environ.get("PKU3B_CONFIG"),
                  Path.home() / "Library/Application Support/org.sshwy.pku3b/cfg.toml",
                  Path.home() / ".config/pku3b/cfg.toml",
                  Path(os.environ.get("APPDATA", "~")) / "sshwy/pku3b/config/cfg.toml"]
    path = next((Path(p).expanduser() for p in candidates if p and Path(p).expanduser().is_file()), None)
    if path is None:
        raise SystemExit("没找到 pku3b 配置；改用 uv run python cli.py setup 手动输入。")
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    password = cfg.get("password", "")
    if cfg.get("secret_backend") == "keyring":
        import keyring
        password = keyring.get_password("org.sshwy.pku3b", f"config:{path.resolve()}:password") or ""
    if not cfg.get("username") or not password:
        raise SystemExit("pku3b 配置里缺少学号或密码。")
    return cfg["username"], password


def setup(args) -> None:
    if args.from_pku3b:
        username, password = pku3b_credentials()
    else:
        username = args.username or input("学号/职工号: ").strip()
        password = ask_secret("IAAA 密码（输入不回显）: ", args.stdin)
    if not username or not password:
        raise SystemExit("学号和密码都不能为空。")
    auth.set_secret("password", password)
    auth.save_config(username=username)
    auth.clear_sessions()
    print(f"已保存 {username} 的密码到 {auth.secret_storage()}。")
    print("OTP:", json.dumps(auth.otp_status(), ensure_ascii=False))


def read_qr(image) -> str:
    import zxingcpp
    for barcode in zxingcpp.read_barcodes(image):
        if barcode.text.startswith("otpauth://"):
            return barcode.text
    raise SystemExit("图片里没有识别到 otpauth:// 二维码。请只截取北大页面上的 OTP 绑定二维码。")


def otp_bind(args) -> None:
    if auth.get_secret("otp") and not args.replace:
        raise SystemExit("已有内置 OTP 绑定；确认要替换时加 --replace。")
    if args.qr:
        from PIL import Image, UnidentifiedImageError
        try:
            text = read_qr(Image.open(Path(args.qr).expanduser()))
        except (OSError, UnidentifiedImageError):
            raise SystemExit(f"读不到图片 {args.qr}") from None
    elif args.clipboard:
        from PIL import ImageGrab
        clip = ImageGrab.grabclipboard()
        if clip is None or isinstance(clip, list):
            raise SystemExit("剪贴板里没有图片。macOS 用 Control+Shift+Command+4 框选二维码后再试。")
        text = clip if isinstance(clip, str) else read_qr(clip)
    else:
        text = ask_secret("粘贴 otpauth:// URI 或 Base32 密钥（输入不回显）: ", args.stdin)
    try:
        otp = auth.parse_otp(text)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    auth.set_secret("otp", json.dumps(otp))
    code = auth.current_otp(min_valid=0)
    print(f"已保存 OTP（{otp.get('label') or '未命名'}，{otp['algorithm']}/{otp['digits']} 位/{otp['period']} 秒）。")
    print(f"当前口令 {code['code']}，剩 {code['remaining_seconds']} 秒。若北大绑定页要求输入口令，就填它。")


def main() -> None:
    parser = argparse.ArgumentParser(description="pku-mcp 本机配置")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("setup", help="保存学号和密码到系统钥匙串")
    p.add_argument("--username")
    p.add_argument("--stdin", action="store_true", help="从标准输入读密码")
    p.add_argument("--from-pku3b", action="store_true", help="从本机 pku3b 配置迁移")
    otp = sub.add_parser("otp", help="动态口令").add_subparsers(dest="action", required=True)
    p = otp.add_parser("bind", help="保存 OTP 密钥")
    p.add_argument("--qr")
    p.add_argument("--clipboard", action="store_true")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--replace", action="store_true")
    otp.add_parser("code")
    otp.add_parser("status")
    otp.add_parser("unbind")
    sub.add_parser("status")
    p = sub.add_parser("check", help="用真实账号登录并读取数据")
    p.add_argument("--force", action="store_true", help="忽略失败冷却并重新登录")
    sub.add_parser("logout", help="清除保存的登录会话")
    sub.add_parser("forget", help="删除保存的密码、OTP 和会话")
    args = parser.parse_args()

    if args.command == "setup":
        setup(args)
    elif args.command == "otp" and args.action == "bind":
        otp_bind(args)
    elif args.command == "otp" and args.action == "code":
        print(auth.current_otp()["code"])
    elif args.command == "otp" and args.action == "status":
        print(json.dumps(auth.otp_status(), ensure_ascii=False))
    elif args.command == "otp" and args.action == "unbind":
        auth.delete_secret("otp")
        print("已删除内置 OTP 密钥。", json.dumps(auth.otp_status(), ensure_ascii=False))
    elif args.command == "status":
        print(json.dumps(auth.status(), ensure_ascii=False, indent=2))
    elif args.command == "check":
        from campus import acceptance
        sys.exit(acceptance(force=args.force))
    elif args.command == "logout":
        auth.clear_sessions()
        print("已清除登录会话。")
    elif args.command == "forget":
        for name in ("password", "otp"):
            auth.delete_secret(name)
        auth.clear_sessions()
        auth.save_config(username=None)
        print("已删除密码、OTP 和会话。")


if __name__ == "__main__":
    try:
        main()
    except auth.AuthError as e:
        raise SystemExit(f"认证失败：{e}") from None
