# secret-book

**飞书令牌表 + agent 取用通道**：把 token、API key、账号密码、OSS/数据库连接参数
保存到你自己的飞书多维表格。Claude Code、Codex 等 agent 可以按用途或精确 ID
查找令牌，把值注入子进程或送入剪贴板，值不会显示在终端输出中。

本机可以保存多套有名称的令牌配置，例如“工作”“个人”。任何时刻只有一套
当前配置，也就是默认配置；你可以直接在对话中说“默认设置改为个人”或“切换到
工作配置”，agent 会核对名称并持久切换。

> 本仓库原名 `cred-ledger`，2026-08-10 更名为 `secret-book`。

## 使用前请确认

- **令牌以明文保存在飞书多维表格中，不加密。这不是密码管理器。**
- 安全边界由你的飞书租户和令牌表权限决定。平台方与任何有表格权限的人都能看到
  数据；飞书多维表格保留 180 天历史记录，删除后另有 30 天回收站。
- 这里只适合保存可随时轮换的中低价值令牌，并应把令牌表权限收紧到仅本人。
  高价值令牌应使用 1Password 等专业密码管理器。
- secret-book 的目标是让数据留在你自己的飞书租户，同时减少 agent 反复向你索要
  token 的过程；它不提供端到端加密。

## 核心概念

- **令牌表**：一张飞书多维表格，保存多条令牌记录。
- **令牌记录**：表中的一行，可以包含一个 token，也可以包含一组需要同时使用的
  dotenv 键值，例如 OSS 的 AK、SK、Endpoint 和 Bucket。
- **令牌配置**：本机访问一张令牌表所需的 `app_token`、`table_id`、lark-cli
  profile 和固定身份。全局可保存多套。
- **当前配置 / 默认配置**：全局多套令牌配置中由 `active_id` 唯一选中的一套。
  两者是同一个概念，在 secret-book 令牌配置语境中也称“默认设置”。业务命令带
  `--use-global-config` 且没有进程环境或项目配置覆盖时使用它。

每套令牌配置只对应一张令牌表和一个经过确认的飞书身份。切换配置不会修改
lark-cli 的 active profile；每次访问都显式传入该配置自己的 profile。

## 快速开始

