# pku-mcp

面向北大学生的本地优先助手。首版提供本地 stdio MCP：`due_list`、`schedule_list`、`auth_status`。目前读取现有课程工作区的已审核待办和课表摘录，不直接登录或发起写操作。

## 一句话交给你的 Agent

> 请从 GitHub 的 `a1henu/pku-mcp`（如果还不可见，请用我提供的本地 checkout）克隆或打开项目，读 README 和 AGENTS.md，按「安装与验收」安装并跑 smoke，再根据我的 MCP 客户端配置演示；如我需要接入本人账号，请按「北大账号与手机令牌」每次只教我一步，等我自行完成再继续，绝不要索取密码、口令、二维码或 Cookie，也不要提交作业。

## 安装与验收

1. 准备 Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。Agent 应先检查 `git --version`、`uv --version`，缺工具时说明安装动作，不关闭证书校验。
2. 进入克隆目录，运行 `uv sync --locked` 和 `uv run --locked python smoke.py`。看到 `PASS` 代表真实 stdio 客户端已完成握手并调用三个工具；不需要北大账号。
3. 运行 `uv run --locked python smoke.py --demo .local/demo`，生成合成演示数据。若目录已有快照，换新目录，不覆盖。
4. 按你的客户端说明添加本地 stdio 服务：命令填 **uv 的绝对路径**，参数为 `--directory`、**仓库绝对路径**、`run`、`--locked`、`python`、`server.py`，环境变量 `PKU_WORKSPACE` 填 **`.local/demo` 的绝对路径**。重载客户端，请 Agent 调用 `due_list`；应看到「示例作业」。在普通 shell 中也可运行 `PKU_WORKSPACE="$PWD/.local/demo" uv run --locked python server.py`；服务等候 stdio 消息属于正常现象。

### 北大账号与手机令牌

1. 先问本人是否已绑定；已绑定就不要重新绑定。未绑定时，由本人在浏览器打开[北大统一身份认证手机令牌说明](https://iaaa.pku.edu.cn/iaaa/resources/help/otpHelp.html)，按官方页面指引登录并用页面支持的手机 App 完成绑定。二维码、密钥、验证码只在本人设备上处理，不截图发给 Agent。
2. 不需要安装 `pku3b`。在本机图形桌面的终端运行 `uv run --locked --extra campus playwright install chromium`，再运行 `uv run --locked --extra campus python campus.py`。在弹出的教学网浏览器里自行登录，需要时输入 OTP；看到课程首页后回终端按 Enter。专用浏览器会话保存在本机 `.local/campus-browser/`，不要上传或分享。
3. 看到 `refreshed` 后，用 `--extra campus` 启动 MCP，并调用 `course_list(refresh=true)`。有效会话可在后台复用，不弹窗、不等输入。若返回 `reauth_required`，旧快照仍可读，需在本机重新运行 `campus.py` 登录。该工具无需 `PKU_WORKSPACE`；待办和课表仍读兼容工作区或演示快照。

在 MCP 客户端配置相同命令和环境变量。课表是历史摘录，不按自然日期推断教学周；新的来源快照也不等于待办已经人工复核。

## 从现有工作流出发

| 日常需求 | 现有做法 | 拟做工具 |
| --- | --- | --- |
| 今天在哪上课 | `pku3b coursetable --force --raw`；按学期、班号和周次核对 | `schedule_list`：按日期查节次、地点、单双周；之后支持本地 ICS 导出 |
| 哪些作业快到期 | `scripts/sync_courses.py` 合并正式作业、公告、目录和普通内容；人工核对后维护任务 JSON、DDL 表 | `due_list`：返回要求、来源、更新时间和未覆盖范围；`sync_preview`：列新增、冲突和变更，不覆盖个人进度 |
| 找题目和资料 | 从内容页下载附件，按条目保存，SHA-256 去重并保留旧版本 | `attachment_list` / `attachment_download`：限定下载目录，返回本地路径和摘要 |
| 交作业 | 核对课程、入口、截止和文件，提交后回读平台时间与附件名 | 先做 `submission_status`，再做 `submission_prepare`；真提交另行设计本机确认，绑定目标和文件摘要；超时先查回执，绝不盲重试 |
| 补看课程 | 本机 `pku3b video` 提供列表和下载子命令；未实测当前账号可见课程 | `replay_list` 先于下载；仅下载本人有权访问且允许下载的回放，不公开分发 |
| OTP 绑定与登录 | 官方北大 App 或 FreeOTP 由本人绑定；当前工作区另用本机 helper，最近一次刷新因钥匙串失败 | MCP 只报认证状态和修复指引；绑定、换机和输入口令留在官方入口或本机交互，不把口令暴露成工具 |

工作流来源：本机 `courses/yr4` 的 `AGENTS.md`、`DDL.md`、`.workspace/tasks.json`、`scripts/sync_courses.py` 与 `pku3b --help`。截至 2026-11-18 本次梳理未刷新个人数据；原工作区记录的最近全量成功是 11 月 12 日，11 月 17 日刷新失败。

## 门户与校园服务

本次只核对公开资料，没有进入账号登录后的服务菜单。以下是候选，不代表已验证当前账号权限，也不把各子站当成门户同一套接口。

| 范围 | 适合 MCP 化 | 保留给官网 |
| --- | --- | --- |
| 教务 | 课表、考试地点和时间、校历；成绩按需单独查询 | 选退课、成绩单申请；考试与成绩入口待登录后核对 |
| 校园卡 | 可考虑仅返回余额和低余额提示，默认不读消费流水 | 充值、挂失、解挂 |
| 图书馆 | 优先馆藏检索、借阅到期和预约规则；个人借阅接口待验证 | 真实预约和续借先跳转官网，不抢座、不代签到 |
| 校园网和账号 | 服务状态、常用入口、OTP 修复指引 | 密码更改、令牌绑定与恢复 |

官方来源：[校内信息门户](https://portal.pku.edu.cn/portal2017/)、[计算中心](https://cc.pku.edu.cn/)、[校园卡服务大厅](https://card.pku.edu.cn/)、[图书馆研修座位](https://lib.pku.edu.cn/3wxbz/39kjzwyy/392yxzw/index.htm)、[手机令牌说明](https://iaaa.pku.edu.cn/iaaa/resources/help/otpHelp.html)。研修专座有资格限制，不等于本科生通用座位入口。

## 开发顺序

1. 用脱敏模拟数据实现本地 stdio 服务，首批只做 `due_list`、`schedule_list` 和 `auth_status`，不需要北大账号即可测试。
2. 接入只读 `pku3b` adapter 与可选的课程内容来源。返回 `verified_at`、`freshness`、`coverage`，让“没有发现”与“根本没查到”可区分。
3. 加附件、回放列表和下载。通过只读提交状态验证回执解析，再决定是否加入提交能力。
4. 补门户服务目录：逐项记录账号角色、页面入口、允许的访问方式和读写边界，再按使用频率接入。

首版验收：登录失败仍能显示上次结果并标明时间；能区分退课与在修、同名不同班；日期级截止不补钟点；能保留来源冲突和“本地完成／已提交／已评分”的区别。

## 开发与提交

从 `main` 建功能分支，改动用合成样例跑 `uv sync --locked`、`uv run --locked python smoke.py` 和 `git diff --check`。依赖有变化时提交 `uv.lock`。提交前检查 `git diff --cached`，只暂存代码、文档和非个人测试数据；不要提交 `.local/`、下载、真实快照或凭据。按功能拆分提交，PR 写清验证范围、未验证项和隐私影响。
