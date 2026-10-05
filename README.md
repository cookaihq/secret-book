# secret-book

让 Claude Code、Codex 等 Agent 从你自己的飞书多维表格中查找凭证，确认后写入调用者的本机配置；也支持明确的临时命令取用和剪贴板。secret-book 自身只显示记录元数据、键名和掩码，
不会把凭证值打印到终端。

## 使用前先确认

- **凭证以明文保存在飞书多维表格中，不加密。secret-book 不是密码管理器。**
- 飞书平台方和任何拥有表格权限的人都能看到数据。飞书多维表格保留 180 天历史
  记录，删除后另有 30 天回收站。
- 只建议保存可以随时轮换的中低价值凭证，并把令牌表权限收紧到仅本人。高价值
  凭证应使用 1Password 等专业密码管理器。
- 在 Agent 对话中粘贴凭证会让它进入会话记录。`run` 启动的目标程序也可能自行
  打印环境变量；secret-book 不会过滤目标程序的输出。

secret-book 使用你自己的飞书令牌表，确认后可把选定字段保存到本机。取用时避免把值写进命令参数、回复或日志；表与本机配置均为明文，不提供端到端加密。

## 直接这样使用

安装后可以显式调用 Skill：

```text
# Claude Code
/secret-book 把这个 GitHub Token 保存为一条令牌记录

# Codex
$secret-book 用我保存的 CNB 凭证执行 git push
```

也可以直接描述需求：

| 你对 Agent 说 | secret-book 执行的操作 |
|---|---|
| “保存这个 API key，用途是调用测试环境” | 从标准输入接收值并新建令牌记录 |
| “列出 GitHub 相关凭证，不要显示值” | 只查询名称、服务、账号、用途和到期时间 |
| “用 `<记录名>` 执行这条命令” | 把记录中的键值注入子进程环境后执行命令 |
| “把 `<记录名>` 里的 `PASSWORD` 复制出来” | 把单个值写入系统剪贴板，只显示掩码 |
| “默认设置切换到工作” | 切换本机当前使用的令牌配置，不修改 lark-cli 的当前 profile |

如果名称可能对应多条记录，Agent 会先列出不含凭证值的候选项，不会自行选择。

## 安装

支持 macOS、Linux 和原生 Windows（2.4.0 起）。Windows 使用 PowerShell，无需 WSL；安装和调用须在同一个实际运行环境内完成。需要：

