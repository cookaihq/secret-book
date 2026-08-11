---
name: secret-book
description: >-
  凭证台账：把 token、API key、账号密码、OSS/数据库等配置组登记在用户自己的
  飞书多维表格里，agent 按意图或精确 ID 查询取用，取用输出一律掩码。当用户说
  "存一下这个 token/API key/密钥/凭证"、"用我存的 xx 推送/登录/调用"、"我的
  OSS/数据库配置"、"凭证台账/密钥台账/secret book"，或任何命令、skill、工具
  因缺少 API key/token/凭证配置而失败、需要查台账兜底注入时使用。Credential
  ledger on the user's own Feishu Bitable: save/list/run/copy tokens, API keys,
  logins and config groups; remembers per-(project, command) bindings so a
  previously used credential is reused by id next time; also the fallback when
  another skill or command fails due to missing credentials. Requires lark-cli
  logged in. Do NOT use for encrypted vault needs — this skill stores
  plaintext; point users to a real password manager for high-value secrets.
compatibility: 需要已安装并登录 lark-cli（user 身份）、python3、可访问飞书开放平台的网络；Claude Code 与 Codex 双端可用
---

# secret-book

## 安全声明（必读，向用户如实转述）

- **明文存储，不加密**。本 skill 不是密码管理器替代品，绝不能向用户描述为
  "安全存储/加密保管"。安全边界 = 用户飞书租户的表格权限，由用户自管。
- 平台方与任何有表格权限的人可见；多维表格保留 **180 天历史记录**（含单条
  记录字段变更），删除另有 30 天回收站。
- 只建议存**可随时轮换的中低价值凭证**；建议用户把台账表权限收紧到仅本人。
- 付得起且买得到 1Password 等专业密码管理器的用户，高价值凭证请用那些产品。
- **掩码纪律**：凭证值只能经脚本流向子进程环境（`run`）或剪贴板（`copy`）。
  禁止用 lark-cli 直接读 `secret` 列、禁止明文打印、禁止把值写进回复。
  agent 帮用户浏览器自动填表会使密码进入会话记录——事先告知用户再做。

## 前置依赖

`lark-cli` 已安装并完成 user 身份登录（缺失时引导用户走 lark 系 skill 的
安装/登录流程，本 skill 不重造门禁）。脚本统一入口：

```bash
python3 "$SKILL_DIR/scripts/secret_book.py" <action> [flags]
```

## 配置（ADR 0003 分层）

每个变量独立 first-found-wins：进程环境变量 → `$PWD/.env.local` → `$PWD/.env`
→ `~/.config/secret-book/.env`（第 4 层**仅在传 `--use-global-config` 时读**）。
变量：`SECRET_BOOK_APP_TOKEN`（base token）、`SECRET_BOOK_TABLE_ID`、
`SECRET_BOOK_IDS`（项目绑定）、`SECRET_BOOK_LARK_PROFILE`（台账所在租户的
lark-cli profile 名）。

- 常规用法：项目无本地覆盖时，**始终显式带 `--use-global-config`**。
- 项目要指向另一本台账：在项目 `.env.local` 写前两个变量即可覆盖全局。

### SECRET_BOOK_LARK_PROFILE（台账绑定哪个 lark-cli profile）

`lark-cli` 支持多 profile（`lark-cli profile list`），每个 profile 对应一个飞书
应用 / 租户身份，**active profile 是全局状态，任何任务都可能把它切走**。台账表
只存在于某一个租户里，active 被切走后本 skill 会拿另一个租户的身份查表，报
`91403 you don't have permission`；`visible_to` 可见范围的比对基准（当前用户
open_id）也会跟着错。

- 配了 `SECRET_BOOK_LARK_PROFILE=<name>` 后，脚本给**每一次** lark-cli 调用
  追加 `--profile <name>`——包括 `base` 表读写、`auth status`（open_id 判定）、
  `+base-create`、`+url-resolve`。
- **绝不调 `lark-cli profile use`**：那会改用户的全局 active profile。逐次传参
  不产生任何全局副作用。
