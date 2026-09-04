---
name: secret-book
version: 2.0.0
description: >-
  v2.0.0｜令牌：把 token、API key、账号密码、OSS/数据库等配置组保存到用户自己的
  飞书令牌表里，agent 按意图或精确 ID 查询取用，取用输出一律掩码；本机可保存多套
  有名称的令牌配置，并持久切换唯一的当前配置。当用户说
  "存一下这个 token/API key/密钥/凭证"、"用我存的 xx 推送/登录/调用"、"我的
  OSS/数据库配置"、"切换到工作配置"、"查看当前配置"、"令牌/secret book"，
  或任何命令、skill、工具因缺少 API key/token/凭证配置而失败、需要查令牌表兜底
  注入时使用。Credential
  storage in the user's own Feishu Bitable: save/list/run/copy tokens, API keys,
  logins and config groups; stores multiple named local configurations with one
  active configuration; remembers per-(token-table, project, command) bindings.
  Requires lark-cli logged in. Do NOT use for encrypted vault needs: this skill
  stores plaintext; point users to a real password manager for high-value secrets.
compatibility: 需要 macOS 或 Linux、已安装并登录 lark-cli（user 身份）、uv >= 0.8（脚本运行时由 uv 管理，首次运行自动建 .venv）、可访问飞书开放平台的网络；当前不支持 Windows；Claude Code 与 Codex 双端可用
---

# secret-book

## 安全声明（必须向用户如实转述）

- **明文存储，不加密**。本 skill 不是密码管理器，不能描述为“安全存储”或
  “加密保管”。安全边界由用户的飞书租户和令牌表权限决定。
- 平台方与任何有表格权限的人可见；飞书多维表格保留 180 天历史记录，删除后
  另有 30 天回收站。
- 只建议保存可随时轮换的中低价值令牌；令牌表权限建议收紧到仅本人。高价值
  令牌应使用 1Password 等专业密码管理器。
- 禁止用 lark-cli 直接读取 `secret` 列，禁止打印或在回复中写出明文令牌值。
  值只能经脚本进入子进程环境（`run`）或剪贴板（`copy`）。若目标工具需要
  持久配置，可用 `run` 包装写文件命令；目标同时支持项目和全局配置时，先问
  用户写哪个作用域。写入 Git 工作树前，必须确认目标文件未被跟踪且已被忽略。
- 用户让 agent 在浏览器中自动填写令牌时，值会进入会话记录；执行前明确告知。

## 第 0 步：自动检查更新

每次进入正式流程前运行：

```bash
scripts/check_update.sh
```

- 退出码 `0`：直接继续，不复述输出。
- 退出码 `10`：原样转述报告并询问是否拉取。用户确认后运行
  `scripts/check_update.sh --pull`；用户拒绝、未回应或拉取失败时，继续使用当前版本。
- 用户要求关闭时，只在 `~/.config/secret-book/.env` 写入
  `AUTO_UPDATE_CHECK=0`，保留文件内其它内容。

检查更新失败不能阻塞用户当前任务。该脚本直接运行，不经过 uv。

## 命令入口

依赖：`lark-cli` 已安装并完成 user 身份登录，`uv >= 0.8`。所有 Python 命令统一用：

```bash
uv run --project "$SKILL_DIR" "$SKILL_DIR/scripts/secret_book.py" <action> [flags]
```

禁止把示例改成裸 `python3`。脚本虽有运行时 bootstrap，调用方仍必须显式使用
skill 自带的 uv 项目。

## 本地令牌配置

一套令牌配置负责定位一张飞书令牌表，并固定访问该表时允许使用的飞书身份。
全局文件 `~/.config/secret-book/.env` 可保存多套有名称的配置，但任何时刻只有
一套“当前配置”。业务命令只有显式带 `--use-global-config` 才会启用这一层。

全局文件使用一个结构化值：

```dotenv
SECRET_BOOK_CONFIGS_JSON='{"schema_version":1,"active_id":"cfg_xxxxxxxxxx","configs":{"cfg_xxxxxxxxxx":{"name":"工作","app_token":"<base_token>","table_id":"<table_id>","lark_profile":"<profile>","feishu_app_id":"<app_id>","feishu_user_open_id":"<open_id>"}}}'
```

