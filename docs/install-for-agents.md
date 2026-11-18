# pku-mcp 安装指南（写给 AI Agent）

你正在帮一位北大同学安装 pku-mcp。它是一个运行在用户电脑上的 MCP 服务，装好后你就能直接读取他的教学网作业、公告、课程资料，以及校内门户的课表和成绩，还能把整学期的资料整理到本机文件夹。

请按下面的步骤做。原则：

- **用户可能完全不懂命令行。** 每一步先用一句大白话告诉用户你要做什么、为什么，再执行；出错时先自己排查，别把报错原样丢给用户。
- **绝不在对话里索要密码、手机令牌密钥或二维码。** 账号只在第 3 步打开的本机网页里由用户本人填写。
- 能自己执行的命令就自己执行；只有填网页、截图这类必须本人做的事才请用户动手，并等用户说「好了」再继续。
- 不要关闭 TLS 证书校验，不要用 `sudo` 安装。

开始前，先跟用户说明大概流程（约 5 分钟）：装一个叫 uv 的小工具 → 装 pku-mcp → 在浏览器里填一次学号密码 → 接到你正在用的 AI 应用里。

## 第 1 步：准备 uv

uv 负责下载合适的 Python 和依赖，用户电脑上不需要预先装 Python 或 Git。

先运行 `uv --version`。已有输出就跳到第 2 步；没有的话按系统安装：

- macOS / Linux：

  ```sh
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- Windows（PowerShell）：

  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

装完后当前终端可能还找不到 `uv`。这时用安装脚本提示的完整路径（通常是 `~/.local/bin/uv`，Windows 为 `%USERPROFILE%\.local\bin\uv.exe`）继续，或者新开一个终端。

如果下载 Python 包很慢（校园网或国内网络常见），可以设置清华镜像后再执行第 2 步：

```sh
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple        # macOS / Linux
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"       # Windows PowerShell
```

## 第 2 步：安装 pku-mcp

```sh
uv tool install --python 3.12 https://github.com/a1henu/pku-mcp/archive/refs/heads/main.zip
```

然后运行 `pku-mcp status`。如果提示找不到命令，运行 `uv tool update-shell` 后新开终端，或直接用完整路径 `~/.local/bin/pku-mcp`（Windows：`%USERPROFILE%\.local\bin\pku-mcp.exe`）。

`pku-mcp status` 输出里的 `mcp_command` 是第 4 步要用的启动命令，记下来。

## 第 3 步：让用户填写账号（用户本人操作）

运行：

```sh
pku-mcp setup
```

它会在用户的浏览器里打开一个本机网页（地址以 `http://127.0.0.1` 开头），并一直等到用户在网页上「测试登录」成功，最长 15 分钟。如果你的命令执行有超时限制，请放到后台运行，或用较长的超时。浏览器没有自动打开时，把命令输出里的地址发给用户。

告诉用户网页上要做的三件事：

1. **填学号和密码**，点「保存」。密码只存在用户自己的电脑上，不会经过 AI。保存后页面会显示这个账号需不需要手机令牌。
2. **手机令牌（如果需要）**。北大门户、成绩等页面登录时要输入手机令牌上的 6 位动态口令。为了让 pku-mcp 自动生成口令，需要把令牌的「绑定二维码」交给它，页面上有详细步骤。可以这样跟用户解释：
   - 打开网页里的「北大手机令牌绑定页」链接，登录后按提示操作，直到出现绑定二维码（不是 App 下载二维码）。
   - 建议先用手机上的令牌 App 扫这个码，手机和电脑就都能生成口令。重新绑定可能让旧令牌失效，以北大页面说明为准。
   - 截图二维码（macOS：Control+Shift+Command+4；Windows：Win+Shift+S），回到 pku-mcp 网页按 ⌘V / Ctrl+V 粘贴。
   - 网页会显示 6 位口令；北大页面要求输入口令时填它，直到北大页面显示绑定成功。
   - 不需要手机令牌的账号跳过这一步。
3. **点「测试登录」**。教学网和校内门户都显示 ✓ 就完成了，`pku-mcp setup` 命令会自动结束。

测试失败时按网页提示处理：密码错就重新保存账号；口令错就重新粘贴二维码。**不要让用户连续反复测试**，北大统一认证连续失败会锁定账号半小时，pku-mcp 失败后也会自动暂停 5 分钟。

完成后运行 `pku-mcp status`，确认 `password_stored` 为 `true`。

## 第 4 步：接入 AI 应用

把第 2 步记下的 `mcp_command`（形如 `/Users/xxx/.local/bin/pku-mcp serve`）填进用户正在用的应用：

- **Claude Code**

  ```sh
  claude mcp add pku -s user -- /完整路径/pku-mcp serve
  ```

- **Codex**

  ```sh
  codex mcp add pku -- /完整路径/pku-mcp serve
  ```

- **Claude Desktop、Cursor 等用 JSON 配置的应用**：在 MCP 配置文件里加入下面这段（已有 `mcpServers` 就只加 `pku` 这一项）。Claude Desktop 的配置文件在 macOS 是 `~/Library/Application Support/Claude/claude_desktop_config.json`，Windows 是 `%APPDATA%\Claude\claude_desktop_config.json`；Cursor 是 `~/.cursor/mcp.json`。

  ```json
  {"mcpServers": {"pku": {"command": "/完整路径/pku-mcp", "args": ["serve"]}}}
  ```

  Windows 的路径要写成 `C:\\Users\\xxx\\.local\\bin\\pku-mcp.exe`（反斜杠写两遍）。

## 第 5 步：重启并试用

请用户重启 AI 应用（Claude Code / Codex 开一个新会话即可）。之后调用 `auth_status(check_login=true)`，两个站点都是 `valid` 或 `relogged_in` 就装好了。

最后告诉用户可以直接这样说：

- 「开学了，帮我把这学期的课程资料整理好」（会用 `semester_sync` 下载并按课程归档，默认放在 `~/PKU/<学期名>`）
- 「我这周还有哪些作业没交？」
- 「明天几点在哪上课？」
- 「帮我把计算机网络 lab2 的附件下下来，一起做」

有些应用会把 pku-mcp 自带的三个流程显示成斜杠命令：开学初始化（`init_semester`）、本周要做什么（`weekly_plan`）、帮我写作业（`homework`）。

## 以后

- 更新：运行 `uv tool install --reinstall --refresh --python 3.12 https://github.com/a1henu/pku-mcp/archive/refs/heads/main.zip`（`uv tool upgrade` 对这种安装方式不生效）。Windows 上先让用户退出 AI 应用，否则文件被占用无法替换。更新后重启 AI 应用。
- 密码改了、令牌换了：`pku-mcp setup`，或在对话里让 AI 调用 `auth_setup`
- 卸载：先 `pku-mcp forget` 删除保存的账号，再 `uv tool uninstall pku-mcp`
