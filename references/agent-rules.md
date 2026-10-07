# 当前 Agent 的规则检查

进入 secret-book 后，由调用 Agent 先从当前会话确定宿主与实际工作目录，再调用：

```bash
uv run --project "${SKILL_DIR}" "${SKILL_DIR}/scripts/secret_book.py" agent-rule --agent codex
```

这是只读静态检查，不查询令牌表、不修改规则、不写业务凭证。`configure` 也会执行相同检查并把结果放入确认摘要。默认不扫描所有安装目录推测宿主；显式盘点才使用 `--all`。

返回 `status` 区分 `missing`、`managed_current_static`、`outdated`、`custom_review`、`conflict_review` 和 `unknown`。每个文件还列出真实路径、symlink 别名、全局/项目范围和 `selected` / `shadowed` / `conditional_review` / `disabled` 等适用状态。`project_only` 表示目前发现的相关规则仅限项目。

脚本能准确比较自己生成的 v6 规则块及已知文件优先级。对于自然语言、imports、IDE 开关、托管策略和版本差异，只标记需要审查；Agent 继续阅读实际适用说明并解释其中的限制。找到字符串不等于完成接入，`session_loaded: not_verified` 不能改口说“当前会话已生效”。旧 v1–v5 块应按当前 Skill 的角色入口及本机保存／原来源修复流程更新；检查不自动覆盖旧块。

## 宿主入口与核对范围

| `--agent` | 默认入口与补充检查 | 边界 |
| --- | --- | --- |
| `codex` | `$CODEX_HOME`（默认 `~/.codex`）中首个非空 `AGENTS.override.md` / `AGENTS.md`；Git 根至 cwd 的项目文件及已配置 fallback 名 | 文档预算、命令行或会话启动加载需要宿主验证 |
| `claude-code` | `~/.claude/CLAUDE.md`、`rules/`，项目 `CLAUDE.md`、`.claude/CLAUDE.md`、`CLAUDE.local.md` 与 rules | 核对 `/memory`、imports、排除设置和托管策略；不无条件断言所有版本都读 AGENTS.md |
| `hermes` | `$HERMES_HOME/SOUL.md`（默认 `~/.hermes`），项目 `.hermes.md` / `HERMES.md` / AGENTS / CLAUDE 候选 | SOUL 是身份文件；项目入口的选择依版本核对，不虚构全局 `~/.hermes/AGENTS.md` |
| `openclaw` | 当前 Agent 实际 workspace 的 `AGENTS.md` | 优先提供已解析 `--workspace`；配置多实例需 `--agent-id`。JSON5、profile、未知结构不能可靠解析时返回待核对 |
| `workbuddy` | 标准桌面 `~/.workbuddy/CODEBUDDY.md`、rules；项目 CODEBUDDY/AGENTS 和 `.codebuddy` | 已按 WorkBuddy 5.5.6 代码核对。实际 `CODEBUDDY_CONFIG_DIR` / `WORKBUDDY_CONFIG_DIR` 可改位置，不猜成 WORKBUDDY.md |
| `codebuddy` | CLI 默认 `~/.codebuddy/CODEBUDDY.md` 与 rules，或实际配置目录 | 与 WorkBuddy 分开确认；IDE/CLI 版本和开关须核对 |
| `gemini` | `~/.gemini/GEMINI.md` 及项目文件，读取 settings 的 `context.fileName` | 用 `/memory show` / reload 验证实际加载 |
| `opencode` | `~/.config/opencode/AGENTS.md`；不存在时按禁用开关检查 Claude 全局回退；项目 AGENTS/CLAUDE | 核对 `opencode.json/jsonc` instructions；不悄悄新建全局文件使 Claude 回退失效 |
| `copilot` | `$COPILOT_HOME`（默认 `~/.copilot`）下 copilot-instructions.md、instructions；项目 .github 和上下文文件 | 仅 Copilot CLI；用 `/instructions` 核对 applyTo、禁用状态和额外目录，IDE 另查 |
| `cursor` | 项目 `.cursor/rules/*.mdc`、AGENTS.md；全局 User Rules 在设置界面 | 不读写私有 IDE 数据库，无法查设置时明确未知 |
| `cline` | `~/Documents/Cline/Rules`、`~/.cline/rules`、`~/Cline/Rules` 候选及 `~/.agents/AGENTS.md`；项目 .clinerules / .cline/rules | 当前安装实际目录、开关和路径条件需宿主确认 |
| `windsurf` | `~/.codeium/windsurf/memories/global_rules.md`；项目 .devin/rules / .windsurf/rules 和 AGENTS | Cascade 全局文件 6000 字符；Devin Desktop 与 Devin Local 不混为一种入口 |
| `qwen` / `iflow` | `~/.qwen/QWEN.md` / `~/.iflow/IFLOW.md` 及项目候选 | 保留原支持入口，按当前宿主版本核对 |
| `amp` / `goose` | `~/.config/amp/AGENTS.md` / `~/.config/goose/AGENTS.md` 及项目候选 | 保留原支持入口，不把路径存在当成实际加载 |