每套配置原子包含：`name`、`app_token`、`table_id`、`lark_profile`、
`feishu_app_id`、`feishu_user_open_id`。`cfg_` ID 稳定不变；名称必须唯一。
脚本通过文件锁、同目录临时文件、`fsync` 和 `os.replace` 更新文件，权限为
`0600`，并保留注释、`AUTO_UPDATE_CHECK` 和未知键。不要手工编辑 JSON；使用：

| 用户意图 | 命令与结果 |
|---|---|
| 查看配置 | `config list`：只显示当前标记、cfg ID、名称、lark-cli profile |
| 新增配置 | `config save --name <名称> --app-token <token> --table-id <id> --lark-profile <profile>`；第一套自动成为当前配置，后续新增不切换 |
| 切换配置 | `config use --name <名称>`：只修改本地 `active_id`，不调用 lark-cli，不修改其 active profile |
| 更新飞书身份 | `config rebind --name <名称> --lark-profile <profile>`：保留 cfg ID、表定位、名称和当前状态 |
| 重命名 | `config rename --name <旧名称> --new-name <新名称>`：cfg ID 不变 |
| 删除 | `config remove --name <名称>`；有多套时不能删除当前配置，先切换；最后一套可直接删除 |
| 迁移/清理 v1 配置 | `config migrate --name <名称> [--lark-profile <profile>]`；旧配置缺 profile 时可用 flag 补充；混合格式填写一个现有名称以清理旧字段 |

用户通过对话点名切换时，先用 `config list` 核对名称，再执行精确的
`config use --name ...`。名称匹配不唯一或用户说法不能对应到一个名称时，列出
候选让用户选择，禁止猜测。切换成功后报告新的当前配置。若进程环境、`.env.local`
或 `.env` 存在更高优先级配置，命令会同时警告：全局当前配置已切换，但当前目录的
业务命令仍使用高优先级覆盖。

旧版全局平面变量不会自动迁移。发现旧格式时，业务命令拒绝访问令牌表，并要求
执行 `config migrate`。迁移会删除旧的五个资源字段和不具备表身份的
`SECRET_BOOK_IDS`，保留文件内其它内容。结构化配置非空并与旧字段并存时，同一命令
只清理旧字段，不改已有命名配置；空结构化配置与完整旧资源字段并存时创建第一套
命名配置；空结构化配置只与 `SECRET_BOOK_IDS` 并存时只删除该旧绑定。

本地配置和绑定采用原子替换。替换前失败表示没有写入；替换完成后目录 `fsync`
失败时，CLI 明确报告“本地写入结果不明”，调用方必须先读取对应文件核对，禁止
直接重放写命令。`run --bind` 在这种情况下仍返回已成功子命令的退出码，并要求先
运行 `bindings` 核对。

## 身份确认与运行前校验

`config save`、`config rebind`、`config migrate`、`init-create`、`init-adopt`
都使用两阶段确认：

1. 第一次运行只调用本机 `lark-cli profile list` 和
   `lark-cli auth status --json --profile <name>` 捕获实际身份，不访问令牌表，也不写配置。
2. CLI 以退出码 `3` 在 stdout 返回
   `secret-book.config-identity-confirmation/v1` JSON，包含
   `observed_identity` 和 `confirmation_token`。
3. 向用户明确展示 profile、应用 `app_id`、用户名和 `open_id`。用户确认这是要
   使用的身份后，用原命令追加 `--confirm-identity <token>` 重跑。
4. 重跑时若身份已变化，旧 token 自动失效，CLI 再次要求确认。

每个令牌表业务命令在 Base 调用前都会重新检查：profile 存在且已登录，实际
`appId/openId` 与配置内固定值完全一致。失败时不访问 Base，以退出码 `3` 返回
`secret-book.profile-guidance/v1` JSON。按其中 `config_write_target`、
`candidates` 和 `fix_actions` 恢复：

- 全局命名配置需要改绑身份时，使用 `config rebind`，再次经过两阶段确认。
- 项目配置仅缺两个身份固定值时，先向用户展示 `observed_identity`；确认后只把
  `SECRET_BOOK_FEISHU_APP_ID`、`SECRET_BOOK_FEISHU_USER_OPEN_ID` 写回
  `config_write_target.path` 指定的同一层，禁止写到其它层补齐。
