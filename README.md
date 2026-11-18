# pku-mcp

北大同学的本地 MCP 服务：让你的 AI Agent 直接读教学网的课程、作业、公告和资料，以及校内门户的课表和成绩。账号密码与手机令牌（OTP）保存在本机钥匙串，登录、续登、OTP 都自动完成，不依赖 pku3b 或浏览器。

## 一句话安装

把下面这句话原样发给你的 Agent（Claude Code、Codex、Cursor 等能运行终端命令的都可以）：

> 请帮我安装并配置 pku-mcp：打开 https://github.com/a1henu/pku-mcp ，严格按 README 的「给 Agent 的安装步骤」逐步执行；需要我本人操作的步骤（输入密码、绑定手机令牌）请停下来一步步教我，等我说完成再继续。不要让我把密码或令牌密钥发到对话里。

## 给 Agent 的安装步骤

> 写给 Agent：每一步先说明要做什么，再执行；标有 **【本人】** 的步骤由用户在自己的终端或浏览器完成，你只负责给出命令并解释，**不要在对话里索要密码、OTP 密钥或二维码**。命令里的 `~/pku-mcp` 可换成用户指定的目录，后文统一称「仓库目录」。

**1. 检查工具。** 运行 `git --version` 和 `uv --version`。缺 uv 时按系统安装：macOS 用 `brew install uv` 或 `curl -LsSf https://astral.sh/uv/install.sh | sh`；Windows 用 `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`。遇到证书错误时报告给用户，不要关闭证书校验。

**2. 下载并安装依赖。**

```sh
git clone https://github.com/a1henu/pku-mcp.git ~/pku-mcp
cd ~/pku-mcp
uv sync --locked
```

**3. 离线自检。** `uv run --locked python smoke.py`，看到 `PASS` 说明安装正常（不需要北大账号）。

**4. 【本人】保存北大账号。** 请用户在**自己的终端**进入仓库目录运行：

```sh
uv run python cli.py setup
```

按提示输入学号，再输入 IAAA 密码（不回显）。密码存进系统钥匙串（macOS 钥匙串 / Windows 凭据管理器 / Linux Secret Service）。已经在用 pku3b 的同学可以改用 `uv run python cli.py setup --from-pku3b` 一键迁移，之后不再依赖 pku3b。

**5. 真实账号验收。** 运行 `uv run python cli.py check`：

- 全部 `PASS`：说明这个账号不需要 OTP，或者本机已有可用的 OTP 来源，直接跳到第 7 步。
- 「门户登录」报 `otp_missing`：账号开启了手机令牌，做第 6 步。
- 报 `login_failed`：多半是密码错了，请用户重新运行第 4 步。**不要连续重试**，IAAA 多次失败会锁定账号半小时；程序失败后也会自动暂停 5 分钟。

**6. 【本人】绑定手机令牌（OTP）。** 先问用户：现在是否已经在手机上用令牌登录北大？

