# AGENTS.md — 给 AI 助手的自助接入指南

**你是正在帮用户接入 chaoxing-mcp 的 AI 助手。** 按下面的步骤顺序执行，每一步都有验证点。全程你只需要问用户两件事：①用的哪个 MCP 客户端 ②学习通的手机号和密码。

## 步骤 1：前置检查

```bash
python --version        # 需要 3.10+；失败则试 python3 --version
python -c "import sys; print(sys.executable)"   # 记下这个绝对路径，后面配置要用
```

- 若 Python 缺失或版本过低：提示用户安装 Python 3.10+，或找可用的解释器（Windows 上常见位置 `%LOCALAPPDATA%\Programs\Python\`）。
- **配置里必须用 python 的绝对路径**，写 `python` 在部分客户端会找不到。

## 步骤 2：获取代码

首选 git clone（Windows PowerShell / macOS / Linux 通用）：

```bash
git clone https://github.com/Han-maker-wp/chaoxing-mcp.git "$HOME/chaoxing-mcp"
```

clone 失败时的备选：直接下载单文件（本项目就一个 server.py）：

```powershell
# Windows PowerShell（注意 bash 里的 curl 可能无网络权限）
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Han-maker-wp/chaoxing-mcp/main/server.py" -OutFile "$HOME\chaoxing-mcp\server.py"
```

```bash
# macOS / Linux
mkdir -p ~/chaoxing-mcp && curl -L -o ~/chaoxing-mcp/server.py \
  https://raw.githubusercontent.com/Han-maker-wp/chaoxing-mcp/main/server.py
```

验证：server.py 存在且大于 10KB。

## 步骤 3：安装依赖

```bash
python -m pip install requests pycryptodome
```

**必须用步骤 1 记下的那个 python**（同一解释器）安装。验证：

```bash
python -c "import requests, Crypto; print('deps ok')"
```

## 步骤 4：账号配置（二选一）

向用户询问学习通手机号和密码。**不要在对话里复述密码明文**，直接写进配置。

方式 A（推荐）：写进 MCP 配置的 `env` 字段（见步骤 5 的模板）。

方式 B：创建账号文件 `~/chaoxing-mcp/account.md`：

```markdown
| 账号（手机号） | 13800000000 |
| 密码 | 用户密码 |
```

并在配置里加环境变量 `"XT_ACCOUNT_FILE": "<该文件的绝对路径>"`。

## 步骤 5：写入 MCP 客户端配置

先问用户用的哪个客户端，按表找配置文件：

| 客户端 | 配置文件 | 键结构 |
|--------|----------|--------|
| ZCode | `~/.zcode/cli/config.json` | `mcp.servers`（**嵌套两层的 mcp→servers，不是顶层 mcpServers**；schema 严格，多余的键会导致 server 被静默丢弃） |
| Claude Desktop | Windows: `%APPDATA%\Claude\claude_desktop_config.json`；macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` | `mcpServers` |
| Claude Code | `~/.claude.json`（全局）或项目 `.mcp.json` | `mcpServers` |
| Codex CLI | `~/.codex/config.toml` | `[mcp_servers.xt]` |
| Cline / 其他 | 各自的 mcp 设置 | `mcpServers` |

**JSON 客户端通用模板**（ZCode 用户注意按上表改键结构）：

```json
{
  "mcpServers": {
    "xt": {
      "type": "stdio",
      "command": "<步骤1记下的python绝对路径>",
      "args": ["-u", "<server.py的绝对路径>"],
      "env": { "XT_PHONE": "用户手机号", "XT_PASSWORD": "用户密码" },
      "timeoutMs": 60000
    }
  }
}
```

**Codex CLI 的 TOML 模板**：

```toml
[mcp_servers.xt]
command = "<python绝对路径>"
args = ["-u", "<server.py绝对路径>", ]
startup_timeout_sec = 60

[mcp_servers.xt.env]
XT_PHONE = "用户手机号"
XT_PASSWORD = "用户密码"
```

注意事项：

- 路径里如果有中文/空格，JSON 里直接写没问题，但务必是**绝对路径**
- 若账号用方式 B（文件），env 里写 `XT_ACCOUNT_FILE` 而不是 PHONE/PASSWORD
- ZCode 也可以走界面：Settings → MCP 添加（等价于改 config.json）
- 修改 JSON 前先备份原文件；合并进已有的 `mcpServers` 对象，不要覆盖别的 server

## 步骤 6：验证

1. 告诉用户**重启 MCP 客户端**（配置只在启动时读取；ZCode 需重启会话）。
2. 重启后调用工具 `xt_courses` 自检：
   - ✅ 返回课程列表（十几到几十行）→ 接入成功，跳到步骤 7
   - ❌ 工具不存在 → 检查配置文件路径/键结构/JSON 语法，重启再试
   - ❌ `[ERROR] ... login failed` → 检查账号密码；若提示验证码/双因子，让用户浏览器登录一次学习通再重试
   - ❌ `ModuleNotFoundError` → 依赖装到了别的解释器，回到步骤 3

## 步骤 7：enc 一次性配置（要用"查作业"功能才需要）

作业列表接口需要每门课一对 `stuenc/enc` 参数（稳定值，配一次永久有效）：

1. 引导用户：浏览器登录学习通 → 进课程 → 作业标签
2. 从作业列表页 URL 中找到 `stuenc=...` 和 `enc=...` 两个参数
3. 调用 `xt_seed_enc(course_id, stuenc, work_enc)` 录入
4. 用 `xt_homework(course="课程名")` 验证能返回作业列表

## 安全须知（务必遵守）

- `cookies.json`（登录态）和 `enc_map.json` 包含账号痕迹：**不提交到任何仓库、不发给任何第三方**
- 不要把用户密码写进对话记录、日志或代码注释
- 本工具只有查询类能力；不要替用户提交作业——提交权永远在人
