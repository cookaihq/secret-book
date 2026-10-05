# secret-book CLI 参考

一般情况下直接让 Agent 调用 Skill 即可。以下供 Agent 排查入口使用；保持业务工作目录，`SKILL_DIR` 指向本次实际加载的 Skill 实体目录：

```bash
uv run --project "$SKILL_DIR" "$SKILL_DIR/scripts/secret_book.py" --help
```

原生 Windows 使用 PowerShell，`$SkillDir` 同样是实际安装目录，不需要 Bash/WSL：

```powershell
uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" --help
```

不要为了调用 Skill 改变目录；项目配置按命令的工作目录读取。Windows 与 WSL 的个人配置目录各自独立，见 [README](../README.md#保存和取用凭证)。

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
| 只读检查当前 Agent 规则 | `agent-rule --agent <当前Agent>` |
| 预览/保存业务配置 | `configure --requirements <声明> --inspection <来源报告> --agent <当前Agent> --key <字段> --id <记录> --use-global-config` |
| 查看待确认请求与写入记录 | `configure-status --requirements <声明>` |

`save` 从标准输入读取 dotenv，不从命令参数读取凭证值：

```bash
printf '%s\n' 'GITHUB_TOKEN=<token>' | \
  uv run --project "$SKILL_DIR" "$SKILL_DIR/scripts/secret_book.py" save \
  --name <名称> --service github --purpose <用途> --use-global-config
```

PowerShell 管道需使用 UTF-8，`$Payload` 仅代表已获授权的内存输入，不把真实值写入命令参数或脚本：

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$Payload | uv run --project "$SkillDir" "$SkillDir/scripts/secret_book.py" save --name "<名称>" --service github --purpose "<用途>" --use-global-config
```

`config list/use` 直接管理全局配置，不接受 `--use-global-config`。`save`、`list`、
`get`、`configure`、`run --id/--name/--auto` 和 `copy` 要使用全局当前配置时，必须显式添加这个参数。`run --requirements` 已移除，不再包装业务程序启动。保存配置的完整确认流程见 [接入流程](consumer-setup.md)。


`list/get/save/copy/configure` 与旧 `run` 也可使用 `--config-name <名称>` 明确选一套完整命名配置，无需切换当前项或传启用 flag；该选择不与项目/进程来源拼接。
