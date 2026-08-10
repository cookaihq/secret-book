---
name: cred-ledger
description: >-
  凭证台账：把 token、API key、账号密码、OSS/数据库等配置组登记在用户自己的
  飞书多维表格里，agent 按意图或精确 ID 查询取用，取用输出一律掩码。当用户说
  "存一下这个 token/API key/密钥/凭证"、"用我存的 xx 推送/登录/调用"、"我的
  OSS/数据库配置"、"凭证台账/密钥台账"时使用。Credential ledger on the user's
  own Feishu Bitable: save/list/run/copy tokens, API keys, logins and config
  groups; agents inject values into subprocess env or clipboard with masked
  output. Requires lark-cli logged in. Do NOT use for encrypted vault needs —
  this skill stores plaintext; point users to a real password manager for
  high-value secrets.
compatibility: 需要已安装并登录 lark-cli（user 身份）、python3、可访问飞书开放平台的网络；Claude Code 与 Codex 双端可用
---

# cred-ledger

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
python3 "$SKILL_DIR/scripts/cred_ledger.py" <action> [flags]
```

## 配置（ADR 0003 分层）

每个变量独立 first-found-wins：进程环境变量 → `$PWD/.env.local` → `$PWD/.env`
→ `~/.config/cred-ledger/.env`（第 4 层**仅在传 `--use-global-config` 时读**）。
变量：`CRED_LEDGER_APP_TOKEN`（base token）、`CRED_LEDGER_TABLE_ID`、
`CRED_LEDGER_IDS`（项目绑定）。

- 常规用法：项目无本地覆盖时，**始终显式带 `--use-global-config`**。
- 项目要指向另一本台账：在项目 `.env.local` 写前两个变量即可覆盖全局。

## 初始化（首次使用）

先问用户：新建台账表，还是接管已有表？

- 新建：`init-create [--base-name 凭证台账]` → 从输出提取 base token 与
  credentials 表 table_id → **向用户口头确认后** `config-write --app-token X --table-id Y`
- 接管：`init-adopt --url <多维表格URL>` → 脚本校验 8 字段（缺列自动补建，
  类型不符会报错拒绝接管，不要绕过）→ 确认后同上 `config-write`
- `config-write` 默认写 `~/.config/cred-ledger/.env`；加 `--project` 写
  `$PWD/.env.local`（脚本会先验证该文件未被 git 跟踪且已被忽略）

## 动作

| 动作 | 命令 | 说明 |
|---|---|---|
| 保存 | `echo "GITHUB_TOKEN=..." \| … save --name github-main --service github --purpose "主账号推送" [--account cookaihq --expires-at 2027-01-01 --notes ...]` | payload 走 **stdin**，dotenv 格式一行一键值；`name` 重复会报错 |
| 列台账 | `… list` | 只回元数据（id/name/service/account/purpose/expires_at），无 secret |
| 查单条 | `… get --name github-main` | 元数据 + 键名列表，不回显值 |
| 执行 | `… run --name github-main -- git push origin main` | payload 全部键值对注入子进程环境后执行；多记录（`--id` 重复或 `CRED_LEDGER_IDS`）合并注入，键名冲突报错 |
| 复制 | `… copy --name site-admin --key PASSWORD` | 值进剪贴板不上屏；单键记录可省 `--key` |

payload 规则：值 = 首个 `=` 之后的原文（不去引号、不转义）、必须单行；
多行 blob（SSH 私钥、证书）请用户先 base64 成一行存入，用时自行解码。

### 项目绑定

项目 `.env.local` 配 `CRED_LEDGER_IDS=sec_xxx,sec_yyy` 后，该项目内
`run -- <命令>` 不需要指定记录——项目文件里只有指针，凭证全在台账。

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
用多维表格原生自动化配置）。