- profile 未登录时，按 `fix_actions` 给出的 `lark-cli auth login --profile ...`
  完成登录后重试。禁止调用 `lark-cli profile use`，它会修改全局 active profile。

## 首次初始化或新增一套配置

先用 `lark-cli profile list` 确认候选 profile，并让用户确定三件事：配置名称、
要使用的 profile、创建新令牌表还是接管已有表。

- 新建：`init-create --lark-profile <profile> [--base-name 令牌表]`
- 接管：`init-adopt --url <多维表格 URL> --lark-profile <profile>`

两条命令第一次都进入身份确认。用户确认后追加 `--confirm-identity` 重跑。
`init-create` 创建 Base、`credentials` 表和 9 个字段；`init-adopt` 校验字段，缺列
会在全部已有字段校验通过后补建，类型不符时不创建任何字段并拒绝接管；
`visible_to` 必须是人员多选字段。成功输出中包含带完整 `uv run --project ...
scripts/secret_book.py` 前缀的可执行 `config save` 命令和已确认的 identity token；
与用户确认配置名称及表定位后执行该命令，即可保存，不需要再次确认同一身份。
身份若在两步之间变化，token 会失效并重新触发确认。

初始化和保存配置都不自动安装 agent 全局规则。若要安装，继续按“agent-rule”章节
单独取得用户确认。

## 配置读取优先级

业务命令每次只解析一次不可变配置快照。资源配置按整套选择：

1. 进程环境变量
2. `$PWD/.env.local`
3. `$PWD/.env`
4. 显式传 `--use-global-config` 后的全局当前配置

前三层如要覆盖全局，必须在同一层完整提供以下五个字段：

```text
SECRET_BOOK_APP_TOKEN
SECRET_BOOK_TABLE_ID
SECRET_BOOK_LARK_PROFILE
SECRET_BOOK_FEISHU_APP_ID
SECRET_BOOK_FEISHU_USER_OPEN_ID
```

某一高优先级层出现部分字段时立即报错，禁止从下一层逐字段拼接。

v1 的 `SECRET_BOOK_IDS` 只有记录 ID，没有所属令牌表身份。v2 检测到它时在查询
记录前退出码 `3`，要求删除该变量，并改用 `run --id ... --bind` 建立带
resource namespace 的自动绑定。禁止把裸 ID 自动归到当前配置。

## 令牌记录动作

| 动作 | 命令 | 可观察结果 |
|---|---|---|
| 保存 | `printf '%s\n' 'GITHUB_TOKEN=...' \| … save --name github-main --service github --purpose '主账号推送' --use-global-config` | stdin 接收 dotenv；输出记录名、`sec_` ID、键数量和键名 |
| 列表 | `… list --use-global-config` | 只读取并输出 id/name/service/account/purpose/expires_at，不读取 secret/notes |
| 查看一条 | `… get --name github-main --use-global-config` | 输出元数据、visible_to、notes 和键名，不输出值；一次只能指定一个 name 或 id |
| 执行 | `… run --id sec_xxx --use-global-config -- <命令>` | 把全部键值注入子进程环境，输出键名与命令，透传子进程退出码 |
| 执行并绑定 | `… run --id sec_xxx --bind --use-global-config -- <命令>` | 仅子进程退出码为 0 时保存自动绑定 |
| 自动执行 | `… run --auto --use-global-config -- <命令>` | 使用当前令牌表对应的历史绑定；无绑定、旧绑定或失效绑定退出 3 |
| 复制 | `… copy --name site-admin --key PASSWORD --use-global-config` | 值进入剪贴板，只输出键名和掩码值；一次只能指定一条记录 |
| 列绑定 | `… bindings` | 输出命名空间前 12 位、项目、命令、记录 ID、时间和次数 |
| 解绑 | `… unbind --command <命令名> --use-global-config` | 只解除当前解析出的令牌配置命名空间内的绑定 |

`run` 可重复 `--id` 合并多条记录，键名冲突时拒绝执行。`--auto` 与
`--name/--id` 互斥，`--auto` 与 `--bind` 也互斥。

payload 每行格式为 `KEY=value`，值是首个 `=` 后的原文，不去引号、不转义；
必须单行。SSH 私钥、证书等多行内容先由用户转为单行 base64，用时自行解码。