- **未配置时行为不变**：不传 `--profile`，沿用 active profile。
- 命令行 `--lark-profile <name>` 覆盖以上四层配置。`init-create` / `init-adopt`
  在配置写入**之前**执行，指定建表 / 接管落在哪个租户只能靠这个 flag。
- 写入配置：`config-write --lark-profile <name>`（与 `--app-token`
  `--table-id` 同一条命令，默认写全局，`--project` 写 `$PWD/.env.local`）。
- profile 名写错或该 profile 未登录时，脚本报错文案会带上 `（profile=xxx）`，
  按提示跑 `lark-cli profile list` 核对。

## 初始化（首次使用）

先问用户：新建台账表，还是接管已有表？本机 lark-cli 有多个 profile 时（跑
`lark-cli profile list` 看），**先问清台账要落在哪个 profile**，全程带
`--lark-profile <name>`，最后写进配置。

- 新建：`init-create [--base-name 凭证台账] [--lark-profile <name>]` → 从输出
  提取 base token 与 credentials 表 table_id → **向用户口头确认后**
  `config-write --app-token X --table-id Y [--lark-profile <name>]`
- 接管：`init-adopt --url <多维表格URL> [--lark-profile <name>]` → 脚本校验
  9 字段（缺列自动补建，类型不符会报错拒绝接管，不要绕过）→ 确认后同上
  `config-write`
- `config-write` 默认写 `~/.config/secret-book/.env`；加 `--project` 写
  `$PWD/.env.local`（脚本会先验证该文件未被 git 跟踪且已被忽略）
- 配置写完后，向用户介绍「缺配置兜底」规则并**展示目标文件清单与规则全文**，
  用户同意后执行 `agent-rule --install`（见下文）

## 动作

| 动作 | 命令 | 说明 |
|---|---|---|
| 保存 | `echo "GITHUB_TOKEN=..." \| … save --name github-main --service github --purpose "主账号推送" [--account cookaihq --expires-at 2027-01-01 --notes ...]` | payload 走 **stdin**，dotenv 格式一行一键值；`name` 重复会报错 |
| 列台账 | `… list` | 只回元数据（id/name/service/account/purpose/expires_at），无 secret |
| 查单条 | `… get --name github-main` | 元数据 + 键名列表，不回显值 |
| 执行 | `… run --name github-main -- git push origin main` | payload 全部键值对注入子进程环境后执行；多记录（`--id` 重复或 `SECRET_BOOK_IDS`）合并注入，键名冲突报错 |
| 自动执行 | `… run --auto -- <命令>` | 按 (项目根, 命令名) 查历史绑定直接注入；无绑定/绑定失效退出码 **3** |
| 执行并绑定 | `… run --id sec_xxx --bind -- <命令>` | 子进程退出码 0 时把记录绑定到 (项目根, 命令名)，下次 `--auto` 直用 |
| 列绑定 | `… bindings` | 列出全部自动绑定（本机 `~/.config/secret-book/bindings.json`，只存元数据） |
| 解绑 | `… unbind --command <命令名> [--scope <项目根>]` | 解除一条自动绑定 |
| 复制 | `… copy --name site-admin --key PASSWORD` | 值进剪贴板不上屏；单键记录可省 `--key` |
| 兜底规则 | `… agent-rule [--install\|--remove] [--agent <key>] [--force]` | 检查/安装/移除各 agent 全局指令文件里的缺配置兜底规则块 |

payload 规则：值 = 首个 `=` 之后的原文（不去引号、不转义）、必须单行；
多行 blob（SSH 私钥、证书）请用户先 base64 成一行存入，用时自行解码。

### 可见范围（visible_to）

表内人员字段 `visible_to` 控制记录级可见性：**为空 = 不受限；非空 = 仅名单内
用户可取用**——当前用户（lark-cli user 身份的 open_id）不在名单里时，该记录在
list/get/run/copy 全部路径上取不到（list 不显示、点名报「找不到」），无绕过
flag。名单直接在多维表格里维护，脚本不提供写入口。取不到 open_id 时脚本报错
退出，不放行受限记录。存量旧表缺此列时全表视为不受限，跑一次 `init-adopt`
即自动补建。唯一例外：`save` 的 name 查重跨全表（含隐藏记录），避免重名。

