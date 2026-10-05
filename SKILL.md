---
name: secret-book
version: 2.4.0
description: >-
  v2.4.0｜令牌：把 token、API key、账号密码、OSS/数据库等配置组保存到用户自己的
  飞书令牌表里，agent 按意图或精确 ID 查询取用，取用输出一律掩码；本机可保存多套
  有名称的令牌配置，并持久切换唯一的当前配置（默认配置）。当用户说
  "存一下这个 token/API key/密钥/凭证"、"用我存的 xx 推送/登录/调用"、"我的
  OSS/数据库配置"、"切换到工作配置"、"查看当前配置/默认配置"、"默认设置改为个人"、
  "令牌/secret book"，或遇到 secret-book 安装版本与配置格式不兼容，
  或命令、Skill、Plugin 缺少配置或配置被拒绝，用户选择从表中取用并保存/修复本机配置时使用。Credential
  storage in the user's own Feishu Bitable: save/list/run/copy tokens, API keys,
  logins and config groups; stores multiple named local configurations with one
  active (default) configuration; confirms field mappings and writes consumer configuration to the effective local files; also supports explicit temporary injection.
  Requires lark-cli logged in. Do NOT use for encrypted vault needs: this skill
  stores plaintext; point users to a real password manager for high-value secrets.
compatibility: 支持 macOS、Linux 和原生 Windows；需要当前环境可执行且已登录的 lark-cli（user 身份）、uv >= 0.8（运行时由 uv 管理）及飞书网络；Windows 使用 PowerShell、支持 DACL 的文件系统；Claude Code 与 Codex 共用入口，宿主实际发现与加载须分别验证
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
  正常配置通过 `configure` 从脚本内存写入已确认文件；仅本轮等例外使用 `run`，
  手工粘贴使用 `copy`。禁止用 `run` 包装任意写文件命令绕过来源、确认和 Git 检查。
- 用户让 agent 在浏览器中自动填写令牌时，值会进入会话记录；执行前明确告知。

## 第 0 步：自动检查更新

每次进入正式流程前，先确认当前安装形态和实际执行环境（Windows 原生与 WSL 分开）。Git 检出在 macOS/Linux/WSL 使用 Bash：

```bash
scripts/check_update.sh
```