前置条件：macOS 或 Linux，已安装并登录 [lark-cli](https://open.feishu.cn/)，并安装
[uv](https://docs.astral.sh/uv/) >= 0.8。当前运行时路径和文件锁依赖 POSIX API，
不支持 Windows。以下命令都在仓库根目录执行。

### 1. 创建第一套配置

查看本机已有的 lark-cli profile：

```bash
lark-cli profile list
```

创建一张新令牌表：

```bash
uv run --project . scripts/secret_book.py init-create \
  --lark-profile work-profile
```

第一次运行不会建表，而会退出 `3` 并输出身份确认 JSON。确认其中的 profile、
`app_id`、用户和 `open_id` 后，把 `confirmation_token` 原样加回命令：

```bash
uv run --project . scripts/secret_book.py init-create \
  --lark-profile work-profile \
  --confirm-identity <confirmation_token>
```

建表成功后，CLI 会输出一条带 `uv run --project ... scripts/secret_book.py` 前缀、
可从任意目录直接执行的 `config save` 命令。把其中的 `<名称>` 替换为“工作”等
唯一名称并执行，即保存第一套配置；第一套会自动成为当前配置。

如果已有符合结构的飞书表，改用：

```bash
uv run --project . scripts/secret_book.py init-adopt \
  --url <多维表格 URL> \
  --lark-profile work-profile
```

它使用相同的身份确认流程，先校验全部已有字段，再补建缺失字段；字段类型不符时
不会创建任何字段并拒绝接管。`visible_to` 必须是人员多选字段。

### 2. 新增和切换配置

新增第二套配置的初始化流程相同。也可以在已经知道表定位时直接保存：

```bash
uv run --project . scripts/secret_book.py config save \
  --name 个人 \
  --app-token <base_token> \
  --table-id <table_id> \
  --lark-profile personal-profile
```

直接保存也会先输出身份确认 JSON，确认后追加 `--confirm-identity` 重跑。

```bash
# 查看所有配置；不会显示表 token、table_id、appId 或 openId
uv run --project . scripts/secret_book.py config list

# 持久切换默认配置（当前配置）
uv run --project . scripts/secret_book.py config use --name 个人
```

“默认设置改为个人”“把默认配置设为个人”“以后默认用个人”和“切换当前配置到
个人”都对应上面的 `config use --name 个人`。agent 先用 `config list` 核对名称，
唯一匹配后执行切换，再回读确认；找不到名称时列出已有配置供选择。
当前项由“当前配置”列的“是”标记识别，与列表顺序无关。目标已经是当前项时，
agent 会报告“默认配置（当前配置）已经是个人”，不会声称从另一套配置切换过来。
`config list` 查看本机配置，`list` 查询飞书令牌记录，两者用途不同。

`config use` 只修改本机配置文件，不访问飞书，也不调用 `lark-cli profile use`。
全部命名配置、表定位和身份固定值都会保留，只有 `active_id` 改为所选配置的 ID。
当前目录若有更高优先级的项目配置，切换仍会成功，但 CLI 会明确警告该目录的业务
命令仍使用项目配置。

### 3. 保存和使用令牌记录

```bash
# payload 从 stdin 进入，不把值作为命令参数
printf '%s\n' 'GITHUB_TOKEN=ghp_xxx' | \
  uv run --project . scripts/secret_book.py save \
  --name github-main \
  --service github \
  --purpose '主账号推送' \
  --use-global-config

# 只列元数据，不读取 secret/notes
uv run --project . scripts/secret_book.py list --use-global-config

# 把值注入子进程环境后执行；值不上屏
uv run --project . scripts/secret_book.py run \
  --name github-main \
  --use-global-config \
  -- git push origin main

# 把单个值写入剪贴板；输出只显示掩码
uv run --project . scripts/secret_book.py copy \
  --name site-admin \
  --key PASSWORD \
  --use-global-config
```

## 管理本地令牌配置

全局配置固定存放在 `~/.config/secret-book/.env`。所有管理命令都直接操作这个文件，
不需要也不接受 `--use-global-config`。

| 操作 | 命令 | 行为 |
|---|---|---|
| 查看 | `config list` | 显示当前标记、稳定 cfg ID、名称和 lark-cli profile |
| 保存 | `config save --name ... --app-token ... --table-id ... --lark-profile ...` | 名称必须唯一；后续新增不改变当前配置 |
| 切换默认配置（当前配置） | `config use --name ...` | 只更新唯一的 `active_id`，保留全部配置 |
| 更新身份 | `config rebind --name ... --lark-profile ...` | 保留 cfg ID、表定位、名称和当前状态，重新确认并写入身份 |
| 重命名 | `config rename --name ... --new-name ...` | 稳定 cfg ID 不变 |
| 删除 | `config remove --name ...` | 有多套时先切走当前配置；最后一套可以直接删除 |
| 迁移/清理 v1 | `config migrate --name ...` | 迁移旧平面配置；混合格式时填写现有名称以清理旧字段 |

旧平面配置不会自动转换。业务命令发现旧格式时会停止并提示运行 `config migrate`。
迁移保留注释、`AUTO_UPDATE_CHECK` 和未知键，删除已经被结构化配置替代的五个
旧资源字段以及没有令牌表身份的 `SECRET_BOOK_IDS`。结构化配置和旧字段并存时，
结构化配置非空时，同一命令只清理旧字段，不改变已有命名配置；空结构化配置与完整
旧资源字段并存时，命令会创建第一套命名配置；空结构化配置只与
`SECRET_BOOK_IDS` 并存时，命令只删除这个不具备表身份的旧绑定。

多套配置保存在同一个结构化环境变量中：

```dotenv
SECRET_BOOK_CONFIGS_JSON='{"schema_version":1,"active_id":"cfg_xxxxxxxxxx","configs":{"cfg_xxxxxxxxxx":{"name":"工作","app_token":"<base_token>","table_id":"<table_id>","lark_profile":"work-profile","feishu_app_id":"<app_id>","feishu_user_open_id":"<open_id>"}}}'
```

脚本用文件锁和原子替换处理并发更新，最终文件权限为 `0600`。配置非空时
`active_id` 必须指向且只指向一个配置，它同时决定当前配置和默认配置。建议只通过
`config` 子命令修改，不要手工编辑这段 JSON。若文件已经替换、但目录 `fsync`
失败，CLI 会明确报告“本地写入结果不明”；此时先读取配置核对结果，不要直接重放
写命令。

## 配置优先级与项目覆盖

令牌表业务命令按以下优先级选择**第一套完整配置**：

1. 进程环境变量
2. `$PWD/.env.local`
3. `$PWD/.env`
4. 仅在传入 `--use-global-config` 时，读取全局默认配置（当前配置）

不带 `--use-global-config` 时，业务命令不会读取全局默认配置；即使前三层均未
配置，也不会自动回退到它。`config list/use` 直接管理全局文件，无需这个 flag，
但这不改变业务命令的读取条件。

前三层如要覆盖全局，必须在同一层同时提供：

```dotenv
SECRET_BOOK_APP_TOKEN=<base_token>
SECRET_BOOK_TABLE_ID=<table_id>
SECRET_BOOK_LARK_PROFILE=<profile>
SECRET_BOOK_FEISHU_APP_ID=<app_id>
SECRET_BOOK_FEISHU_USER_OPEN_ID=<open_id>
```

如果高优先级层只出现部分字段，CLI 直接报错，不会从下一层补齐。这能避免表定位、
profile 和用户身份来自不同配置。

v1 的 `SECRET_BOOK_IDS=sec_xxx,sec_yyy` 没有记录 ID 所属的令牌表身份。切换配置
后继续使用它可能从另一张表取到同 ID 记录，因此 v2 在查询 Base 记录前拒绝这种
绑定并退出 `3`。删除该变量，然后使用 `run --id ... --bind` 建立带令牌表身份的
自动绑定。

向 Git 工作树中的 `.env.local` 写入前，应先确认它没有被 Git 跟踪且确实被忽略。

## 旧版安装无法读取多配置时

同一用户的 Claude Code、Codex、WorkBuddy 等 Agent 可能各有一份 Skill 安装，
但共用 `~/.config/secret-book/.env`。v1.2.0 只读取旧平面变量，无法读取 v2 的
`SECRET_BOOK_CONFIGS_JSON`。配置内的 `schema_version` 是格式版本，不是 Skill
版本；不能仅凭旧脚本报“缺少配置”，就认定该文件无效或不是脚本生成的。

这时应先确认目标 Agent 实际加载的 `SKILL.md`、脚本路径、版本和更新来源，再将
该安装同步到支持现有格式的版本。更新检查没有提示新版本，不代表当前安装已兼容；
新版可能尚未发布，检查也可能因节流或网络问题跳过。同步后在该 Agent 的新会话中
执行 `config list`，确认全部配置可读，再执行原来的切换命令并回读验证。

切换默认配置不会要求降级配置格式。不要把多配置 JSON 改回平面变量、只保留目标
配置、把其它配置放进注释，或另写平面覆盖来适配旧脚本；单独改 `active_id` 也不能
让旧脚本读懂新格式。暂时无法更新时，应保留完整配置并报告版本阻塞。
升级共享配置格式前，还应确认其它共用该文件的已知安装也支持新格式。

## 身份固定值校验

保存、改绑、迁移和初始化都先返回
`secret-book.config-identity-confirmation/v1` JSON，要求确认 lark-cli profile 实际
对应的应用和用户。确认 token 只对这组身份有效；身份变化后不能复用。

每次执行 save/list/get/run/copy 等令牌表业务命令前，CLI 都先执行：

```text
lark-cli profile list
lark-cli auth status --json --profile <配置中的 profile>
```

只有 profile 存在、登录有效、实际 `appId/openId` 与配置固定值一致时才访问 Base。
否则退出 `3`，在 stdout 返回 `secret-book.profile-guidance/v1` JSON，不会发送 Base
请求。全局命名配置可用 `config rebind` 在用户确认后更新；CLI 从不修改 lark-cli
的全局 active profile。

## 自动绑定

```bash
# 命令成功后建立绑定
uv run --project . scripts/secret_book.py run \
  --id sec_xxx --bind --use-global-config -- git push origin main

# 下次在同一个项目、同一个命令、同一张令牌表身份下直接复用
uv run --project . scripts/secret_book.py run \
  --auto --use-global-config -- git push origin main
```

绑定的完整键是 `(令牌表身份, 项目根, 命令名)`。令牌表身份由 `app_token`、
`table_id`、`feishu_app_id`、`feishu_user_open_id` 计算 SHA-256；
`~/.config/secret-book/bindings.json` 只保存哈希、路径、命令和记录 ID，不保存这些
原值或令牌值。因此切换配置后不会误用另一张表的同名或同 ID 记录。

`bindings` 列出所有绑定。删除配置或用 `config rebind` 改变身份时不会删除旧
namespace 的绑定，因为其它全局配置或项目覆盖仍可能使用同一 namespace。旧绑定
不会被新配置命中；确认它已不再使用后，从 `bindings` 输出取得 namespace 前缀，
再显式清理。解绑全局当前配置对应的条目：

```bash
uv run --project . scripts/secret_book.py unbind \
  --command git \
  --use-global-config
```

旧版绑定没有令牌表身份，v2 不猜归属、不查询当前表，也不自动删除；`run --auto`
会提示重新绑定并退出 `3`。

删除 v1 旧绑定可运行
`unbind --command git --legacy`。删除已无法由现有配置引用的 v2 绑定时，从
`bindings` 输出取得前缀，再运行
`unbind --command git --namespace <namespace-prefix>`。

## 退出码

| 退出码 | 含义 |
|---|---|
| `0` | CLI 或被包装命令成功 |
| `1` | 参数、配置、数据或确定性外部调用错误 |
| `3` | stdout 是身份确认/修复 JSON，或 `run --auto` 没有可用绑定；调用方必须检查 stdout |
| `121` | 飞书写请求遇到瞬时失败，结果可能已经生效，禁止直接重试 |
| 其它 | `run` 透传被包装命令的退出码 |

幂等读取遇到瞬时网络失败最多尝试 3 次。写请求没有幂等键，不自动重试；退出
`121` 后先用 `list` 或 `get` 核实结果。本地原子写在替换后无法确认目录同步时仍
退出 `1`，但错误会明确写“本地写入结果不明”，应先读取对应配置文件核对。

## Agent Skill 安装

本仓库使用 [Agent Skills](https://agentskills.io) 格式，Claude Code 与 Codex 共用
同一份 `SKILL.md`。clone 后分别建立指向仓库实体的 symlink：

```bash
ln -s "$(pwd)" ~/.claude/skills/secret-book
ln -s "$(pwd)" ~/.agents/skills/secret-book
```

`agent-rule --install` 可以把“命令缺少令牌时先尝试 secret-book”的规则写入已检测到
的 agent 全局指令文件。它会修改用户文件，执行前必须先查看目标和规则全文并明确
确认；`agent-rule --remove` 可精确移除。不要从临时 worktree 安装全局规则。

## 令牌表结构

初始化创建或校验 9 列：

`id` · `name` · `service` · `account` · `purpose` · `secret` · `expires_at` ·
`notes` · `visible_to`

`id` 是 `sec_` 加 10 位随机字符的稳定机器键。`secret` 使用 dotenv 格式。
`visible_to` 为空表示不限制，非空表示只有名单内用户可取用；名单外记录在
list/get/run/copy 中都不可见。直接在表中新增且没有 `id` 的行，会在后续读取时
自动补写 ID。

<!-- release-table:begin -->
| 目标 | 版本 | Release |
|---|---|---|
| secret-book | 2.0.1 | [v2.0.1](https://github.com/cookaihq/secret-book/releases/tag/v2.0.1) |
<!-- release-table:end -->