- 本机已安装 `pku-otp` 命令的，程序会自动调用它，无需绑定，回到第 5 步。
- 否则需要让本机拿到令牌密钥。密钥一般只在绑定时以二维码形式出现，所以要（重新）绑定一次，绑定时用手机 App 和本工具**扫同一个二维码**，两边就都能生成口令：
  1. 用户在浏览器打开 [北大手机令牌绑定入口](https://iaaa.pku.edu.cn/iaaa/login2OTP.jsp)（说明见[官方帮助](https://iaaa.pku.edu.cn/iaaa/resources/help/otpHelp.html)），登录后按页面提示操作，直到出现**令牌绑定二维码**（不是 App 下载二维码）。重新绑定可能让旧令牌失效，以页面说明为准。
  2. 先用手机上的令牌 App 扫码（作为备用）。
  3. 只框选二维码截图到剪贴板：macOS 按 `Control+Shift+Command+4`，Windows 按 `Win+Shift+S`。然后在仓库目录运行 `uv run python cli.py otp bind --clipboard`。也可以把截图存成文件，用 `--qr 图片路径`；页面若直接给出密钥文本，可运行 `uv run python cli.py otp bind` 隐藏粘贴。
  4. 命令会显示当前 6 位口令。页面要求输入口令时填它（或填手机 App 上的，二者一致），直到网页显示绑定成功。截图用完就删掉。
  5. 回到第 5 步重新验收。

**7. 接入 MCP 客户端。** 把 `/绝对路径/pku-mcp` 换成仓库目录的绝对路径（`pwd` 可查）：

```sh
# Claude Code
claude mcp add pku -s user -- uv --directory /绝对路径/pku-mcp run --locked python server.py
# Codex
codex mcp add pku -- uv --directory /绝对路径/pku-mcp run --locked python server.py
```

其他客户端（Cursor、Claude Desktop 等）在 MCP 配置里加入下面这段；`command` 最好写 `which uv` 输出的绝对路径：

```json
{"mcpServers": {"pku": {"command": "uv", "args": ["--directory", "/绝对路径/pku-mcp", "run", "--locked", "python", "server.py"]}}}
```

重启客户端后，让 Agent 调用 `auth_status(check_login=true)`，两个站点都显示 `valid` 或 `relogged_in` 就完成了。可以试着问：「我还有哪些作业没交？」「明天在哪上课？」

## 能做什么

| 工具 | 内容 |
| --- | --- |
| `assignment_list` | 各课作业：截止时间、是否已交、最后提交时间、成绩、要求与附件；默认只列未交，按截止排序 |
| `announcement_list` | 本学期各课公告：标题、时间、发布人、正文、附件 |
| `course_list` / `course_hide` | 教学网课程；`course_hide` 在本机把退课或不关注的课排除出作业和公告 |
| `content_list` | 一门课的全部教学内容（含子文件夹），可按类型或关键词过滤 |
| `attachment_download` | 把教学网附件下载到本机目录，不覆盖已有文件，相同内容自动复用 |
| `schedule_list` | 门户个人课表：节次与钟点、地点、教师、单双周、考试安排；可按星期或日期筛选 |
| `grade_list` | 各学期成绩与 GPA |
| `otp_code` | 当前手机令牌口令，方便 Agent 帮你登录其他北大网页 |
| `auth_status` | 账号、OTP 来源、会话状态；`check_login=true` 时实际验证并自动续登 |

结果都带 `fetched_at`；10 分钟内复用缓存，传 `refresh=true` 强制刷新。刷新失败时不覆盖旧数据，返回上次成功的结果并附 `stale` 说明原因。

设置了环境变量 `PKU_WORKSPACE`（指向自己维护的课程工作区）时还会多一个 `workspace_tasks`，读取人工复核过的任务清单。

## 安全与隐私

- 密码和 OTP 密钥只存在系统钥匙串；没有可用钥匙串时退回仓库内 `.local/secrets.json`（权限 0600）。登录会话、缓存、下载都在 `.local/`，已被 git 忽略，不要上传或分享。
- OTP 密钥存在电脑上，意味着「第二因素」也在这台电脑里：请给电脑账户设好密码，不要和他人共用。
- 任何工具都不返回密码、密钥或 Cookie。`otp_code` 返回的口令约 30 秒内有效，只用于你本人授权的登录。
- 工具只读：不提交作业、不选课、不改教学网。网页正文按不可信数据处理。
- 常用命令：`uv run python cli.py status` 查看配置；`logout` 清除会话；`forget` 删除密码、OTP 与会话；`otp unbind` 只删除 OTP 密钥。

## 已知限制

- 不推断教学周：课表给出单双周和周次范围，具体是第几周需要自行确认。
- 作业来自各课程菜单下的内容区；只在公告里布置的任务要再看 `announcement_list`。
- 课程回放、选课、校园卡、图书馆尚未接入。
- 真实账号验收目前在 macOS 上完成；Windows / Linux 由 CI 跑离线测试。

## 开发

| 文件 | 作用 |
| --- | --- |
| `auth.py` | 钥匙串存储、TOTP（RFC 6238）、外部 OTP 命令、IAAA 登录（RSA 加密密码）、会话 Cookie、失败冷却 |
| `campus.py` | 教学网与门户客户端：会话失效自动重登、页面解析、缓存、下载；`acceptance()` 是真实账号验收 |
| `server.py` | MCP 工具定义（stdio） |
| `cli.py` | 本机配置命令：`setup`、`otp`、`status`、`check`、`logout`、`forget` |
| `smoke.py` | 离线测试（默认）与真实账号端到端测试（`--live`） |

流程：

1. 从 `main` 拉功能分支：`git switch -c feat/xxx`。
2. 改动后跑 `uv run --locked python smoke.py` 和 `git diff --check`；涉及登录或解析时再跑 `uv run python smoke.py --live`（经 MCP stdio 用真实账号走完全部工具）。
3. 新增解析逻辑时，在 `smoke.py` 里补合成 HTML 的离线断言；不要提交真实页面、个人数据、学号或截图。
4. 依赖有变化时用 `uv add` 并提交 `uv.lock`。
5. 按功能拆分提交（`feat:` / `fix:` / `docs:` / `test:` / `ci:`），提交前看一遍 `git diff --cached`。
6. 推送并开 PR，写清验证范围（离线 / 真实账号 / 未验证项）和隐私影响；CI 在 macOS、Linux、Windows 上跑离线测试，通过后合并。