`--config-dir` 用于调用 Agent 已确认的真实配置目录，不能传一个希望它加载但实际没使用的目录。非默认 profile、远程 Agent、容器或 Windows/WSL 应在真实执行环境检查。2.4.0 起支持 Windows 原生规则检查和已授权的文件写入；Windows 用户目录由 Python 的 `Path.home()` 确定，WSL 使用自己的 Linux 目录。文件写入成功仍不证明宿主会话已加载。

## 修改规则

检查发现缺失、旧规则或冲突时，报告状态、行为差异和建议。读取不授权写入。安装或更新规则（包括安装 Skill、补齐依赖时的可选规则安装）按以下顺序执行，Claude Code、Codex、WorkBuddy 及其他支持的宿主共用此流程：

1. **准备准确的预览。** 根据只读检查和目标文件现状确定实际写入路径、作用范围（当前宿主的个人全局／具体项目），以及是新建文件、向已有文件追加规则，还是替换已有的 secret-book 规则块。替换时说明旧规则版本或手工修改情况及行为差异。新规则取自当前安装的 `scripts/agent_rules.py` 中 `rule_block()` 的实际返回文本，保留起止标记、版本与全部条款。
2. **先发送正文，再请求确认。** 在用户可见的回复正文中先列出上述路径、范围和变更方式，再用 Markdown 代码块逐字展示即将写入的完整规则。正文必须位于确认选项上方；仅在工具输出中打印、给出文件链接、概括作用、用省略号截断或把全文塞进选择题，都不算完成展示。
3. **正文发送完成后才提供选项。** 随后调用宿主的选择／确认工具，第一项为“安装规则块（推荐）”，第二项为“暂不安装”；更新时第一项为“更新规则块（推荐）”，第二项为“暂不更新”。宿主没有选择工具时，在完整预览之后按同样顺序用普通文字列出选项并请求确认。推荐或预选不代替用户确认。用户已对展示过的同一目标和完整内容明确授权时沿用，无需重复询问；目标、范围、变更方式或规则内容变化时重新展示并确认。
4. **按确认内容写入并回读。** 确认后才执行 `agent-rule --install --agent <当前Agent>`，安装和更新都使用此入口，保留检查时使用的配置目录／工作区参数。回读目标，核对规则与预览一致，报告实际写入位置与验证范围。用户暂不安装／更新时继续其他已授权步骤。

规则原文可在本机只读生成，两个路径均换成当前安装位置；此命令的输出仍须按第 2 步完整转述到回复正文：

```text
uv run --project "<实际 Skill 目录>" python -X utf8 -c "import sys; sys.path.insert(0, sys.argv[1]); from agent_rules import rule_block; print(rule_block())" "<实际 Skill 目录>/scripts"
```

移除规则时先展示具体目标、作用范围和待移除的规则块，取得明确授权后执行 `agent-rule --remove --agent <当前Agent>`。多路径指向同一实体只写一次并保留 symlink。其他手工说明不删除，手改受管理块需要针对性的覆盖授权才能使用 `--force`；多个受管理块需人工审查。规则文件只保存使用说明，不保存业务密钥。

v6 规则引用安装的 Skill 角色入口和随包业务流程，不复制角色协议或嵌入开发机、临时 worktree 脚本绝对路径。修改后仍须用宿主的新会话或实际规则查看入口验证加载；文件写入成功不能作为该验收的替代。

## 调研依据（2026-10-01）

- [Codex AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Claude Code memory](https://code.claude.com/docs/en/memory)
- [Hermes context files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files)
- [OpenClaw agent workspace](https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent-workspace.md)
- [Gemini CLI GEMINI.md](https://geminicli.com/docs/cli/gemini-md/)
- [OpenCode rules](https://opencode.ai/docs/rules/)
- [Copilot CLI instructions](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions)
- [Cursor rules](https://cursor.com/docs/rules)
- [Cline rules](https://docs.cline.bot/customization/cline-rules)
- [Windsurf / Devin Desktop Cascade](https://docs.devin.ai/desktop/cascade/memories)

WorkBuddy 的入口依据本机 5.5.6 包内 `codebuddy-headless.js` 的 `MemoryLoader.loadUserMemories`、`PathUtils.getHomeDir`，桌面端 `main/app-instance.js` 的配置目录注入和 product.json 的 dataFolderName；这是特定版本的代码核对，不表示全部宿主已完成真实会话验收。
