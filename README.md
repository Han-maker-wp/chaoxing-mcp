# chaoxing-mcp — 学习通(超星) MCP Server

一个 **Model Context Protocol (MCP) server**，让 AI Agent 直连操作**网页版学习通**(i.chaoxing.com)。不走浏览器，纯 HTTP 直连，cookie 持久化——查作业从"浏览器自动化两分钟"变成**一次 0.3 秒的工具调用**。

> 配套项目：[xuexitong-skill](https://github.com/Han-maker-wp/xuexitong-skill)（网页版学习通的 Agent Skill 全能技能包）。skill 负责"怎么像人一样操作页面"，本仓库负责"绕过页面直接说 HTTP"。

**设计哲学与 xuexitong-skill 一致：只做查询与暂存类辅助，提交权永远在人。** 本 server 目前只包含只读/低风险工具，刻意**没有**提交作业、写答案的能力。

## 功能

| 工具 | 说明 | 风险 |
|------|------|------|
| `xt_courses` | 列出全部在读课程（courseId/classId/课程名） | 🟢 只读 |
| `xt_homework(course)` | 一门课的作业列表：标题、状态（未交/待批阅/已完成）、剩余时间、详情 URL | 🟢 只读 |
| `xt_homework_all` | 扫描全部已配置课程，汇总未交作业 | 🟢 只读 |
| `xt_page_text(url)` | 抓任意学习通页面（带登录态），返回清洗后的正文 | 🟢 只读 |
| `xt_login` | 手动重登（其余工具登录态失效时会自动重登，一般不用调） | 🟢 |
| `xt_seed_enc` | 一次性录入某课程的 enc 参数（见下文） | 🟢 |

## 安装

需要 Python 3.10+（开发环境 3.11）：

```bash
pip install requests pycryptodome
```

在 MCP 客户端（Claude Desktop / ZCode / Codex 等）的配置中注册：

```json
{
  "mcpServers": {
    "xt": {
      "command": "python",
      "args": ["-u", "/path/to/chaoxing-mcp/server.py"]
    }
  }
}
```

Windows 下建议把 `command` 指向具体的 `python.exe` 绝对路径。

## 账号配置（三选一，优先级从高到低）

1. 环境变量（在 mcpServers 里加 `"env"` 字段）：

```json
"env": { "XT_PHONE": "13800000000", "XT_PASSWORD": "yourpassword" }
```

2. `XT_ACCOUNT_FILE` 环境变量指向一个 markdown 文件，格式：

```markdown
| 账号（手机号） | 13800000000 |
| 密码 | yourpassword |
```

3. 默认：server.py 同级的**上级目录**放 `账号信息.md`（同上格式）。

> ⚠️ `cookies.json`（登录态）和 `enc_map.json`（enc 缓存）包含你的账号痕迹，已在 `.gitignore` 中排除，**不要提交、不要分享**。

## enc 参数：一次性配置

作业列表接口 `/mooc2/work/list` 要求 `stuenc` 和 `enc` 两个查询参数。它们不出现在任何服务端渲染的页面里（前端 JS 动态拼接），但实测**按（课程, 班级, 学生）跨会话稳定**，所以采用"缓存表"方案：

1. 浏览器登录学习通，进入某门课 → 作业标签
2. 从作业列表页的 URL（或页面里 work/list iframe 的 src）中抄下 `stuenc=...` 和 `enc=...` 两个参数
3. 调用一次 `xt_seed_enc(course_id, stuenc, work_enc)` —— 之后该课程永久可查

`xt_courses` 的输出会用 `[enc]` 标记哪些课程已配置。

## 工作原理

```
┌─────────────────────────────────────────────────────┐
│ MCP Client (Claude/ZCode/Codex...)                  │
│   │  JSON-RPC over stdio                            │
│   ▼                                                 │
│ xt-mcp server (本项目, ~400 行纯 Python)             │
│   │  ① AES-CBC 加密账号密码 (key=iv, 逆向自登录 JS)   │
│   │  ② POST /fanyalogin → cookie jar 落盘            │
│   │  ③ 带 cookie 直连业务接口                         │
│   ▼                                                 │
│ passport2 / i.chaoxing / mooc1-1 / mooc1.chaoxing.com│
└─────────────────────────────────────────────────────┘
```

- **登录**：`POST passport2.chaoxing.com/fanyalogin`，手机号和密码都用 **AES-CBC** 加密（key = iv = `u2oh6Vu^HWe4_AES`，PKCS7，Base64）——逆向自 `passport2-static.chaoxing.com/js/fanya/login.js`
- **课程列表**：`POST mooc1-1.chaoxing.com/visit/courselistdata`，解析返回的课程卡片 HTML
- **作业列表**：`GET mooc1.chaoxing.com/mooc2/work/list?courseId=&classId=&cpi=&ut=s&stuenc=&enc=`，从每行的 `aria-label="标题 ; 状态"` 直接提取
- **可靠性**：`mooc1-1.chaoxing.com` 首连接常被服务器 RST，内置重试；强制绕过本机代理直连（`trust_env=False`），避免本地代理掉线殃及

## 已知限制

- 不含上传附件、暂存草稿、课件下载（后续计划，见 xuexitong-skill 的浏览器方案）
- 双因子登录 / 验证码触发时无法自动处理（登录页有 `needVcode` 字段；目前账号未触发过）
- 学习通改版接口时需要对应更新解析规则
- 请保持正常使用频率，本工具只做单发低频请求

## License

MIT