### 项目绑定

项目 `.env.local` 配 `SECRET_BOOK_IDS=sec_xxx,sec_yyy` 后，该项目内
`run -- <命令>` 不需要指定记录——项目文件里只有指针，凭证全在台账。

## 缺配置兜底流程（硬规则）

任何命令、skill、工具因**缺少凭证/API key/token 配置**而失败时，按序执行：

1. `run --auto --use-global-config -- <原命令>`：命中绑定即注入重试；
   退出码 3 = 无绑定，进入下一步。
2. `list` 拉元数据，按 name/service/account/purpose 对照失败场景的意图匹配：
   **唯一命中** → `run --id <id> --bind --use-global-config -- <原命令>`
   （成功自动落绑定并播报）；**多条候选或不确定** → 把候选列给用户点名选择，
   **禁止自行选一个执行**——凭证选错的代价不可承受；**无命中** → 向用户要值，
   问是否 `save` 进台账。
3. **注入后命令仍以鉴权类错误失败** → 立即 `unbind --command <命令名>` 解除
   绑定，回到第 2 步重新匹配。**禁止用同一绑定重试**。
4. **MCP server 缺配置**：MCP 进程由会话启动时拉起，无法注入环境变量。
   改用 `copy` 取值，引导用户写入对应配置后重启会话；不要假装能自动修好。

绑定的优先级：项目 `.env.local` 的 `SECRET_BOOK_IDS`（显式）>
`bindings.json`（自动学习）。`--auto` 在显式绑定存在时直接用它。
绑定按记录 `id` 存储——凭证轮换、改名都不影响；记录删除时 `--auto` 会自动
解除失效绑定并退出码 3。

## agent-rule：把兜底规则装进各 agent 的全局指令文件

上面的兜底流程要生效，前提是「遇到缺配置错误时能想起本 skill」。
`agent-rule --install` 把一段带哨兵标记、带版本号的规则块写进已检测到的
各 agent 全局指令文件（Claude Code `~/.claude/CLAUDE.md`、Codex
`~/.codex/AGENTS.md`、Gemini CLI、OpenCode、Qwen Code、iFlow、Amp、
Windsurf、Cline、Copilot CLI、Goose；完整矩阵见脚本 `AGENT_TARGETS`）。

- **安装前必须取得用户确认**：列出将写入的目标文件与规则全文，用户同意后
  再执行。改用户的全局指令文件是侵入操作，禁止静默安装。
- 幂等：重复 install 跳过已是最新的；规则升级时原地替换旧版本块；
  检测到用户手工改过规则块时跳过，需 `--force` 才覆盖。
- Cursor 无全局指令文件（User Rules 存 IDE 设置内），`--install` 时会打印
  规则文本供用户手动粘贴进 Settings → Rules。
- Windsurf 的 `global_rules.md` 有 6000 字符上限，超限会跳过并提示。
- 卸载：`agent-rule --remove` 精确移除规则块，不动文件其余内容。

## 意图匹配（硬规则）

用户说"用我的 xx 凭证"时：先 `list` 拉元数据，按 name/service/account/purpose
对照用户意图。**命中多条候选且不确定时，必须把候选列给用户点名选择，禁止
自行选一个执行**——凭证选错的代价不可承受。

## 摄入路径

- 用户直接在多维表格里加行（缺 `id` 的行会在下次 list/get/run 时自动补写）
- 或用户在对话中给出值，agent 经 stdin 传给 `save`（提醒用户：值会留在本次
  会话记录中；介意的话请自行往表格里粘贴）

## 边界（v1 非目标）

不做：加密、`export` 落盘、原生多行值、多表管理、agent 自动填表专门接口、
Notion 后端（存储层已按适配器切面隔离，v1.1 增量接入）、到期提醒（引导用户
用多维表格原生自动化配置）。绑定缓存不做命令参数级指纹（如按 git remote
区分）——靠复用时播报 + 失败即解绑兜住误绑风险。