## 可见范围

`visible_to` 是令牌表中的人员多选字段：空值表示不限制，非空表示只有名单内用户
可取用。当前用户的 `open_id` 来自已经固定并验证的令牌配置。名单外记录在
list/get/run/copy 全部路径中不可见，没有绕过 flag。`save` 的名称查重例外地跨
全表执行，避免隐藏记录导致重名。旧表缺此列时视为不限制；执行一次 `init-adopt`
会补建。

## 自动绑定

自动绑定的键是 `(令牌表身份, 项目根, 命令名)`。其中令牌表身份由
`app_token + table_id + feishu_app_id + feishu_user_open_id` 的规范 JSON 计算
SHA-256；`bindings.json` 只保存哈希和其它元数据，不保存这四个原值或令牌值。
因此两套配置即使有相同记录 ID，也不会互相复用绑定。

存储路径为 `~/.config/secret-book/bindings.json`，schema version 为 2。
旧版条目没有令牌表身份，CLI 不猜归属、不查当前表、不自动删除，只提示重新绑定并
退出码 `3`。可用 `unbind --command <命令> --legacy` 删除 v1 条目；已无法由
配置引用的 v2 条目可按 `bindings` 显示的前缀执行
`unbind --command <命令> --namespace <前缀>`。删除配置或改绑到新身份时不自动
删除旧 namespace：其它全局配置或项目覆盖仍可能使用它；新配置不会命中旧条目，
确认不再使用后再显式清理。

## 缺配置兜底硬规则

任何命令或 skill 因缺少 API key、token 或账号配置而失败时：

1. 运行 `run --auto --use-global-config -- <原命令>`。命中即注入并重试；退出码
   `3` 且不是结构化身份 JSON时，进入下一步。
2. 运行 `list --use-global-config`，按 name/service/account/purpose 与用户意图
   匹配。唯一命中时执行
   `run --id <id> --bind --use-global-config -- <原命令>`；多条候选或无命中时
   列给用户选择，禁止自行选取。无记录且用户提供值时，询问是否用 `save` 保存。
3. 注入后仍出现鉴权失败，立即运行
   `unbind --command <命令名> --use-global-config`，再重新匹配。禁止用同一绑定重试。
4. MCP server 已在会话启动时运行，不能向其进程补注环境变量。用 `copy` 让用户
   配置并重启会话；目标支持多个配置作用域时先问具体作用域。令牌值不得上屏。

## 网络失败与退出码

每次 lark-cli 调用都有超时。幂等读取遇到瞬时网络失败最多尝试 3 次，退避
1 秒、2 秒；确定性错误不重试。飞书写接口没有幂等键，瞬时失败不重试，以退出码
`121` 报告“写入结果不明”。遇到 `121` 先用 `list` 或 `get` 核实，不要直接重放。

| 退出码 | 含义 |
|---|---|
| `0` | CLI 动作成功，或被包装命令成功 |
| `1` | 参数、配置、数据或确定性外部调用错误 |
| `3` | stdout 是身份确认/修复 JSON，或 `run --auto` 没有可用绑定；必须检查 stdout 再决定下一步 |
| `121` | 写请求遇到瞬时失败，结果可能已生效，禁止盲目重试 |
| 其它 | `run` 透传被包装命令的退出码 |

## agent-rule

`agent-rule` 检测各 agent 的全局指令文件；`agent-rule --install` 安装上面的兜底
规则，`--remove` 精确移除。规则块当前为 v3。

安装会修改用户全局文件。执行 `--install` 前，必须展示目标文件清单和规则全文，
取得明确确认。检测到用户手改的块时默认跳过，只有用户确认覆盖后才用 `--force`。
Cursor 没有可写的全局规则文件，CLI 只打印内容供用户手动配置。禁止从临时
worktree 安装全局规则；使用稳定 skill 检出路径。

## 边界（v2 非目标）

不做：加密、`export` 明文落盘、原生多行值、跨多张令牌表聚合查询、单条业务命令
临时选择非当前配置、自动按项目切换全局当前配置、agent 自动填表专用接口、
Notion 后端、到期提醒。到期提醒使用飞书多维表格原生自动化。
