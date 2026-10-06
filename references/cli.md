# secret-book CLI 参考

一般情况下直接让 Agent 调用 Skill 即可。以下供 Agent 排查入口使用；保持业务工作目录，`SKILL_DIR` 指向本次实际加载的 Skill 实体目录：

```bash
uv run --project "$SKILL_DIR" "$SKILL_DIR/scripts/secret_book.py" --help
```

原生 Windows 使用 PowerShell，`$SkillDir` 同样是实际安装目录，不需要 Bash/WSL：

```powershell
uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" --help
```

不要为了调用 Skill 改变目录；项目配置按命令的工作目录读取。Windows 与 WSL 的个人配置目录各自独立，见 [README](../README.md#配置保存在哪里)。每次调用先按 [角色入口](roles.md) 明确本次职责。

## 任务与确认

先按角色规范读取未完成任务，再开始目标一致的新任务。以下为 PowerShell 示例；Bash 将 `$SkillDir` 换为 `$SKILL_DIR`：

```powershell
uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" workflow status --agent codex
uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" workflow start --role consumer --basis inferred --goal "连接已有令牌表" --agent codex
```

`--agent` 填实际宿主，例如 `codex`、`claude-code` 或 `workbuddy`。角色值为 `administrator`、`maintainer`、`consumer`、`developer`；用户指定角色用 `--basis explicit`，Agent 根据明确目标判断用 `inferred`。开始任务不登录飞书、不创建令牌表。省略角色返回退出码 `3` 和 `role_required`，不保存任务。

保存返回的 `task.id`。本任务后续业务命令均带 `--workflow <task.id> --workflow-agent <实际宿主>`；`run` 将这两个参数放在 `--` 和被执行命令之前。下面命令表为简写，省略统一的 uv 前缀及这两个参数。旧命令直接调用保持兼容；新增 `init-connect`、`repair-ids` 必须携带任务参数。

中断后读取 `workflow status --id <task.id> --agent <实际宿主>`。成功响应 schema 为 `secret-book.workflow/v1`，包含角色、目标、上下文、目标表和待确认信息；无 `--id` 时仅列当前上下文的未关闭任务。恢复待办后仍须取得用户对具体摘要的确认，用相同参数及任务最新的 token 继续：身份用 `--confirm-identity`，业务文件写入与 ID 修复用 `--confirm`。不要将 `configure-status` 的底层 token 代替任务 token；完整业务写入摘要仍从 `configure-status` 读取。

完成目标后用 `workflow finish --id <task.id> --agent <实际宿主>` 关闭。取消用 `--outcome cancelled`，不回滚已经发生的操作。写入中断或结果不明时返回 `verification_required`，先核对实际表或文件；恢复方式见 [角色规范](roles.md#恢复)，不能直接重放或标记完成。

## 常用命令

| 操作 | 命令 |
|---|---|
| 查看本机令牌配置 | `config list` |
| 切换当前配置 | `config use --name <配置名>` |
| 新建令牌表 | `init-create --lark-profile <profile>` |
| 接管令牌表 | `init-adopt --url <多维表格 URL> --lark-profile <profile>` |
| 只读连接已有表 | `init-connect --url <多维表格 URL> --lark-profile <本人profile>` |
| 预览并补齐一条记录的 ID | `repair-ids --name <唯一记录名> --use-global-config` |
| 保存令牌记录 | `save --name <名称> --service <服务> --purpose <用途> --use-global-config` |
| 列出令牌记录 | `list --use-global-config` |
| 查看一条记录的元数据和键名 | `get --name <名称> --use-global-config` |
| 注入环境变量并执行命令 | `run --name <名称> --use-global-config -- <命令>` |
| 执行成功后建立自动绑定 | `run --id <记录 ID> --bind --use-global-config -- <命令>` |
| 复用自动绑定 | `run --auto --use-global-config -- <命令>` |
| 复制单个值 | `copy --name <名称> --key <键名> --use-global-config` |
| 查看自动绑定 | `bindings` |
| 只读检查当前 Agent 规则 | `agent-rule --agent <当前Agent>` |
| 预览/保存业务配置 | `configure --requirements <声明> --inspection <来源报告> --agent <当前Agent> --key <字段> --id <记录> --use-global-config` |
| 查看待确认请求与写入记录 | `configure-status --requirements <声明>` |

## 首次连接与记录维护

使用者和维护者连接管理员提供的已有表。管理员检查已有表也可用同一只读入口：

```powershell
uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" init-connect --url "<表链接>" --lark-profile "<本人profile>" --workflow "<task.id>" --workflow-agent codex
```

首次返回实际飞书身份与确认 token；用户确认后在相同命令后添加 `--confirm-identity "<本任务token>"`。通过后返回 `connected_read_only`、可见记录数、缺 ID 数与 `save_command`。这一阶段只校验字段并读取可见元数据，不读取凭证值，不改表，也不保存连接。缺列返回 `table_fields_missing`；字段类型冲突直接失败，由管理员核对处理。

需要保存连接时，先确认配置名称、表和个人配置文件路径，再执行返回的 `save_command`，将 `<名称>` 替换为已确认名称。返回命令已经携带任务上下文和本次身份交接，不把命令的存在当成用户授权。保存后 `config list` 回读；本机保存成功不能代替远端访问验证。

仅维护者任务可用 `repair-ids`，首次预览不会写入：

```powershell
uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" repair-ids --name "<唯一记录名>" --use-global-config --workflow "<维护任务id>" --workflow-agent codex
```

展示目标记录和“仅补缺失 ID”摘要，确认后用相同命令加 `--confirm "<本任务token>"`。成功返回 `id_repaired`；已有 ID 返回 `already_has_id`，不改写。补齐前后都不读取凭证值。`list/get/run/copy/configure` 不再自动修复 ID；缺 ID 的目标记录返回 `record_id_missing`，交维护者处理。

## 凭证输入与配置选择

`save` 从标准输入读取 dotenv，不从命令参数读取凭证值：

```bash
printf '%s\n' 'GITHUB_TOKEN=<token>' | \
  uv run --project "$SKILL_DIR" "$SKILL_DIR/scripts/secret_book.py" save \
  --name "<名称>" --service github --purpose "<用途>" --use-global-config \
  --workflow "<维护任务id>" --workflow-agent "<实际宿主>"
```

PowerShell 管道需使用 UTF-8，`$Payload` 仅代表已获授权的内存输入，不把真实值写入命令参数或脚本：

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$Payload | uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" save --name "<名称>" --service github --purpose "<用途>" --use-global-config --workflow "<维护任务id>" --workflow-agent "<实际宿主>"
```

`config list/use` 直接管理全局配置，不接受 `--use-global-config`。`save`、`list`、
`get`、`configure`、`run --id/--name/--auto` 和 `copy` 要使用全局当前配置时，必须显式添加这个参数。`run --requirements` 已移除，不再包装业务程序启动。保存配置的完整确认流程见 [接入流程](consumer-setup.md)。


`list/get/save/copy/configure` 与旧 `run` 也可使用 `--config-name <名称>` 明确选一套完整命名配置，无需切换当前项或传启用 flag；该选择不与项目/进程来源拼接。

## 退出码

| 退出码 | 处理方式 |
| --- | --- |
| `0` | 当前 CLI 步骤成功；完整任务仍以实际目标和验证结果为准 |
| `1` | 参数、配置、数据或确定性外部错误；先修复具体原因 |
| `3` | 根据 JSON 的 schema/status 处理角色、身份、记录、业务配置或恢复引导；旧 `run --auto` 无绑定也使用此码 |
| `121` | 写入结果不明；先只读核对，不能直接重试 |

任务错误 schema 为 `secret-book.workflow-guidance/v1`。`role_mismatch` 要求确认任务职责，`context_changed` 要求核对宿主、工作目录和安装，`state_invalid` 要求保留损坏文件并核对，不能覆盖为新安装。角色守卫不代替飞书权限。参数解析错误使用 argparse 的退出码 `2`；`run` 另会透传被执行命令的退出码。

宿主通过 PowerShell `-Command` 包装调用时，外层可能把非零退出码显示为 `1`。应同时读取已知 schema 的 JSON `status`，不能把 `confirmation_required` 当成初始化失败而重试。需要保留 CLI 原始退出码时，在包装命令最后显式执行 `exit $LASTEXITCODE`。
