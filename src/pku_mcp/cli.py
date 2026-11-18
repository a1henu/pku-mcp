"""命令行入口 `pku-mcp`。

    pku-mcp serve                 启动 stdio MCP 服务（MCP 客户端调用；不带参数且不在终端里时同此）
    pku-mcp setup                 打开本机配置网页：学号密码、手机令牌、测试登录
    pku-mcp setup --terminal      改在终端里输入学号和密码（不回显）
    pku-mcp otp bind [--qr 图片 | --clipboard | --stdin] [--replace]
    pku-mcp otp code | otp status | otp unbind
    pku-mcp sync [--dest 目录]     整理本学期资料
    pku-mcp status | check [--force] | logout | forget
"""
import argparse
import getpass
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

from . import auth


def ask_secret(prompt: str, stdin: bool) -> str:
    if stdin or not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    return getpass.getpass(prompt).strip()


def pku3b_credentials() -> tuple[str, str]:
    """读取本机已有 pku3b 的明文配置（只迁移一次，之后不再依赖 pku3b）。"""
    candidates = [os.environ.get("PKU3B_CONFIG"),
                  Path.home() / "Library/Application Support/org.sshwy.pku3b/cfg.toml",
                  Path.home() / ".config/pku3b/cfg.toml",
                  Path(os.environ.get("APPDATA", "~")) / "sshwy/pku3b/config/cfg.toml"]
    path = next((Path(p).expanduser() for p in candidates if p and Path(p).expanduser().is_file()), None)
    if path is None:
        raise SystemExit("没找到 pku3b 配置；改用 pku-mcp setup。")
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    if not cfg.get("username") or not cfg.get("password"):
        raise SystemExit("pku3b 配置里没有明文学号和密码；改用 pku-mcp setup。")
    return cfg["username"], cfg["password"]


def setup(args) -> None:
    if args.from_pku3b or args.terminal or args.stdin:
        if args.from_pku3b:
            username, password = pku3b_credentials()
        else:
            username = args.username or input("学号/职工号: ").strip()
            password = ask_secret("IAAA 密码（输入不回显）: ", args.stdin)
        try:
            auth.save_account(username, password)
        except ValueError as e:
            raise SystemExit(str(e)) from None
        print(f"已保存 {username} 的账号到 {auth.SECRETS}。手机令牌：",
              "已绑定" if auth.otp_status()["configured"] else "未绑定（需要时运行 pku-mcp otp bind）")
        return
    from . import setup_page
    server = setup_page.start(open_browser=not args.no_browser)
    print(f"配置页：{server.url}\n浏览器没有自动打开的话，复制上面的地址打开。"
          "在页面里点「测试登录」成功后这里会自动退出（Ctrl+C 可提前退出）。")
    try:
        done = setup_page.wait(server)
    except KeyboardInterrupt:
        done = False
    print("配置完成。" if done else "配置页已关闭。", json.dumps(auth.status(), ensure_ascii=False))


def otp_bind(args) -> None:
    if auth.get_secret("otp") and not args.replace:
        raise SystemExit("已有 OTP 密钥；确认要替换时加 --replace。")
    from PIL import Image, ImageGrab, UnidentifiedImageError
    try:
        if args.qr:
            try:
                text = auth.read_qr(Image.open(Path(args.qr).expanduser()))
            except (OSError, UnidentifiedImageError):
                raise SystemExit(f"读不到图片 {args.qr}") from None
        elif args.clipboard:
            clip = ImageGrab.grabclipboard()
            if clip is None or isinstance(clip, list):
                raise SystemExit("剪贴板里没有图片。截取二维码后再试，或改用 pku-mcp setup 在网页里粘贴。")
            text = auth.read_qr(clip)
        else:
            text = ask_secret("粘贴 otpauth:// 链接或 Base32 密钥（输入不回显）: ", args.stdin)
        otp = auth.save_otp(text)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    code = auth.current_otp(min_valid=0)
    print(f"已保存 OTP（{otp.get('label') or '未命名'}，{otp['algorithm']}/{otp['digits']} 位/{otp['period']} 秒）。")
    print(f"当前口令 {code['code']}，剩 {code['remaining_seconds']} 秒。若北大绑定页要求输入口令，就填它。")


