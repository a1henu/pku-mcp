# 参与开发

## 目录

```
pku-mcp/
├── src/pku_mcp/
│   ├── auth.py        账号与密钥存储、TOTP、IAAA 登录（RSA 加密密码）、Cookie、失败冷却
│   ├── campus.py      教学网与门户：会话失效自动重登、页面解析、缓存、下载
│   ├── sync.py        学期资料整理（semester_sync）
│   ├── setup_page.py  本机配置网页（127.0.0.1）
│   ├── server.py      MCP 工具与预设流程（prompts）
│   └── cli.py         命令行 pku-mcp
├── tests/
│   ├── test_offline.py  离线测试（CI 跑这个）
│   └── test_live.py     真实账号端到端测试
├── docs/              用户与 Agent 文档
└── assets/            README 题图
```

## 本地运行

```sh
git clone https://github.com/a1henu/pku-mcp.git && cd pku-mcp
uv sync                       # 含开发依赖 pytest
uv run pku-mcp --help
uv run pytest                 # 离线测试，不需要北大账号
```

用开发中的代码接入 MCP 客户端：命令填 `uv`，参数 `--directory /仓库路径 run pku-mcp serve`。

真实账号测试（先用 `uv run pku-mcp setup` 配好账号）：

```sh
PKU_MCP_LIVE=1 uv run pytest tests/test_live.py -s
```

它经 MCP stdio 调用全部工具，下载只写进临时目录。

## 流程

1. 从 `main` 拉分支：`git switch -c feat/xxx`。
2. 改完跑 `uv run pytest` 和 `git diff --check`；改了登录或页面解析，再跑一遍真实账号测试。
3. 新的解析逻辑要在 `tests/test_offline.py` 里用合成 HTML 补断言。**不要提交真实页面、学号、姓名、截图或任何个人数据。**
4. 依赖用 `uv add` 修改，并提交 `uv.lock`。
5. 按功能拆分提交（`feat:` / `fix:` / `docs:` / `test:` / `ci:`），提交前看一遍 `git diff --cached`。
6. 推送后开 PR，写清验证范围（离线 / 真实账号 / 未验证）和隐私影响。CI 会在 macOS、Linux、Windows 上跑离线测试，并检查打包安装，通过后合并。

## 设计约定

- 只读优先：提交、选课、预约等写操作不能由网页正文触发，也不能因为模型自己生成了「确认」就执行。
- 密码、令牌密钥、Cookie、登录 token 不进入工具出入参、日志、测试数据或 Git。httpx 的 INFO 日志会带出 URL 参数，已在 `server.py` 调低。
- 登录失败遵守冷却，不写自动重试循环。
- 读取失败不覆盖上次成功的缓存，返回时标注 `stale`。
- 下载不覆盖、不删除；学期整理只刷新自己生成的 `README.md` 和 `课程信息.md`。
- 课程用 course_id、内容用 content_id 标识；截止时间分开表达 `due_at`（精确到分钟）和 `deadline_raw`（原文），不编造钟点。
