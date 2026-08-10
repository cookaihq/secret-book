# cred-ledger

**凭证台账 + agent 取用通道**：把 token、API key、账号密码、OSS/数据库配置组
登记在你自己的飞书多维表格里；Claude Code / Codex 等 agent 通过本 skill 按
意图或精确 ID 查询取用，取用输出一律掩码。

## ⚠️ 请先读这一段

- **明文存储，不加密。这不是密码管理器的替代品。**
- 安全边界 = 你的飞书表格权限：平台方与任何有表格权限的人可见；多维表格
  保留 180 天历史记录，删除另有 30 天回收站。
- 只建议存**可随时轮换的中低价值凭证**，并把台账表权限收紧到仅本人。
- 高价值凭证请使用 1Password 等专业密码管理器（其端到端加密架构严格强于
  本工具）。cred-ledger 的定位是：数据在你自己的租户里、飞书生态零新增
  账号、agent 原生取用、免费。

## 它解决什么

- 凭证散在各处：一张多维表格当**台账**——名称、归属、用途、到期日一目了然，
  到期提醒用多维表格原生自动化即可配置
- agent 要用凭证时反复问你要：`run` 把凭证注入子进程环境执行命令、`copy`
  把值送进剪贴板——**值不上屏、不进会话记录**
- 项目与凭证解耦：项目 `.env.local` 里只放指针（`CRED_LEDGER_IDS=sec_xxx`），
  真实凭证全在台账

## 快速开始

前置：[lark-cli](https://open.feishu.cn/) 已安装并完成登录；python3。

```bash
# 1. 初始化（新建台账 Base，或用 init-adopt --url 接管已有表）
python3 scripts/cred_ledger.py init-create
python3 scripts/cred_ledger.py config-write --app-token <base_token> --table-id <table_id>

# 2. 保存一组凭证（payload 走 stdin，dotenv 格式）
echo "GITHUB_TOKEN=ghp_xxx" | python3 scripts/cred_ledger.py save \
  --name github-main --service github --purpose "主账号推送" --use-global-config

# 3. 使用
python3 scripts/cred_ledger.py run  --name github-main --use-global-config -- git push origin main
python3 scripts/cred_ledger.py copy --name site-admin --key PASSWORD --use-global-config
python3 scripts/cred_ledger.py list --use-global-config
```

记录 = **凭证组**：`secret` 列是 dotenv 格式键值对，单 token 是单键退化情形，
OSS 一套 AK/SK/Endpoint/Bucket 是一条四键记录，`run` 一次全量注入。

## 作为 Agent Skill 安装

本仓库遵循 [Agent Skills](https://agentskills.io) 开放标准，Claude Code 与
Codex 通用。clone 后建 symlink：

```bash
ln -s "$(pwd)" ~/.claude/skills/cred-ledger   # Claude Code
ln -s "$(pwd)" ~/.agents/skills/cred-ledger   # Codex
```

行为约定（agent 必须遵守，详见 [SKILL.md](SKILL.md)）：凭证值只经脚本流向
子进程环境或剪贴板；多候选记录必须让用户点名，禁止 agent 自行猜选。

## 表结构（9 列，init 自动创建）

`id`（sec_ 随机机器键）· `name`（人类别名）· `service` · `account` ·
`purpose`（意图匹配主依据）· `secret`（dotenv payload）· `expires_at` · `notes` ·
`visible_to`（人员，可见范围）

直接在表格里手工加行也可以——缺 `id` 的行会在下次任意操作时自动补写。

`visible_to` 为空时记录不受限；非空时仅名单内用户能取用——当前用户（lark-cli
登录身份）不在名单里的记录，list 不显示、按 name/id 点名也取不到，没有绕过
开关。名单直接在表格里维护。旧表缺这一列时全表不受限，跑一次 `init-adopt`
会自动补建。
