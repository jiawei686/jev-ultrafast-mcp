# jev-ultrafast-mcp

[![CI](https://github.com/jiawei686/jev-ultrafast-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/jiawei686/jev-ultrafast-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

[English](README.md) · **简体中文**

给 agent 用的、快速且带守卫的浏览器控制服务，走 MCP。

**调用方 agent 就是决策者（policy）**。服务端的职责是把页面读便宜、把「瞄错元素」变不可能：
**不调第二个模型、不需要任何 API key、循环里没有截图。**

```
browser_open  →  元素表  →  browser_act [refs]  →  browser_assert
```

思路受 [`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast) 与 TypeSafe 的
typed-question API 启发。本项目是独立实现，与 browser-use 和 TypeSafe 无隶属关系。
差异化的取舍见 [`docs/DESIGN.md`](docs/DESIGN.md)。

## 快速开始

```bash
git clone https://github.com/jiawei686/jev-ultrafast-mcp.git
cd jev-ultrafast-mcp
python3 -m venv .venv
.venv/bin/pip install -e .          # Windows: .venv\Scripts\pip install -e .

python scripts/install.py           # 自动识别你装了的 MCP 客户端并写入配置
```

重启客户端，然后对它说：

> 打开 example.com，告诉我头条是什么。

装完就这一步。`install.py` 会探测 WorkBuddy、Claude Code、Claude Desktop、Codex CLI、Cursor、
VS Code、Cline、Windsurf、Gemini CLI，并按各自要求的格式写配置文件。它是**合并**而不是覆盖，
写之前会把原文件备份成 `*.bak`，也不会自己编造路径。

```bash
python scripts/install.py --list              # 看装了哪些、各自读哪个文件
python scripts/install.py --print             # 只打印将要写入的配置，不动任何文件
python scripts/install.py -c cursor,codex     # 只装这两个
python scripts/install.py --headed            # 保留可见的浏览器窗口
python scripts/install.py --allow-domains example.com,*.example.org
python scripts/install.py --uninstall         # 把条目删回去
```

依赖：Python ≥ 3.10，以及一个 Chromium 系浏览器（Chrome / Chromium / Edge / Brave）。
运行时只依赖 `mcp`、`websockets`、`httpx`——不用 Playwright、不用 Selenium、不用 browser-harness。

## 接入各类 agent

| 客户端 | `install.py` 写入的配置文件 | 装完还要做什么 |
|---|---|---|
| **WorkBuddy** | `~/.workbuddy/mcp.json` | 连接器 → 自定义连接器 → 点**信任** |
| **Claude Code** | `~/.claude.json`（user 作用域） | 或直接 `claude mcp add --scope user …` |
| **Claude Desktop** | `~/Library/Application Support/Claude/claude_desktop_config.json` | 完全退出应用再打开 |
| **Codex CLI** | `~/.codex/config.toml` | `codex mcp list` 确认 |
| **Cursor** | `~/.cursor/mcp.json` | 重载窗口 |
| **VS Code (Copilot)** | `…/Code/User/mcp.json` | 只在 Agent 模式可用，Ask/Edit 不行 |
| **Cline** | `…/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` | 重载窗口 |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | 重载窗口 |
| **Gemini CLI** | `~/.gemini/settings.json` | `gemini mcp list` 确认 |

<details>
<summary><b>手动配置</b> —— 不想跑安装脚本的话</summary>

下面每个客户端要的都是同一件事：解释器的绝对路径、模块名、一个环境变量。把 `/ABS/PATH`
换成你自己的路径。

**WorkBuddy** —— `~/.workbuddy/mcp.json`

```json
{
  "mcpServers": {
    "jev-ultrafast-mcp": {
      "command": "/ABS/PATH/jev-ultrafast-mcp/.venv/bin/python",
      "args": ["-m", "jev_ultrafast_mcp"],
      "env": { "JEVMCP_HEADLESS": "1" }
    }
  }
}
```

**Claude Code**

```bash
claude mcp add --scope user jev-ultrafast-mcp \
  --env JEVMCP_HEADLESS=1 \
  -- /ABS/PATH/jev-ultrafast-mcp/.venv/bin/python -m jev_ultrafast_mcp
```

也可以手写同样的 `mcpServers` 对象：`~/.claude.json` 是 user 作用域，项目根目录的 `.mcp.json`
是团队作用域（会提交进 git）。

**Codex CLI** —— `~/.codex/config.toml`。Codex 用 TOML，而且表名是 `mcp_servers`，不是
`mcpServers`：

```toml
[mcp_servers.jev-ultrafast-mcp]
command = "/ABS/PATH/jev-ultrafast-mcp/.venv/bin/python"
args = ["-m", "jev_ultrafast_mcp"]
startup_timeout_sec = 20

[mcp_servers.jev-ultrafast-mcp.env]
JEVMCP_HEADLESS = "1"
```

同一条也可以用命令加：`codex mcp add jev-ultrafast-mcp --env JEVMCP_HEADLESS=1 -- /ABS/PATH/…/python -m jev_ultrafast_mcp`。

**Cursor** —— 全局 `~/.cursor/mcp.json`，或单个项目的 `.cursor/mcp.json`。内容和 WorkBuddy 一样。

**VS Code (Copilot)** —— `.vscode/mcp.json`，或者命令面板 → *MCP: Open User Configuration*
（对所有工作区生效）。VS Code 有两处和别家不同：根键是 `servers`，而且每条必须显式写
`"type": "stdio"`，否则会被静默忽略。

```json
{
  "servers": {
    "jev-ultrafast-mcp": {
      "type": "stdio",
      "command": "/ABS/PATH/jev-ultrafast-mcp/.venv/bin/python",
      "args": ["-m", "jev_ultrafast_mcp"],
      "env": { "JEVMCP_HEADLESS": "1" }
    }
  }
}
```

**Claude Desktop** —— `claude_desktop_config.json`（Windows 在 `%APPDATA%\Claude\`），同样是
`mcpServers` 对象。要从托盘完全退出再启动，光关窗口不够。

</details>

浏览器在第一次 `browser_open` 之前不会启动；它驱动的标签页是**自己拥有的后台标签页**——
焦点模拟让动画和菜单照常运行，但不会抢你的窗口。

## agent 实际读到的东西

`browser_open` / `browser_observe` 返回的是一张**元素表**，不是 DOM dump，也不是截图。每行是
`ref` + 角色简码 + 标记 + 可访问名称；可编辑的带上当前值，可选择的带上选项：

```
[obs#1] http://127.0.0.1:54409/fixture.html  "Ultrafast Fixture"  scroll=0/860  reachable=16/16
e1   lnk    Home
e2   lnk    About
e3   lnk    Open popup
e4   inp*   Where from? ▸ ""
e5   cmb*   Where to? ▸ ""
e6   cmb    Passengers ▸ 1 adult opts{1 adult=1 | 2 adults=2 | 3 adults=3 | 4 adults=4}
e7   chk·   Nonstop only
e8   btn    Search
e10  inp*   Password ▸ ""
e11  file    CV accept=.pdf,.txt
e12  btn    Delete account
```

标记含义：`*` 可编辑 · `»` 在视口外（服务端会先滚到它） · `⊘` 被别的东西盖住 · `▾` 已展开 ·
`✓`/`·` 勾选状态。`reachable=16/19` 表示页面上有 3 个控件此刻被遮挡或不在视口内。

`browser_act` 收一批针对这些 ref 的操作，然后**只回报变化的部分**：

```
[delta#2] http://127.0.0.1:54409/fixture.html  "Ultrafast Fixture"  reachable=16/16
~ e4   inp*   Where from? ▸ "Zurich"   (was "")
~ e7   chk✓   Nonstop only
  2 changed, 0 new, 0 gone
```

**「操作了什么都没发生」是 agent 循环里最贵的一种情况**，因为模型会重试。所以这件事被压缩成一行：

```
[delta#3] … 16 elements
  = no change (16 elements)
```

新增的行以 `+` 开头，消失的以 `-` 开头。两个控件重名时，行里会带上能区分它们的那点上下文：

```
+ e17  btn    Select  @Zurich → Anywhere Option 1 · 1 adult · nonstop Select
+ e18  btn    Select  @Zurich → Anywhere Option 2 · 1 adult · nonstop Select
```

已经失效的 ref 会被**拒绝并给出原因**，而不是点到错误的元素上：

```json
[{"op": "click", "ok": false, "ref": "e999", "error": "detached"}]
```

## 为什么还要再造一个浏览器 MCP？

多数浏览器 MCP 暴露的是 CDP 原语——`click_at_xy`、CSS 选择器、`evaluate`。灵活性拉满，安全性见底：
每一步都依赖模型自己编一个选择器或坐标，编错了要么静默失败，要么更糟——成功点在了错的元素上。

`jev-ultrafast-mcp` 走反向取舍。模型只负责说**哪个**元素（`ref`）和**做什么**；把这个 ref 变成
一次真实点击是服务端的事，而服务端**宁可拒绝，也不猜**。

| | 原语型浏览器 MCP | **jev-ultrafast-mcp** |
|---|---|---|
| agent 怎么瞄目标 | 自己写选择器 / 坐标 / JS | 从元素表里挑一个 `ref` |
| 额外模型调用 | 无 | **无 —— 你的 agent 就是 policy** |
| 需要的 API key | 无 | **无** |
| ref 生命周期 | 不适用（每步重造） | **跨观测稳定** |
| 重读页面 | 每次全量 dump | **增量**：`+` 新增 / `~` 变更 / `-` 移除 / `= no change` |
| 往返次数 | 每个动作一次 | **批量**：多个 op 一次往返 |
| 目标有歧义 | 模型猜 | **服务端拒绝并说明原因** |
| Shadow DOM / iframe | 通常不支持 | **可穿透，滚动会带上 frame 偏移** |
| 重复流程 | 重跑模型 | **宏回放，零模型成本** |
| 怎么知道做成了 | 模型自己看页面 | **确定性 `browser_assert`** |
| 危险点击 | 模型自己判断 | **`needs_confirmation`、域名白名单、敏感字段脱敏** |

批量和增量不是锦上添花。在仓库自带的端到端测试里，整轮会话交给模型 **13.4 KB**，其中
**12.6 KB 是增量、0.8 KB 是全量表**——模型只重读页面上动过的那部分。

### 诚实的代价

agent 仍然要花 token 读元素表；而且**需要真正视觉判断的页面不在范围内**（验证码、图表、
canvas 应用），因为这个服务刻意不看像素。这类场景请换截图 + 视觉的 agent，或者在这里用
`screenshot` 操作——那是给人留证据，不是给模型看的。

## 工具

### `browser_open(url, session="default", hint="")`
在新建的自有标签页里打开 URL，返回完整元素表。

### `browser_observe(session="default", mode="auto", include_text=True, include_json=False)`
重读页面。`auto` 出增量，`full` 强制全量。`= no change` 意味着上一个动作什么都没做——
该换策略，不要重试。

### `browser_act(ops, session="default", dry_run=False, stop_on_error=True, observe_after=True)`
按顺序在**一次往返**里执行 ops，然后返回增量。

| op | 字段 |
|---|---|
| `click` | `ref` |
| `type` | `ref`、`text`、`clear`=true、`submit`=false、`slow` |
| `select` | `ref`、`value`（选项 value 或 label） |
| `toggle` | `ref`、`state`（不填则翻转） |
| `hover` / `upload` | `ref` / `ref`、`path` |
| `keys` | `key`（`"Enter"`、`"Meta+A"`、`"ArrowDown"`） |
| `scroll` | `dir`、`amount`、`ref` |
| `nav` / `back` / `forward` / `reload` | `url`（`nav` 用） |
| `wait` / `wait_for_ref` / `wait_for_text` / `wait_for_load` | `ms` / `ref`,`timeout_ms` / `text` / `timeout_ms` |
| `screenshot` | `path`、`full`、`format` |
| `tab` | `action`=`list\|new\|switch\|close`、`target_id`、`index`、`url` |
| `eval` | `js` —— 仅在 `JEVMCP_ALLOW_JS=1` 时可用 |

```json
{"ops": [
  {"op": "type",   "ref": "e4", "text": "Zurich"},
  {"op": "select", "ref": "e6", "value": "3 adults"},
  {"op": "toggle", "ref": "e7"},
  {"op": "click",  "ref": "e8"}
]}
```

失败会说明原因：`occluded`、`detached`、`target_changed`、`page_changed`、`needs_confirmation`、
`blocked_by_policy`。这时该去 `browser_observe`，而不是重试。

操作标签页时**优先用 `target_id` 而不是 `index`**：index 是位置性的，标签列表一变就会重编号，
上一次调用读到的 index 可能已经指向另一个标签了。

### `browser_assert(checks, session="default")`
确定性断言——不靠模型「感觉」判断是否完成。

```json
{"checks": [
  {"type": "url_matches",    "pattern": "*/checkout*"},
  {"type": "text_contains",  "text": "Order confirmed"},
  {"type": "element_exists", "role": "button", "name": "Continue"},
  {"type": "value_equals",   "ref": "e4", "value": "Zurich"},
  {"type": "count_at_least", "role": "link", "min": 3}
]}
```

### `browser_macro(action, session="default", name="", params={}, ...)`
`record_start` → 手动走一遍 → `record_stop` → `run`。回放**不花模型调用**；步骤按 role +
可访问名称重新解析，匹配偏弱或有歧义时直接报错，而不是点错东西。

### `browser_goal(goal, session="default", max_steps=20, verify=[...])`
在服务端跑完整个循环，用 TypeSafe 的投机扇出（每步一次请求）。需要 `TYPESAFE_API_KEY`；
给了 `verify` 检查时返回 `verified: PASS/FAIL`。

### `browser_tabs` · `browser_sessions` · `browser_close` · `browser_doctor`
标签页管理（列 / 新建 / 切换 / 关闭）、会话列举、收尾、以及自检——报告找到的是哪个浏览器、
能不能连上。

## 配置

全部可选，默认值就是设计意图。

| 环境变量 | 默认 | 含义 |
|---|---|---|
| `JEVMCP_CHROME` | 自动探测 | Chrome/Chromium/Edge/Brave 可执行文件 |
| `JEVMCP_MODE` | `launch` | `launch` 自己启动，或 `attach` 到已运行的 CDP |
| `JEVMCP_CDP_URL` | — | `mode=attach` 时填 `http://127.0.0.1:9222` |
| `JEVMCP_HEADLESS` | `1` | `0` 显示窗口 |
| `JEVMCP_FOREGROUND` | `0` | `1` 激活自有标签页 |
| `JEVMCP_SANDBOX` | `auto` | 浏览器启动即崩时，`auto` 会用 `--no-sandbox` 重试 |
| `JEVMCP_PROFILE_DIR` | `~/.jev-ultrafast-mcp/chrome-profile` | 持久化 profile——手动登录一次，之后一直保持 |
| `JEVMCP_ALLOW_DOMAINS` | *（不限）* | 逗号分隔；导航到其他域名会被拒绝 |
| `JEVMCP_DENY_DOMAINS` | *（无）* | 逗号分隔黑名单 |
| `JEVMCP_CONFIRM_PATTERNS` | pay / delete / unsubscribe … | 命中这些词的操作需要 `"confirm": true` |
| `JEVMCP_ALLOW_JS` | `0` | 打开 `eval` 与 js 断言 |
| `JEVMCP_ALLOW_UPLOADS` | `1` | 控制 `upload` op |
| `JEVMCP_MAX_ACTIONS` | `250` | 元素表条数上限，按有用程度裁剪 |
| `JEVMCP_MAX_TEXT` | `6000` | 单次观测的可见文本上限 |
| `JEVMCP_STATE_DIR` | `~/.jev-ultrafast-mcp` | profile、宏、截图的存放位置 |
| `TYPESAFE_API_KEY` | — | 可选；开启 `browser_goal` |
| `TEXT_MODEL_API_KEY` | — | 可选；只用于 `browser_goal` 模式下的输入 |

如果你要拿它操作自己的账号，有两个值得先设上：`JEVMCP_ALLOW_DOMAINS` 把浏览器钉死在一组域名内，
之外一律拒绝；配一个持久的 `JEVMCP_PROFILE_DIR`，让你手动登录一次，而不是把密码教给模型。

## 不接 agent 也能试

```bash
.venv/bin/python scripts/smoke.py            # 无头，52 项检查
.venv/bin/python scripts/smoke.py --headed   # 看着它操作
```

它会启动 Chrome、用 HTTP 伺服 `tests/fixture.html`，然后把真实代码路径全跑一遍：批量执行、
自动补全、遮挡弹层、Shadow DOM、同源 iframe、文件上传、密码字段、危险点击守卫、过期 ref、
宏录制回放、标签页交接、截图。

```
1. Observation — one atomic read, indexed refs
  [ok  ] element table is not empty  — 16 elements
  [ok  ] shadow DOM element indexed  — Shadow action -> e14
...
5. Occlusion — precomputed, not discovered by a failed click
  [ok  ] covered control flagged before any click  — e8 occluded=True
  [ok  ] click on a covered control is refused with a reason  — occluded
...
  52/52 checks passed
```

## 目录结构

```
jev_ultrafast_mcp/
  js/observer.js   页内观察器：稳定 ref、shadow/frame 穿透、verify/resolve
  cdp.py           同步 CDP 客户端 + Chrome 启动（不套第三方库）
  browser.py       会话、守卫执行、op 分发、宏录制
  observe.py       元素模型、紧凑渲染、增量计算
  macros.py        语义描述符、打分重解析、存储
  assertions.py    确定性断言
  policy.py        可选的 TypeSafe 涡轮（投机扇出）
  safety.py        域名围栏、脱敏、确认规则
  config.py        环境变量配置
  server.py        MCP 接口层
scripts/
  install.py       为本机的各个 MCP 客户端写入正确格式的配置
  smoke.py         对真实浏览器的端到端验证
  mcp_check.py     走真实 stdio MCP 协议驱动服务端
```

## 开发

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke.py        # 52 项，真实浏览器
.venv/bin/python scripts/mcp_check.py    # 17 项，真实 stdio MCP
```

CI 会在 Python 3.10 / 3.12 / 3.13 上、对着无头 Chrome 全跑一遍。改「目标解析」那部分逻辑之前
请先读 [`CONTRIBUTING.md`](CONTRIBUTING.md)——那是这个项目的全部意义所在。

## 许可证

MIT —— 见 [`LICENSE`](LICENSE)。
