# secret-book

让 Claude Code、Codex 等 Agent 从你自己的飞书多维表格中查找凭证，并把凭证临时
注入需要它的命令或写入剪贴板。secret-book 自身只显示记录元数据、键名和掩码，
不会把凭证值打印到终端。

## 使用前先确认

- **凭证以明文保存在飞书多维表格中，不加密。secret-book 不是密码管理器。**
- 飞书平台方和任何拥有表格权限的人都能看到数据。飞书多维表格保留 180 天历史
  记录，删除后另有 30 天回收站。
- 只建议保存可以随时轮换的中低价值凭证，并把令牌表权限收紧到仅本人。高价值
  凭证应使用 1Password 等专业密码管理器。
- 在 Agent 对话中粘贴凭证会让它进入会话记录。`run` 启动的目标程序也可能自行
  打印环境变量；secret-book 不会过滤目标程序的输出。

secret-book 的安全目标是让凭证留在你自己的飞书租户中，并避免 Agent 在取用时把
值写进命令参数、回复或日志；它不提供端到端加密。

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

支持 macOS 和 Linux，当前不支持 Windows。需要：

- [uv](https://docs.astral.sh/uv/) >= 0.8
- 已安装 `lark-cli`，并至少有一个 user 身份完成登录的 profile
- 能访问飞书开放平台

仓库根目录就是 Skill 目录。同一份 `SKILL.md` 同时用于 Claude Code 和 Codex：

```bash
mkdir -p "${HOME}/agent-repos" "${HOME}/.claude/skills" "${HOME}/.agents/skills"
git clone https://github.com/cookaihq/secret-book.git "${HOME}/agent-repos/secret-book"

# Claude Code
ln -s "${HOME}/agent-repos/secret-book" "${HOME}/.claude/skills/secret-book"

# Codex
ln -s "${HOME}/agent-repos/secret-book" "${HOME}/.agents/skills/secret-book"
```

第一次运行脚本时，uv 会按仓库内的锁文件创建
`agent-repos/secret-book/.venv`。后续更新使用：

```bash
git -C "${HOME}/agent-repos/secret-book" pull --ff-only
```

Skill 会定期检查远端是否有新版本，但不会自行拉取；发现更新后会先征求确认。

## 首次设置

一套“令牌配置”对应一张飞书令牌表、一个 lark-cli profile 和一个经过确认的飞书
身份。第一次使用时，在下面两种方式中选择一种。以下对话示例使用 Claude Code 的
`/secret-book`；在 Codex 中请改用 `$secret-book`。

### 新建令牌表

告诉 Agent 要使用的 lark-cli profile 和配置名称：

```text
/secret-book 使用 lark-cli profile `<profile>` 新建一张令牌表，
并把这套配置命名为 `<配置名>`
```

### 接管已有令牌表

```text
/secret-book 接管这张令牌表：`<飞书多维表格 URL>`，
使用 lark-cli profile `<profile>`，配置名为 `<配置名>`
```

两种方式都会按相同流程执行：

1. secret-book 读取所选 profile 当前登录的应用和用户身份。
2. Agent 展示 profile、`app_id`、用户名和 `open_id`，等待你确认。
3. 确认后才创建或校验令牌表，并保存本机令牌配置。

接管已有表时，secret-book 会先校验已有字段，再补建缺失字段。字段类型不符合要求
时不会修改表结构。第一套令牌配置会自动成为当前配置，也就是默认配置。

## 保存和取用凭证

一条令牌记录可以只保存一个值，也可以保存一组需要同时注入的 dotenv 键值，例如
OSS 的 `ACCESS_KEY_ID`、`ACCESS_KEY_SECRET`、`ENDPOINT` 和 `BUCKET`。

secret-book 提供两种取值方式：

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

## 可选：让 Agent 在缺少凭证时调用 secret-book

可以让 Agent 安装 secret-book 的凭证兜底规则：

```text
/secret-book 安装凭证兜底规则
```

这会修改检测到的 Agent 全局指令文件。安装前，Agent 必须展示目标文件和完整规则，
由你确认后才写入。安装后，当命令因为缺少凭证而失败时，Agent 会：

1. 先尝试当前项目、命令和令牌表身份已有的自动绑定。
2. 没有绑定时只查询令牌记录元数据，并按用途匹配。
3. 只有唯一匹配时才使用；多条候选或无法匹配时由你选择。
4. 鉴权失败后解除旧绑定，不会用同一条凭证反复重试。

## 命令行使用

一般情况下直接让 Agent 调用 Skill 即可。需要手工操作时，从仓库目录执行：

```bash
cd "${HOME}/agent-repos/secret-book"
uv run --project . scripts/secret_book.py --help
```

常用命令：

| 操作 | 命令 |
|---|---|
| 查看本机令牌配置 | `config list` |
| 切换当前配置 | `config use --name <配置名>` |
| 新建令牌表 | `init-create --lark-profile <profile>` |
| 接管令牌表 | `init-adopt --url <多维表格 URL> --lark-profile <profile>` |
| 保存令牌记录 | `save --name <名称> --service <服务> --purpose <用途> --use-global-config` |
| 列出令牌记录 | `list --use-global-config` |
| 查看一条记录的元数据和键名 | `get --name <名称> --use-global-config` |
| 注入环境变量并执行命令 | `run --name <名称> --use-global-config -- <命令>` |
| 执行成功后建立自动绑定 | `run --id <记录 ID> --bind --use-global-config -- <命令>` |
| 复用自动绑定 | `run --auto --use-global-config -- <命令>` |
| 复制单个值 | `copy --name <名称> --key <键名> --use-global-config` |
| 查看自动绑定 | `bindings` |

`save` 从标准输入读取 dotenv，不从命令参数读取凭证值：

```bash
printf '%s\n' 'GITHUB_TOKEN=<token>' | \
  uv run --project . scripts/secret_book.py save \
  --name <名称> --service github --purpose <用途> --use-global-config
```

`config list/use` 直接管理全局配置，不接受 `--use-global-config`。`save`、`list`、
`get`、`run` 和 `copy` 要使用全局当前配置时，必须显式添加这个参数。

## 常见问题

### 提示确认身份或修复 profile

退出码 `3` 可能表示需要确认飞书身份、修复 profile，或者 `run --auto` 没有可用
绑定。Agent 会读取结构化提示并说明下一步；不要把它当成普通失败直接重复执行。

### 写请求的结果无法确定

飞书写请求遇到瞬时网络错误时不会自动重试，退出码为 `121`。这时先用 `list` 或
`get` 检查记录是否已经写入，再决定下一步，避免创建重复记录。

### 旧安装无法读取现有配置

同一用户的多个 Agent 可能安装了不同版本的 secret-book，但共用
`~/.config/secret-book/.env`。如果旧安装无法识别多配置格式，应先更新该安装并在
新会话中执行 `config list`；不要把现有配置降级成旧格式。

### 项目需要使用另一张令牌表

业务命令按以下顺序选择第一套完整配置：进程环境变量、当前目录的 `.env.local`、
当前目录的 `.env`，最后才是在显式使用 `--use-global-config` 时读取全局当前配置。
项目配置必须在同一层提供完整的表定位、profile 和身份字段，不能跨层拼接。

## 版本与 Release

<!-- release-table:begin -->
| 目标 | 版本 | Release |
|---|---|---|
| secret-book | 2.0.1 | [v2.0.1](https://github.com/cookaihq/secret-book/releases/tag/v2.0.1) |
<!-- release-table:end -->

## License

[MIT](LICENSE)
