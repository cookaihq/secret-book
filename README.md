# secret-book

**凭证台账 + agent 取用通道**：把 token、API key、账号密码、OSS/数据库配置组
登记在你自己的飞书多维表格里；Claude Code / Codex 等 agent 通过本 skill 按
意图或精确 ID 查询取用，取用输出一律掩码。用过一次的凭证会按
（项目, 命令）自动记住，下次缺配置时直接按 id 注入重试。

> 本仓库原名 `cred-ledger`，2026-08-10 更名为 `secret-book`。

## ⚠️ 请先读这一段

- **明文存储，不加密。这不是密码管理器的替代品。**
- 安全边界 = 你的飞书表格权限：平台方与任何有表格权限的人可见；多维表格
  保留 180 天历史记录，删除另有 30 天回收站。
- 只建议存**可随时轮换的中低价值凭证**，并把台账表权限收紧到仅本人。
- 高价值凭证请使用 1Password 等专业密码管理器（其端到端加密架构严格强于
  本工具）。secret-book 的定位是：数据在你自己的租户里、飞书生态零新增
  账号、agent 原生取用、免费。

## 它解决什么

- 凭证散在各处：一张多维表格当**台账**——名称、归属、用途、到期日一目了然，
  到期提醒用多维表格原生自动化即可配置
- agent 要用凭证时反复问你要：`run` 把凭证注入子进程环境执行命令、`copy`
  把值送进剪贴板——**值不上屏、不进会话记录**
- 缺配置自动兜底：`agent-rule --install` 把兜底规则写进各主流 agent
  （Claude Code / Codex / Gemini CLI / OpenCode / Qwen Code / iFlow / Amp /
  Windsurf / Cline / Copilot CLI / Goose）的全局指令文件；此后任何命令因缺
  key 失败，agent 会先查台账注入重试，用过一次即绑定、下次 `run --auto` 直用
- 项目与凭证解耦：项目 `.env.local` 里只放指针（`SECRET_BOOK_IDS=sec_xxx`），
  真实凭证全在台账
- 多租户不串号：`SECRET_BOOK_LARK_PROFILE` 把台账钉在指定的 lark-cli
  profile 上，别的任务切走 active profile 也不影响本工具

## 快速开始