def mcp_command() -> list[str]:
    """MCP 客户端里应填写的启动命令（绝对路径）。"""
    exe = shutil.which("pku-mcp") or sys.argv[0]
    return [str(Path(exe).resolve()), "serve"]


def main() -> None:
    parser = argparse.ArgumentParser(prog="pku-mcp", description="北大教学网、课表、成绩的本地 MCP 服务")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="启动 stdio MCP 服务")
    p = sub.add_parser("setup", help="打开本机配置网页（或 --terminal 在终端输入）")
    p.add_argument("--terminal", action="store_true", help="在终端输入学号和密码")
    p.add_argument("--username")
    p.add_argument("--stdin", action="store_true", help="从标准输入读密码")
    p.add_argument("--from-pku3b", action="store_true", help="从本机 pku3b 明文配置迁移")
    p.add_argument("--no-browser", action="store_true", help="只打印配置页地址")
    otp = sub.add_parser("otp", help="手机令牌").add_subparsers(dest="action", required=True)
    p = otp.add_parser("bind", help="保存 OTP 密钥")
    p.add_argument("--qr")
    p.add_argument("--clipboard", action="store_true")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--replace", action="store_true")
    otp.add_parser("code")
    otp.add_parser("status")
    otp.add_parser("unbind")
    p = sub.add_parser("sync", help="整理本学期资料到本机")
    p.add_argument("--dest")
    p.add_argument("--max-file-mb", type=int, default=100)
    sub.add_parser("status", help="查看本机配置和 MCP 启动命令")
    p = sub.add_parser("check", help="用真实账号登录并读取数据")
    p.add_argument("--force", action="store_true", help="忽略失败冷却")
    sub.add_parser("logout", help="清除保存的登录会话")
    sub.add_parser("forget", help="删除保存的账号、OTP 和会话")
    args = parser.parse_args()

    if args.command is None and sys.stdin.isatty():
        parser.print_help()
    elif args.command in (None, "serve"):
        from .server import main as serve
        serve()
    elif args.command == "setup":
        setup(args)
    elif args.command == "otp" and args.action == "bind":
        otp_bind(args)
    elif args.command == "otp" and args.action == "code":
        print(auth.current_otp()["code"])
    elif args.command == "otp" and args.action == "status":
        print(json.dumps(auth.otp_status(), ensure_ascii=False))
    elif args.command == "otp" and args.action == "unbind":
        auth.set_secret("otp", None)
        print("已删除 OTP 密钥。")
    elif args.command == "sync":
        from .sync import sync
        while True:
            report = sync(args.dest, max_file_mb=args.max_file_mb, time_budget=600)
            print(f"新下载 {report['downloaded_count']} 个文件，跳过大文件 {len(report['skipped_large'])} 个，"
                  f"失败 {len(report['failed'])} 个")
            if report["done"]:
                break
        print(f"学期资料在 {report['root']}")
    elif args.command == "status":
        print(json.dumps({**auth.status(), "mcp_command": mcp_command()}, ensure_ascii=False, indent=2))
    elif args.command == "check":
        from .campus import acceptance
        sys.exit(acceptance(force=args.force))
    elif args.command == "logout":
        auth.clear_sessions()
        print("已清除登录会话。")
    elif args.command == "forget":
        auth.set_secret("password", None)
        auth.set_secret("otp", None)
        auth.clear_sessions()
        auth.save_config(username=None)
        print("已删除账号、OTP 和会话。")


def run() -> None:
    try:
        main()
    except auth.AuthError as e:
        raise SystemExit(f"认证失败：{e}") from None