- [uv](https://docs.astral.sh/uv/) >= 0.8
- 已安装 `lark-cli`，并至少有一个 user 身份完成登录的 profile
- 能访问飞书开放平台

Windows 还需要支持文件访问权限的磁盘（如 NTFS），以及当前 Windows 环境能运行的 `lark-cli`。

把下面一段发给 Claude Code 或 Codex：

```text
请安装 secret-book：优先用 https://github.com/cookaihq/secret-book，网络故障时改用 https://cnb.cool/zhidateam/tannt/secret-book.git。保留已有配置，检查当前 Agent 能否发现和调用，并引导我补齐运行依赖。
```

业务配置保存与修复需要 2.3.0 或更新版本。Skill 会自动检查更新并提醒，实际拉取前征求确认。可以说“请检查 secret-book 是否有更新”；决定更新后再明确要求安装新版。

Windows 的脚本、文件权限、并发写入、确认恢复和命令执行在原生环境验证；飞书响应使用模拟数据。真实账号登录与飞书业务请求，以及 Codex / Claude Code 的新会话发现和隐式触发，仍须在实际安装后分别核对。

## 首次设置

一套“令牌配置”对应一张飞书令牌表、一个 lark-cli profile 和一个经过确认的飞书
身份。第一次使用时，在下面两种方式中选择一种。以下对话示例使用 Claude Code 的
`/secret-book`；在 Codex 中请改用 `$secret-book`。

### 新建令牌表

让 Agent 检查可用飞书账号，再选择账号和便于识别的配置名称：

```text
/secret-book 请列出可用的飞书账号，让我选择后新建令牌表，
并把这套配置命名为 `<配置名>`；已有可用配置时先告诉我，避免重复创建。
```

### 接管已有令牌表

```text
/secret-book 接管这张令牌表：`<飞书多维表格 URL>`，
请检查可用飞书账号并让我选择，配置名为 `<配置名>`
```

两种方式都会按相同流程执行：

1. secret-book 读取所选 profile 当前登录的应用和用户身份。如果 profile 尚未登录，
   Agent 按下面“提示确认身份或修复 profile”中的 split-flow 完成登录后再继续。
2. Agent 展示 profile、`app_id`、用户名和 `open_id`，等待你确认。
3. 确认后才创建或校验令牌表，并保存本机令牌配置。

接管已有表时，secret-book 会先校验已有字段，再补建缺失字段。字段类型不符合要求
时不会修改表结构。第一套令牌配置会自动成为当前配置，也就是默认配置。

## 让其他 Skill / Plugin 使用凭证

需要首次配置，或发现本机配置缺失、被服务拒绝时，可以对 Agent 说：

```text
请用 secret-book 为〈Skill / Plugin〉配置或修复凭证。先检查实际来源和当前 Agent 的相关规则，复用我的令牌表；让我确认记录、账号、表中实际 key 与业务字段的对应关系，以及要写入的文件，隐藏值。修复错误时替换原文件；首次新增沿用已有保存位置，没有决定时再建议个人全局。写完告诉我路径和实际生效来源。
```

你不需要整理变量名、表 ID 或启动命令。Agent 先读取业务方声明，再由 secret-book 检查账号和表、列出候选。尚未使用过 secret-book 时会引导安装依赖、登录和建立或接管令牌表，也可以改为自行填写文件。没有记录或缺少字段时，在自己的表中补填，不把密钥发到聊天。

确认摘要包含记录、服务/账号、**表中实际 key → 业务字段**、准确的目标文件和替换项；唯一候选首次也确认。确认后值直接写入本机配置，保留其他字段。修复原本位于项目或 Skill 专用文件中的错误时，就改那份文件；不会另写全局文件掩盖错误。来自环境变量的值需要先查明启动或注入来源。

首次新增没有既定位置时，默认建议调用者的个人全局配置：Plugin 使用 `~/.config/〈Plugin名〉/.env`，独立 Skill 使用 `~/.config/〈Skill名〉/.env`。业务方必须实际支持读取这个位置。写完 Agent 会报告完整路径；如需只供项目使用，可以让它检查项目 `.env.local` 的忽略规则与覆盖关系，再确认迁移。

**后续直接使用业务 Skill / Plugin。** 它读取已保存的本机配置，无需每次访问 Secret Book 或飞书。表中轮换密钥不会自动同步本机；需要更新时重新检查、确认和保存，防止覆盖你手动修改过的配置。仅本轮、短期令牌或不允许保存的情形才采用临时取用。

文件写入成功、本机读取成功和服务鉴权成功会分别说明。网络故障、余额或权限不足不直接判定为 Key 错误；修复配置不表示可以自动重发原业务请求。不支持配置声明和来源报告的旧产物，Agent 应先核对其真实配置方式，不能声称已支持自动修复。

## 保存和取用凭证

凭证值保存在飞书令牌表中。secret-book 自身的表定位和身份配置保存在个人目录的 `.config/secret-book/.env`；业务配置的确认与写入记录保存在同目录 `consumer-configurations.json`，临时命令绑定保存在 `bindings.json`；这两份文件只含元数据和校验值，不保存密钥值。业务配置值写在调用者自己的已确认文件中。

| 运行环境 | 个人配置目录 |
| --- | --- |
| macOS | 通常为 `/Users/〈用户名〉/.config/secret-book/` |
| Linux | 通常为 `/home/〈用户名〉/.config/secret-book/` |
| WSL | WSL 自己的 Linux 用户目录，通常为 `/home/〈WSL 用户名〉/.config/secret-book/`；不自动共用 Windows 用户目录，完整流程尚未验证 |
| Windows 原生 | `%USERPROFILE%\.config\secret-book\`，通常为 `C:\Users\〈用户名〉\.config\secret-book\`；不是 `%APPDATA%`，不自动读取 WSL 的配置 |

实际个人目录由 Python 运行时确定，Agent 应报告当前环境的完整路径；表配置可能被当前工作目录或进程中的完整配置覆盖，顺序见下方“项目需要使用另一张令牌表”。这些文件独立于安装目录，更新 Skill 不应删除它们。

本机配置仍是明文。macOS/Linux 写入文件仅允许本人访问；Windows 写入前设置并核对只允许当前用户与系统账户访问的权限，磁盘不支持时停止写入。不会改动已有业务项目目录的权限。Windows 剪贴板支持中文；复制不会自动清空剪贴板。

一条令牌记录可以只保存一个值，也可以保存一组需要同时注入的 dotenv 键值，例如
OSS 的 `ACCESS_KEY_ID`、`ACCESS_KEY_SECRET`、`ENDPOINT` 和 `BUCKET`。

secret-book 提供三种取值方式：

- **保存到业务配置**：预览并确认目标文件和字段后写入；业务程序后续直接读自己的配置。

- **执行命令**：`run` 把记录中的键值加入子进程环境。secret-book 只显示注入的
  键名，不显示值。
- **复制到剪贴板**：`copy` 适合必须手工粘贴的场景。多键记录需要指定一个键名。

`get` 只返回记录元数据、备注和键名，不返回凭证值。`list` 只返回记录列表的元数据。

### 自动记住某个命令使用的凭证

第一次成功执行命令时可以建立绑定：

```text
/secret-book 用记录 `<记录名>` 执行 git push；成功后记住这次选择
```

绑定按“令牌表身份 + 项目目录 + 命令名”区分。以后在同一项目中执行相同命令时，
Agent 可以复用这条绑定；切换到另一张令牌表后不会误用原表中的记录。可以让 Agent
“列出 secret-book 自动绑定”或“解除当前项目中 git 命令的绑定”。

## 多套令牌配置

本机可以保存多套有名称的令牌配置，例如“工作”和“个人”。任何时刻只有一套当前
配置，它也是默认配置。

- “列出令牌配置”只显示名称、稳定 ID、lark-cli profile 和当前标记。
- “默认设置切换到 `<配置名>`”会持久切换当前配置。
- 切换不会删除其它配置，也不会调用 `lark-cli profile use`。
- 每次访问飞书前，secret-book 都会确认配置中的 profile 仍是原先确认过的应用和
  用户；身份发生变化时会停止访问并引导重新绑定。

## 检查 Agent 中的使用规则

每次进入 secret-book 流程，会先只读检查当前调用 Agent 的规则：是否已有说明、是否仍要求旧的自动注入重试、是否受项目覆盖或开关限制，以及多个路径是否指向同一文件。检查不自动修改你的全局规则，文件存在也不代表当前会话已加载。

已提供 Codex、Claude Code、Hermes、OpenClaw、WorkBuddy，以及 Gemini CLI、OpenCode、Copilot CLI、Cursor、Cline、Windsurf 等入口检查。配置目录、版本和加载机制各不相同；比如 Cursor 的全局 User Rules 在设置界面，需要在宿主核对。完整列表与验证边界见 [Agent 规则检查](references/agent-rules.md)。

需要安装或更新规则时可以说：

```text
请检查当前 Agent 的 secret-book 使用规则，列出要修改的实际文件和新规则，确认后再安装或更新；保留其他规则和共用文件的链接。
```

新规则要求先报告配置问题、沿用管理选择、确认后修复原文件，并在可保存时优先让业务程序读取本机配置。规则文件只存使用说明，不存业务密钥。修改后仍要核对宿主实际加载状态。

需要进一步诊断时，可让 Agent 参考 [业务配置流程](references/consumer-setup.md) 和 [CLI 说明](references/cli.md)。

## 常见问题

### 提示确认身份或修复 profile

退出码 `3` 可能表示需要确认飞书身份、修复 profile，或者 `run --auto` 没有可用
绑定。Agent 会读取结构化提示并说明下一步；不要把它当成普通失败直接重复执行。

如果结构化提示中的 `fix_actions` 含有 `kind: auth_split_flow`，登录必须分两轮完成：

1. 使用动作里的 `start_argv_template`，显式传入目标 `--profile`、`--domain base`、
   `--no-wait --json`，从 JSON 读取 `verification_url`、`device_code` 和 `expires_in`。
2. 使用动作里的 `qrcode_argv_template` 生成临时 PNG，先向你展示原始授权 URL，再展示
   二维码；展示完成后删除临时文件。只向你展示目标 profile、目标账号、授权范围和过期
   信息；`device_code` 只保存在当前任务的短期运行上下文中，不写入回复、日志、配置或
   文件。展示 URL 和二维码后结束当前轮，等待你完成网页授权。
3. 你回复“已授权”后，Agent 使用同一 profile 和原 `device_code` 执行动作里的
   `resume_argv_template`，不能再次执行 `--no-wait`。随后执行 `status_argv`，确认
   本机 user 凭据已保存，再由 secret-book 校验绑定的 `app_id` 和 `open_id`。

网页显示授权完成不等于本机登录成功。续接失败或出现 `device_code is invalid` 时，Agent
   必须先检查 profile 登录状态、两条命令的 profile 是否一致、是否发生 profile 切换、
   CLI 版本和授权请求是否过期；不能连续重试或直接生成新请求。只有原 code 明确过期、
   被服务端作废，或你明确要求重新授权，并再次确认这次新请求后，才允许重新发起一次授权。
   如果当前任务已经丢失原 code，Agent 必须停止并等待你明确要求重新授权。

### 写请求的结果无法确定

飞书写请求遇到瞬时网络错误时不会自动重试，退出码为 `121`。这时先用 `list` 或
`get` 检查记录是否已经写入，再决定下一步，避免创建重复记录。

### 旧安装无法读取现有配置

同一用户的多个 Agent 可能安装了不同版本的 secret-book，但共用
`~/.config/secret-book/.env`。如果旧安装无法识别多配置格式，应先更新该安装并在
新会话中执行 `config list`；不要把现有配置降级成旧格式。

### Windows 提示不支持或找不到依赖

可以说：“请检查当前 Agent 实际使用的 secret-book 路径和版本，确认在原生 Windows 运行，并核对 uv、lark-cli 和配置目录；保留已有配置。”2.3.x 的 Windows 限制需要升级到 2.4.0 或更新版本才能解除；仅改提示语或切换终端不能修复旧代码。

### 项目需要使用另一张令牌表

按当前配置查表的命令，按以下顺序选择第一套完整配置：进程环境变量、当前目录的 `.env.secret-book`、`.env.local`、
当前目录的 `.env`，最后才是在显式使用 `--use-global-config` 时读取全局当前配置。
工作文件夹就是 Agent 执行命令时所在的目录，与 Skill 安装目录不同；不会向父目录搜索配置。
项目配置必须在同一层提供完整的表定位、profile 和身份字段，不能跨层拼接。也可以用 `--config-name <名称>` 明确选择一套完整的全局命名配置；它跳过进程和项目来源，不改变当前配置。

已经写入业务文件的值不会随令牌表或默认配置切换而改变。如需从另一张表更新业务配置，让 Agent 重新检查来源、确认记录与目标文件，再写入。

## 自动检查更新

Git 安装在 Windows 使用 PowerShell 检查，在 macOS/Linux/WSL 使用 Bash；复制安装由 Agent 按随包元数据检查稳定版本。检查失败继续当前任务，实际更新须经你确认。可以说“关闭 secret-book 的自动检查更新”；开关只保存并读取个人配置目录 `.env` 中的 `AUTO_UPDATE_CHECK=0`，与业务配置的读取顺序独立。说“重新开启”即可恢复检查。

## 版本与 Release

<!-- release-table:begin -->
| 目标 | 版本 | Release |
|---|---|---|
| secret-book | 2.4.0 | [v2.4.0](https://github.com/cookaihq/secret-book/releases/tag/v2.4.0) |
<!-- release-table:end -->

## License

[MIT](LICENSE)