原生 Windows 使用 PowerShell，不要求 Bash 或 WSL：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SkillDir/scripts/check_update.ps1"
```

- 退出码 `0`：直接继续，不复述输出。
- 退出码 `10`：原样转述报告并询问是否拉取。用户确认后运行
  Bash 的 `scripts/check_update.sh --pull` 或 PowerShell 的 `scripts/check_update.ps1 -Pull`；用户拒绝、未回应或拉取失败时，继续使用当前版本。
- 用户要求关闭时，只在 `~/.config/secret-book/.env` 写入
  `AUTO_UPDATE_CHECK=0`，保留文件内其它内容。

检查更新失败不能阻塞用户当前任务。该脚本直接运行，不经过 uv。

复制安装按随包 `update.json` 查询 stable tag 及其 commit SHA，展示本地/远端版本、来源、tag、SHA 和目标目录。用户确认后才能按该 SHA 下载到临时目录，校验名称、版本与 description 前缀，再原子替换并回读验证；失败保留或恢复旧目录。缺少可验证的查询或安装能力时报告 `not-applicable` 并继续，不把 Git 检查跳过解释成“已是最新”。

## 命令入口

依赖：`lark-cli` 已安装并完成 user 身份登录，`uv >= 0.8`。所有 Python 命令统一用：

```bash
uv run --project "$SKILL_DIR" "$SKILL_DIR/scripts/secret_book.py" <action> [flags]
```

禁止把示例改成裸 `python3`。脚本虽有运行时 bootstrap，调用方仍必须显式使用
skill 自带的 uv 项目。

Windows PowerShell 将 `$SkillDir` 设为当前加载的 Skill 实体目录，入口相同：

```powershell
uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" <action> [flags]
```

Windows 解释器固定到 `.venv/Scripts/python.exe`；配置个人目录来自 Python 的 `Path.home()`（通常为 `%USERPROFILE%`），不从 `%APPDATA%` 或 WSL 读取。管道使用 UTF-8；Windows PowerShell 5.1 发送中文 stdin 前设 `$OutputEncoding = [System.Text.UTF8Encoding]::new($false)`，值仍仅经 stdin。脚本支持 UTF-8 BOM 的 dotenv 和业务声明文件。`run` 直接启动实际可执行程序，PowerShell cmdlet 须显式通过 `powershell.exe`/`pwsh` 调用，不能把引号或 shell 表达式当作可执行文件名。Windows `copy` 用 `clip.exe` 接收 Unicode stdin。

## 使用前检查当前 Agent 规则

自动检查更新后，按当前会话确定宿主，运行 `agent-rule --agent <当前Agent>` 只读检查。
不能以配置目录存在推断宿主。核对已有 secret-book 说明、旧自动注入规则、项目覆盖、
禁用与 symlink 共用实体；脚本标记手工规则或未知加载状态时继续审查。
检查不授权修改全局规则，规则文件不保存业务密钥。具体入口、状态、安装授权及宿主
限制见 [Agent 规则检查](references/agent-rules.md)。

## 为其他 Skill / Plugin 配置凭证

用户选择 secret-book 后，先读 [业务配置流程](references/consumer-setup.md)。业务方提供
真实配置声明、自己的来源检查入口和错误依据；本 Skill 负责自身初始化、身份和表、
候选记录、实际 key→业务字段映射、目标文件确认以及写入回读。

能持久保存时优先建议写入本机。修复已有错误必须写回实际生效的原文件/原字段，
不能另写全局文件掩盖高优先级错误；环境变量先定位启动/注入来源。首次新增沿用既定
位置，无决定才建议个人全局。`configure` 先预览，再凭用户确认的 token 写入；
`configure-status` 可恢复待确认请求。写完让业务方直接读自己的配置并验证，正常运行
不访问 secret-book，不自动同步表中轮换。值不进入聊天、argv 或规则文件。

用户选择手填文件时不查表，也不要求安装 secret-book。`run --requirements` 已移除；
旧命令绑定只用于用户明确的临时取用场景，不能推断成 Plugin 配置。

## 本地令牌配置

一套令牌配置负责定位一张飞书令牌表，并固定访问该表时允许使用的飞书身份。
全局文件 `~/.config/secret-book/.env` 可保存多套有名称的配置，但任何时刻只有
一套“当前配置”，它就是“默认配置”。在 secret-book 令牌配置语境中，“默认设置”
也指这套配置。三种说法都对应 `SECRET_BOOK_CONFIGS_JSON.active_id` 选中的同一项，
没有第二个默认项；`config list` 的“当前配置”标记就是默认配置标记。
按当前配置查表的命令只有显式带 `--use-global-config` 才会启用这一层；进程环境变量和当前目录
的 `.env.secret-book` / `.env.local` / `.env` 仍有更高优先级。
也可用 `--config-name <名称>` 明确选择一套完整的全局命名配置，跳过进程和项目来源，不改变当前项。

全局文件使用一个结构化值：

```dotenv
SECRET_BOOK_CONFIGS_JSON='{"schema_version":1,"active_id":"cfg_xxxxxxxxxx","configs":{"cfg_xxxxxxxxxx":{"name":"工作","app_token":"<base_token>","table_id":"<table_id>","lark_profile":"<profile>","feishu_app_id":"<app_id>","feishu_user_open_id":"<open_id>"}}}'
```

每套配置原子包含：`name`、`app_token`、`table_id`、`lark_profile`、
`feishu_app_id`、`feishu_user_open_id`。`cfg_` ID 稳定不变；名称必须唯一。
脚本通过文件锁、同目录临时文件、刷新和原子替换更新文件，仅允许当前用户访问（Windows 另允许 SYSTEM），并保留注释、`AUTO_UPDATE_CHECK` 和未知键。不要手工编辑 JSON；使用：

| 用户意图 | 命令与结果 |
|---|---|
| 查看当前配置 / 默认配置 | `config list`：只显示当前标记、cfg ID、名称、lark-cli profile |
| 新增配置 | `config save --name <名称> --app-token <token> --table-id <id> --lark-profile <profile>`；第一套自动成为当前配置，后续新增不切换 |
| 切换当前配置 / 默认配置 | `config use --name <名称>`：只修改本地 `active_id`，不调用 lark-cli，不修改其 active profile |
| 更新飞书身份 | `config rebind --name <名称> --lark-profile <profile>`：保留 cfg ID、表定位、名称和当前状态 |
| 重命名 | `config rename --name <旧名称> --new-name <新名称>`：cfg ID 不变 |
| 删除 | `config remove --name <名称>`；有多套时不能删除当前配置，先切换；最后一套可直接删除 |
| 迁移/清理 v1 配置 | `config migrate --name <名称> [--lark-profile <profile>]`；旧配置缺 profile 时可用 flag 补充；混合格式填写一个现有名称以清理旧字段 |

用户说“默认设置改为 xxx”“把默认配置设为 xxx”“以后默认用 xxx”或“切换当前
配置到 xxx”，都表示切换到名称为 `xxx` 的已有令牌配置，按以下顺序执行：

1. 用 `config list` 核对配置名称。不要用 `list` 代替：`list` 查询飞书令牌记录，
   `config list` 才读取本机配置列表。当前项是“当前配置”列标记为“是”的那一行，
   不一定是第一行；记录该行的 cfg ID 和名称，不按列表顺序推断。
2. 名称精确对应一套时，直接执行 `config use --name <名称>`。名称不存在或不能
   唯一对应时，列出候选让用户选择；不要自行创建配置或切换 lark-cli 的 active profile。
3. 成功后再次执行 `config list` 回读，核对“是”所在行的 cfg ID 和名称，报告
   “默认配置（当前配置）：<名称>”。若执行前已经是目标配置，报告“默认配置
   （当前配置）已经是 <名称>”；不要虚构“之前是另一套配置”。
   若命令警告进程环境、`.env.secret-book`、`.env.local` 或 `.env` 存在覆盖，同时说明覆盖来源和当前
   目录的业务命令实际使用的配置层。命令失败时按实际错误反馈，不报告切换成功。

切换会保留全部命名配置、表定位和身份固定值，只持久更新 `active_id`。明确的
切换请求不需要用户再选择如何改写存储格式。

切换结果以回读为准，回复只需包含已核实的当前名称及必要的覆盖警告。用户询问
后续取用方式时，按具体命令说明，不将其它命令的行为类推过来：

- `config list/use` 直接管理全局配置文件，不需要也不接受 `--use-global-config`。
- `list/get/save/copy` 与旧 `run --id/--name/--auto` 读取全局默认配置时，必须显式带 `--use-global-config`。
  不带 flag 时，即使进程和项目都没有配置，也不会回退到全局默认配置。
- `list/get/save/copy/configure` 与旧 `run` 可用 `--config-name <名称>` 直接选择完整命名配置；不修改当前项，不与进程/项目字段拼接。该显式选择不需要 `--use-global-config`。
- `bindings` 只读取本地 `bindings.json`，不解析令牌配置，不接受这个 flag，也不
  访问飞书令牌表。
- `unbind` 按全局默认配置选择绑定时需要这个 flag；指定 `--namespace` 或
  `--legacy` 时不解析令牌配置。它只删除本地绑定，不发起令牌表请求。

`configure` 与普通查表命令使用相同的表选择规则；业务文件写入后独立使用，不再保留运行时表绑定。具体流程见 [业务配置流程](references/consumer-setup.md)。

旧版全局平面变量不会自动迁移。发现旧格式时，业务命令拒绝访问令牌表，并要求
执行 `config migrate`。迁移会删除旧的五个资源字段和不具备表身份的
`SECRET_BOOK_IDS`，保留文件内其它内容。结构化配置非空并与旧字段并存时，同一命令
只清理旧字段，不改已有命名配置；空结构化配置与完整旧资源字段并存时创建第一套
命名配置；空结构化配置只与 `SECRET_BOOK_IDS` 并存时只删除该旧绑定。

本地配置和绑定采用原子替换。macOS/Linux 使用 `0600` 文件权限；Windows 在写入值前设置并校验仅当前用户与 SYSTEM 可访问的 DACL，无法设置时拒绝写入。已有业务项目目录不改权限。Windows 使用字节范围锁，临时文件先刷新再替换并回读内容和 ACL；POSIX 保留目录 `fsync`。不承诺断电后的目录持久性。
替换前失败表示没有写入；替换完成后同步或回读失败时，CLI 明确报告“本地写入结果不明”，调用方必须先读取对应文件核对，禁止
直接重放写命令。`run --bind` 在这种情况下仍返回已成功子命令的退出码，并要求先
运行 `bindings` 核对。

## 版本与配置格式不兼容时

当前安装不认识 `config list/use`，或报缺配置但本机已有 `SECRET_BOOK_CONFIGS_JSON`
时，先核查实际调用的 Skill 版本和入口，不要据此认定配置无效或不是脚本生成的。
配置内的 `schema_version` 是配置格式版本，不等于 Skill 版本号。

1. 确认当前 Agent 实际加载的 `SKILL.md` 和执行的 `scripts/secret_book.py` 的路径，
   核对两者属于同一安装目录，并读取该安装的版本和命令帮助。同一用户的多个 Agent
   可能使用不同安装副本，却共用 `~/.config/secret-book/.env`。
2. 查清该安装的更新来源及可用版本，按已获授权的更新流程同步到支持现有格式的
   版本。更新检查退出 `0` 可能只是节流、网络失败或远端没有新版，不能证明格式兼容；
   另一份本地源码较新也不代表当前 Agent 已更新。
3. 更新后，在目标 Agent 的新会话中用同一安装的 CLI 执行 `config list`，确认能读取
   全部已有配置，再执行原来的 `config use --name <名称>` 并回读结果。只修改
   `active_id` 不能让旧脚本获得读取新格式的能力。

禁止为适配旧脚本把多配置 JSON 改回 v1 平面变量、只保留目标配置、把其它配置放入
注释，或另写平面覆盖配置绕过新格式；也不要把这些做法列为普通切换选项。没有可用的
兼容版本、安装存在未处理的修改或同步失败时，保留原配置，明确报告版本阻塞及所需
更新操作。升级共享配置格式前，应核对共用该文件的其它已知安装也能读取新格式。

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
`secret-book.profile-guidance/v2` JSON。按其中 `config_write_target`、
`candidates` 和 `fix_actions` 恢复。所有授权请求和续接命令都必须显式传入同一个
目标 profile；禁止调用 `lark-cli profile use`，也不能用全局 active profile 代替配置中的
profile。

- 全局命名配置需要改绑身份时，使用 `config rebind`，再次经过两阶段确认。
- 项目配置仅缺两个身份固定值时，先向用户展示 `observed_identity`；确认后只把
  `SECRET_BOOK_FEISHU_APP_ID`、`SECRET_BOOK_FEISHU_USER_OPEN_ID` 写回
  `config_write_target.path` 指定的同一层，禁止写到其它层补齐。
- profile 未登录或身份不匹配时，按 `fix_actions` 中 `kind` 为 `auth_split_flow` 的动作
  完成登录后重试。这个动作的 `profile`、`start_argv_template`、
  `resume_argv_template` 和 `status_argv` 是同一轮授权的机器可读约束；不要把它替换成
  阻塞式裸 `auth login`。

### profile 授权的 split-flow

`auth_split_flow` 必须按下面的阶段推进，用户文字本身不能证明本机登录已成功：

1. `AUTH_REQUEST_CREATED`：从动作的 `profile` 确定目标 profile。若该字段是
   `<profile-name>`，先选定一个 profile 名称，并把它固定用于本轮所有命令。执行
   `start_argv_template`，即 `lark-cli auth login --profile <profile> --domain base
   --no-wait --json`。如果上游错误返回明确的 `missing_scopes`，只请求本次操作需要的
   最小 scope。
2. 从 JSON 中读取 `verification_url`、`device_code`、`expires_in`。使用动作里的
   `qrcode_argv_template` 生成临时 PNG，输出路径必须是当前工作目录下的相对路径。按模板
   执行命令：`lark-cli auth qrcode <verification-url> --profile <profile> --output <temporary-qr-path>`。
   先把 verification URL 原样提供给用户，
   再展示二维码，展示完成后删除临时文件。只向用户展示目标 profile、目标账户、授权
   范围和过期信息；`device_code` 只保留在当前任务的短期运行上下文中，不得写入回复、
   日志、配置、Issue、记忆或测试产物。展示 URL 和二维码后结束当前轮，等待用户回来确认。
3. `USER_AUTHORIZED`：用户回复“已授权”时，先确认当前上下文仍有这次未消费且未过期的
   授权请求。不得再次执行 `--no-wait`，也不得创建第二个 URL；把
   `resume_argv_template` 中的 `<current-device-code>` 替换为原 code，执行
   `lark-cli auth login --profile <profile> --device-code <current-device-code> --json`。
4. `LOCAL_CREDENTIALS_CONFIRMED`：续接命令成功后，执行动作中的 `status_argv`，即
   `lark-cli auth status --json --profile <profile>`，确认 user identity ready。仅有
   网页显示“授权完成”不能跳过这一步。
5. `SECRET_BOOK_IDENTITY_CONFIRMED`：再次确认配置绑定的 profile、`app_id` 和
   `user_open_id` 与本机实际身份完全一致。
6. `SECRET_BOOK_OPERATION_ALLOWED`：只有到达该状态，才允许执行 `list`、`get`、`run`、
   `copy`、`save` 等令牌表操作。

续接出现 `device_code is invalid` 或其它失败时，先按动作的 `failure_diagnostics` 检查
当前 profile 登录状态、发起与续接是否使用同一 profile、期间是否发生 CLI/profile 切换、
CLI 版本和授权请求生命周期。不得连续盲目重试，也不得立即重新生成授权请求。

只有原 code 明确过期、明确被服务端作废，或用户明确要求重新授权，并且用户明确确认这次
新的授权请求后，才允许重新执行 `start_argv_template`；同一任务最多产生一次新的请求，
新请求创建成功后立即丢弃旧 code。当前任务上下文丢失 code 时，不得猜测、从文件读取或
自动生成新请求；停止并说明无法安全续接，等待用户明确要求重新授权。

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

查表命令及 `configure` 每次只解析一次不可变配置快照。资源配置按整套选择：

1. 进程环境变量
2. `$PWD/.env.secret-book`
3. `$PWD/.env.local`
4. `$PWD/.env`
5. 显式传 `--use-global-config` 后的全局默认配置（当前配置）

前四层如要覆盖全局，必须在同一层完整提供以下五个字段：

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

## 缺配置或配置被拒绝时

先让调用者报告字段、实际来源和证据，区分配置问题与网络、余额、权限、限流等错误。
提供“修改本机配置 / 从 secret-book 选择配置修复”，已有明确选择则沿用。选择手填时
不查表。选择 secret-book 时按 [业务配置流程](references/consumer-setup.md) 准备版本、
飞书身份和令牌表；新用户可以中途改为手填，无记录或缺项时让其在自己的表或文件中填写。

展示真实记录/账号、表中 key、映射、准确写入文件和替换项，唯一候选首次也确认。
修复原来源；新增才决定保存位置。业务配置可持久保存时，不把每次 `run` 取值当作默认。
MCP 等已经读取旧配置的进程按其机制重新加载或重启，业务方再检查实际来源；保存成功
不等于鉴权成功，也不授权重发原业务任务。

## 网络失败与退出码

每次 lark-cli 调用都有超时。幂等读取遇到瞬时网络失败最多尝试 3 次，退避
1 秒、2 秒；确定性错误不重试。飞书写接口没有幂等键，瞬时失败不重试，以退出码
`121` 报告“写入结果不明”。遇到 `121` 先用 `list` 或 `get` 核实，不要直接重放。

| 退出码 | 含义 |
|---|---|
| `0` | CLI 动作成功，或被包装命令成功 |
| `1` | 参数、配置、数据或确定性外部调用错误 |
| `3` | stdout 是身份或业务接入的确认/修复 JSON，或旧 `run --auto` 没有可用绑定；按 schema/status 判断，不把待确认当成执行成功 |
| `121` | 写请求遇到瞬时失败，结果可能已生效，禁止盲目重试 |
| 其它 | `run` 透传被包装命令的退出码 |

## agent-rule

规则块当前为 v5，主流程见 [Agent 规则检查](references/agent-rules.md)。
`agent-rule --agent <当前Agent>` 只读；`--all` 仅显式盘点。`--install` / `--remove`
必须指定 Agent，并按用户明确的规则修改授权操作。先展示路径和完整规则；手工修改
默认不覆盖。写入不含开发 worktree 路径，真实会话加载仍需宿主验证。

## 边界（v2 非目标）

不做：加密、通用明文导出（仅支持已确认的业务配置字段写入）、原生多行值、跨多张令牌表聚合查询、自动按项目切换全局当前配置、agent 自动填表专用接口、
Notion 后端、到期提醒。到期提醒使用飞书多维表格原生自动化。