前置：[lark-cli](https://open.feishu.cn/) 已安装并完成登录；
[uv](https://docs.astral.sh/uv/) >= 0.8（脚本的 Python 环境由 uv 管理，首次运行
自动在仓库里建 `.venv`）。

```bash
# 0. 下面所有命令都从仓库根目录执行；uv run --project . 负责钉死解释器，
#    不要改用裸 python3（会解析到系统解释器，见 ADR 0007）

# 1. 初始化（新建台账 Base，或用 init-adopt --url 接管已有表）
#    lark-cli 有多个 profile 时，全程带 --lark-profile 指定台账落在哪个租户
uv run --project . scripts/secret_book.py init-create [--lark-profile <name>]
uv run --project . scripts/secret_book.py config-write --app-token <base_token> \
  --table-id <table_id> [--lark-profile <name>]

# 2. 保存一组凭证（payload 走 stdin，dotenv 格式）
echo "GITHUB_TOKEN=ghp_xxx" | uv run --project . scripts/secret_book.py save \
  --name github-main --service github --purpose "主账号推送" --use-global-config

# 3. 使用
uv run --project . scripts/secret_book.py run  --name github-main --use-global-config -- git push origin main
uv run --project . scripts/secret_book.py copy --name site-admin --key PASSWORD --use-global-config
uv run --project . scripts/secret_book.py list --use-global-config

# 4.（可选）安装缺配置兜底规则到各 agent 全局指令文件
uv run --project . scripts/secret_book.py agent-rule            # 先看检测与安装状态
uv run --project . scripts/secret_book.py agent-rule --install  # 确认后安装，--remove 可卸载
```

退出码：`0` 成功 · `1` 一般错误 · `3` `run --auto` 无绑定或绑定失效 ·
`121` 写入结果不明（网络中断在写入过程中发生，本工具不盲重试）。遇到 `121` 先用
`list` 核实记录是否已写入，再决定重试。

`121` 取在「包装器自身错误」的惯例带（121–125，紧邻 shell 保留的 126/127/128+N）：
`run` 会原样透传被包装命令的退出码，这个值必须高于被包装命令的常用取值
（curl 文档化上限约 102），也要避开 `timeout` 的 124/125 与 `xargs` 的 123–125，
否则调用方无从分辨这个码是台账写入给的还是子命令给的。

记录 = **凭证组**：`secret` 列是 dotenv 格式键值对，单 token 是单键退化情形，
OSS 一套 AK/SK/Endpoint/Bucket 是一条四键记录，`run` 一次全量注入。

## 自动绑定

`run --id sec_xxx --bind -- <命令>` 成功后，(项目根, 命令名) → 记录 id 的
映射存进本机 `~/.config/secret-book/bindings.json`（只存元数据，无凭证值）。
之后 `run --auto -- <命令>` 直接按 id 注入；绑定按 id 存储，凭证轮换、记录
改名都不影响；记录删除时自动解除绑定。`bindings` 列出全部，`unbind` 解除。
注入后仍鉴权失败时应先 `unbind` 再重新匹配，防止错误绑定反复注入。

## 配置

每个变量独立 first-found-wins：进程环境变量 → `$PWD/.env.local` → `$PWD/.env`
→ `~/.config/secret-book/.env`（第 4 层仅在传 `--use-global-config` 时读）。

| 变量 | 作用 |
|---|---|
| `SECRET_BOOK_APP_TOKEN` | 台账 Base 的 base token |
| `SECRET_BOOK_TABLE_ID` | credentials 表的 table_id |
| `SECRET_BOOK_IDS` | 项目绑定：`sec_xxx,sec_yyy`，该项目内 `run` 免指定记录 |
| `SECRET_BOOK_LARK_PROFILE` | 台账所在租户的 lark-cli profile 名 |

### 多 profile / 多租户

`lark-cli` 的 active profile 是全局状态，别的任务随时可能切走它。台账表只存在
于一个租户里，active 被切走后本工具会用另一个租户的身份查表，报
`91403 you don't have permission`，`visible_to` 的比对基准（当前用户 open_id）
也会跟着错。

配了 `SECRET_BOOK_LARK_PROFILE=<name>` 后，脚本给每一次 lark-cli 调用追加
`--profile <name>`（表读写、`auth status`、`+base-create`、`+url-resolve`），
**不调 `lark-cli profile use`**，不产生全局副作用。未配置时不传 `--profile`，
沿用 active profile。命令行 `--lark-profile <name>` 覆盖配置——`init-create` /
`init-adopt` 跑在配置写入之前，指定建表落在哪个租户只能用它。

```bash
lark-cli profile list                                   # 看有哪些 profile
uv run --project . scripts/secret_book.py config-write \
  --app-token <base_token> --table-id <table_id> --lark-profile <name>
```

## 作为 Agent Skill 安装

本仓库遵循 [Agent Skills](https://agentskills.io) 开放标准，Claude Code 与
Codex 通用。clone 后建 symlink：

```bash
ln -s "$(pwd)" ~/.claude/skills/secret-book   # Claude Code
ln -s "$(pwd)" ~/.agents/skills/secret-book   # Codex
```

行为约定（agent 必须遵守，详见 [SKILL.md](SKILL.md)）：凭证值只经脚本流向
子进程环境或剪贴板；多候选记录必须让用户点名，禁止 agent 自行猜选；
`agent-rule --install` 前必须向用户展示目标文件与规则全文并取得同意。

## 表结构（9 列，init 自动创建）

`id`（sec_ 随机机器键）· `name`（人类别名）· `service` · `account` ·
`purpose`（意图匹配主依据）· `secret`（dotenv payload）· `expires_at` · `notes` ·
`visible_to`（人员，可见范围）

直接在表格里手工加行也可以——缺 `id` 的行会在下次任意操作时自动补写。

`visible_to` 为空时记录不受限；非空时仅名单内用户能取用——当前用户（lark-cli
登录身份）不在名单里的记录，list 不显示、按 name/id 点名也取不到，没有绕过
开关。名单直接在表格里维护。旧表缺这一列时全表不受限，跑一次 `init-adopt`
会自动补建。
