"""Conservative static inspection of the calling Agent's instruction files.

A file's existence is not proof that a running host loaded it. Reports expose
uncertainties and conditional rules for the calling Agent to review, not a
natural-language policy decision made by a substring search.
"""
import json
import os
import re
import subprocess
from pathlib import Path

VERSION = 5
BEGIN = re.compile(r"<!-- secret-book:fallback-rule v(\d+) -->")
END = "<!-- /secret-book:fallback-rule -->"
AGENTS = ("codex", "claude-code", "hermes", "openclaw", "workbuddy", "codebuddy", "gemini",
          "opencode", "copilot", "cursor", "cline", "windsurf", "qwen", "iflow", "amp", "goose")


def rule_block():
    return f"""<!-- secret-book:fallback-rule v{VERSION} -->
## secret-book 配置取用与修复
1. Skill / Plugin 正常读取自己的本机配置。缺项或有证据表明配置无效时，报告字段、实际来源和错误依据，让用户选择修改本机配置或用 secret-book；沿用已明确选择，不擅自切换。
2. 选择 secret-book 后，读取当前安装的 SKILL.md 与 references/consumer-setup.md。先只读检查当前调用 Agent 实际适用的规则、覆盖和旧规则；检查不授权修改规则，规则文件不保存业务密钥。
3. 能安全持久保存的配置优先写入本机。修复已有错误必须替换实际生效的原文件/原字段；环境变量来源先定位启动或注入配置，不得另写全局文件假装修好。首次新增沿用既定位置，无既定位置才建议调用者个人全局目录。
4. 展示令牌表/身份、候选记录与账号、表中实际 key 到业务字段的映射、目标文件与替换项，唯一候选首次也确认。没有记录或缺 key 时引导用户在自己的表或文件中填写，不要求把密钥发到聊天。
5. 确认后用 secret-book 的配置写入入口保存，仅更新确认的字段并回读；让调用者重新读配置，报告写入路径、实际生效来源及验证范围。后续直接运行调用者，不每次查表；表中轮换不自动同步本机。
6. 短期令牌、仅本轮、不允许保存或目标不支持保存时才显式使用临时注入。网络故障、余额或权限不足不能直接判定密钥错误；配置修复不授权自动重发有副作用或结果不明的业务请求。凭证值不上屏、不进 argv 或规则文件。
{END}"""


def classify(text):
    matches = list(BEGIN.finditer(text))
    if len(matches) > 1:
        return "conflict", None
    if matches:
        match = matches[0]
        end = text.find(END, match.end())
        if end < 0:
            return "custom_review", (match.start(), len(text))
        span = (match.start(), end + len(END))
        outside = text[:span[0]] + text[span[1]:]
        if re.search(r"secret[- ]book", outside, re.I):
            return "conflict_review", span
        if int(match.group(1)) < VERSION:
            return "outdated", span
        if text[span[0]:span[1]] != rule_block():
            return "custom_review", span
        return "managed_current", span
    return ("custom_review" if re.search(r"secret[- ]book", text, re.I) else "missing"), None


def project_dirs(cwd):
    try:
        result = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                                capture_output=True, timeout=5)
        root = Path(os.fsdecode(result.stdout).strip()).resolve() if result.returncode == 0 else cwd
        return [root] + [root.joinpath(*cwd.relative_to(root).parts[:i])
                         for i in range(1, len(cwd.relative_to(root).parts) + 1)]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return [cwd]


def inspect_agent(agent, *, cwd=None, config_dir=None, workspace=None, agent_id=None):
    if agent not in AGENTS:
        raise ValueError("必须由调用上下文明确当前 Agent，不能按目录存在推测")
    cwd = Path(cwd or Path.cwd()).resolve()
    home = Path.home()
    entries, notes, seen = [], [], {}
    directories = project_dirs(cwd)
    primary = None

    def add(path, scope, applicability="candidate"):
        path = Path(path).expanduser().absolute()
        real = path.resolve()
        if str(real) in seen:
            seen[str(real)]["aliases"].append(str(path))
            return
        entry = {"path": str(path), "real_path": str(real), "aliases": [], "scope": scope,
                 "applicability": applicability, "exists": path.exists(), "status": "missing"}
        seen[str(real)] = entry
        entries.append(entry)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except (OSError, UnicodeError):
            entry["status"] = "unreadable"
            return
        entry["status"], _ = classify(text)
        entry["secret_book_lines"] = [i for i, line in enumerate(text.splitlines(), 1)
                                      if re.search(r"secret[- ]book", line, re.I)]
        if re.search(r"(?m)^\s*(paths|applyTo|alwaysApply|enabled)\s*:", text):
            entry["applicability"] = "conditional_review"
        if re.search(r"(?mi)^\s*enabled\s*:\s*false\s*$", text):
            entry["applicability"] = "disabled"
        if re.search(r"(?m)(?:^|\s)@(?:[\w./~-]+\.md)\b", text):
            notes.append(f"{path} 含 import；需按宿主版本确认引用和授权，不据当前文件推定全部有效规则")

    def rules(directory, scope, pattern="**/*.md"):
        for path in sorted(Path(directory).glob(pattern)):
            if path.is_file():
                add(path, scope, "conditional_review")

    def choose(directory, names, scope):
        chosen = None
        for name in names:
            candidate = directory / name
            try:
                nonempty = bool(candidate.read_text(encoding="utf-8").strip())
            except FileNotFoundError:
                nonempty = False
            except (OSError, UnicodeError):
                nonempty = True
            if nonempty and chosen is None:
                chosen = candidate
            add(candidate, scope, "selected" if candidate == chosen else "shadowed" if chosen else "candidate")
        return chosen or directory / names[-1]

    def conf(default, env_key=None):
        return Path(config_dir or (os.environ.get(env_key) if env_key else None) or default).expanduser()

    if agent == "codex":
        base = conf(home / ".codex", "CODEX_HOME")
        primary = choose(base, ["AGENTS.override.md", "AGENTS.md"], "global")
        fallback = []
        try:
            import tomllib
            settings = tomllib.loads((base / "config.toml").read_text())
            fallback = settings.get("project_doc_fallback_filenames", [])
            if not isinstance(fallback, list) or any(not isinstance(n, str) or Path(n).name != n for n in fallback):
                fallback = []
                notes.append("Codex fallback 文件名无法确定，需要核对实际设置")
            if settings.get("project_doc_max_bytes") is not None:
                notes.append("已配置 Codex 项目文档字节预算；需在宿主确认是否发生截断")
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            notes.append("无法解析 Codex config.toml；fallback 名称和文档预算需要人工核对")
        for directory in directories:
            choose(directory, ["AGENTS.override.md", "AGENTS.md", *fallback], "project")
    elif agent == "claude-code":
        base = conf(home / ".claude", "CLAUDE_CONFIG_DIR")
        primary = base / "CLAUDE.md"
        add(primary, "global", "selected")
        rules(base / "rules", "global")
        for directory in directories:
            for relative in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md"):
                add(directory / relative, "project")
            rules(directory / ".claude/rules", "project")
        notes.append("需核对 Claude Code 实际 /memory、设置中的排除项、imports 和托管策略")
    elif agent == "hermes":
        base = conf(home / ".hermes", "HERMES_HOME")
        add(base / "SOUL.md", "global_identity", "selected")
        primary = cwd / ".hermes.md"
        for directory in directories:
            # Versions differ in supported aliases; report candidates, do not claim all are loaded.
            for name in (".hermes.md", "HERMES.md", "AGENTS.override.md", "AGENTS.md", "CLAUDE.md"):
                add(directory / name, "project", "version_dependent")
        notes.append("SOUL.md 是全局身份说明；项目文件的选择顺序需按当前 Hermes 版本核对，不向虚构的 ~/.hermes/AGENTS.md 写规则")
    elif agent == "openclaw":
        base = conf(home / ".openclaw", "OPENCLAW_STATE_DIR")
        location = workspace or os.environ.get("OPENCLAW_WORKSPACE_DIR")
        if not location:
            try:
                settings = json.loads(Path(os.environ.get("OPENCLAW_CONFIG_PATH", str(base / "openclaw.json"))).read_text())
                agents = settings.get("agents", {})
                instances = agents.get("entries", agents.get("list", []))
                if not isinstance(instances, list):
                    raise ValueError
                matches = [a for a in instances if isinstance(a, dict) and a.get("id") == agent_id] if agent_id else instances
                if (agent_id and len(matches) != 1) or (not agent_id and instances):
                    notes.append("必须明确当前 OpenClaw agent ID 或传入已解析的 --workspace；不能默认选择一个实例")
                else:
                    chosen = matches[0] if matches else {}
                    location = chosen.get("workspace") or agents.get("defaults", {}).get("workspace") or str(base / "workspace")
            except FileNotFoundError:
                if agent_id:
                    notes.append("无法读取当前实例配置；请明确提供 --workspace")
                else:
                    location = str(base / "workspace")
            except (OSError, ValueError, AttributeError):
                notes.append("OpenClaw 配置可能为 JSON5 或受 profile 影响；请传入宿主实际 workspace")
        if location:
            primary = Path(location).expanduser() / "AGENTS.md"
            add(primary, "agent_workspace", "selected")
        notes.append("OpenClaw 仅检查所选 workspace；当前进程 profile/agent 由调用方确认")
    elif agent in ("workbuddy", "codebuddy"):
        default = home / (".workbuddy" if agent == "workbuddy" else ".codebuddy")
        base = conf(os.environ.get("WORKBUDDY_CONFIG_DIR") or default, "CODEBUDDY_CONFIG_DIR")
        primary = base / "CODEBUDDY.md"
        add(primary, "global", "selected")
        add(base / "CODEBUDDY.mdc", "global", "version_dependent")
        rules(base / "rules", "global")
        rules(base / "rules", "global", "**/*.mdc")
        for directory in directories:
            for name in ("CODEBUDDY.md", "AGENTS.md", ".codebuddy/CODEBUDDY.md"):
                add(directory / name, "project", "version_dependent")
        rules(cwd / ".codebuddy/rules", "project")
        rules(cwd / ".codebuddy/rules", "project", "**/*.mdc")
        notes.append("WorkBuddy 5.5.6 已核对 CODEBUDDY.md 与 rules/；其他版本、启用开关、imports 和 paths 需在宿主确认")
    elif agent == "gemini":
        base = conf(home / ".gemini")
        names = ["GEMINI.md"]
        for settings_path in (base / "settings.json", cwd / ".gemini/settings.json"):
            try:
                configured = json.loads(settings_path.read_text()).get("context", {}).get("fileName")
                if configured:
                    names = [configured] if isinstance(configured, str) else configured
            except FileNotFoundError:
                pass
            except (OSError, ValueError, AttributeError):
                notes.append(f"无法解析 {settings_path} 的 context.fileName")
        if not isinstance(names, list) or any(not isinstance(n, str) or Path(n).name != n for n in names):
            names = ["GEMINI.md"]
            notes.append("自定义 context.fileName 需要人工核对")
        primary = base / names[0]
        for name in names:
            add(base / name, "global")
            for directory in directories:
                add(directory / name, "project")
        notes.append("用 /memory show 核对实际加载，修改文件后可能需要 /memory reload")
    elif agent == "opencode":
        base = conf(Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "opencode", "OPENCODE_CONFIG_DIR")
        primary = base / "AGENTS.md"
        disabled = os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE") in ("1", "true") or os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE_PROMPT") in ("1", "true")
        if not primary.exists() and not disabled and (home / ".claude/CLAUDE.md").is_file():
            primary = home / ".claude/CLAUDE.md"
            notes.append("正在检查 Claude 全局回退；新建 OpenCode AGENTS.md 会改变回退行为，不自动新建")
        add(primary, "global", "selected")
        for directory in directories:
            choose(directory, ["AGENTS.md", "CLAUDE.md"] if not disabled else ["AGENTS.md"], "project")
        notes.append("opencode.json/jsonc instructions 引用和命令行覆盖须由调用方核对")
    elif agent == "copilot":
        base = conf(home / ".copilot", "COPILOT_HOME")
        primary = base / "copilot-instructions.md"
        add(primary, "global")
        rules(base / "instructions", "global", "**/*.instructions.md")
        for directory in directories:
            for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md"):
                add(directory / name, "project")
            rules(directory / ".github/instructions", "project", "**/*.instructions.md")
        notes.append("这是 Copilot CLI；用 /instructions 核对启用状态、applyTo 及 COPILOT_CUSTOM_INSTRUCTIONS_DIRS，不代表 IDE 已加载")
    elif agent == "cursor":
        for directory in directories:
            add(directory / "AGENTS.md", "project")
            rules(directory / ".cursor/rules", "project", "**/*.mdc")
        notes.append("Cursor User Rules 位于设置界面，无法从全局 Markdown 判定；请在当前 IDE 核对，不读写私有数据库")
    elif agent == "cline":
        bases = [conf(home / "Documents/Cline/Rules")] if config_dir else [home / "Documents/Cline/Rules", home / ".cline/rules", home / "Cline/Rules"]
        primary = bases[0] / "secret-book.md"
        for base in bases:
            rules(base, "global")
        add(home / ".agents/AGENTS.md", "global")
        for directory in directories:
            if (directory / ".clinerules").is_file():
                add(directory / ".clinerules", "project")
            else:
                rules(directory / ".clinerules", "project")
            rules(directory / ".cline/rules", "project")
        notes.append("Cline 的规则目录候选、开关和路径条件需在当前宿主核对")
    elif agent == "windsurf":
        primary = conf(home / ".codeium/windsurf") / "memories/global_rules.md"
        add(primary, "global")
        for directory in directories:
            rules(directory / ".devin/rules", "project")
            rules(directory / ".windsurf/rules", "project")
            add(directory / "AGENTS.md", "project")
        notes.append("Windsurf/Devin Desktop Cascade 全局规则限 6000 字符；需核对 .devin/.windsurf 兼容顺序和规则触发条件，不代表 Devin Local")
    else:
        defaults = {"qwen": (".qwen", "QWEN.md"), "iflow": (".iflow", "IFLOW.md"),
                    "amp": (".config/amp", "AGENTS.md"), "goose": (".config/goose", "AGENTS.md")}
        directory, name = defaults[agent]
        primary = conf(home / directory) / name
        add(primary, "global", "version_dependent")
        for directory in directories:
            add(directory / name, "project", "version_dependent")
        notes.append("保留既有 Agent 入口；当前版本的加载设置与项目覆盖需在宿主核对")

    active = [e for e in entries if e["applicability"] not in ("shadowed", "disabled") and e["exists"]]
    statuses = {e["status"] for e in active}
    relevant = [e for e in active if e["status"] not in ("missing",)]
    if "outdated" in statuses and "managed_current" in statuses or "conflict" in statuses or "conflict_review" in statuses:
        status = "conflict_review"
    elif "outdated" in statuses:
        status = "outdated"
    elif "custom_review" in statuses:
        status = "custom_review"
    elif "unreadable" in statuses or primary is None:
        status = "unknown"
    elif "managed_current" in statuses:
        status = "managed_current_static"
    else:
        status = "missing"
    return {"schema": "secret-book.agent-rules/v1", "agent": agent, "status": status,
            "cwd": str(cwd), "suggested_target": str(primary.absolute()) if primary else None,
            "files": entries, "project_only": bool(relevant) and all(e["scope"] == "project" for e in relevant),
            "uncertainties": notes, "session_loaded": "not_verified",
            "next_step": "由当前 Agent 核对手工规则、覆盖、禁用条件和实际加载；仅检查不授权写入规则，规则中不得保存业务密钥"}


def command(api, args):
    selected = args.agent or []
    if args.all:
        if args.install or args.remove or selected:
            api.die("--all 只用于显式只读盘点，不能与写操作或 --agent 混用")
        selected = AGENTS
    if not selected or any(agent not in AGENTS for agent in selected):
        api.die("请明确 --agent（" + ", ".join(AGENTS) + "）；--all 仅盘点，不推断当前 Agent")
    reports, written = [], set()
    for agent in selected:
        report = inspect_agent(agent, cwd=Path(args.cwd or Path.cwd()), config_dir=args.config_dir,
                               workspace=args.workspace, agent_id=args.agent_id)
        reports.append(report)
        if not (args.install or args.remove):
            continue
        target = report["suggested_target"]
        if not target:
            report["write_status"] = "manual_required"
            continue
        path = Path(target).resolve()
        if path in written:
            report["write_status"] = "same_physical_file_already_processed"
            continue
        written.add(path)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        status, span = classify(text)
        if status in ("custom_review", "conflict", "conflict_review") and not args.force:
            report["write_status"] = "manual_review_required"
            continue
        if status == "conflict":
            report["write_status"] = "multiple_blocks_require_manual_edit"
            continue
        if args.remove:
            if span is None:
                report["write_status"] = "no_managed_block"
                continue
            new_text = text[:span[0]] + text[span[1]:]
        elif span:
            new_text = text[:span[0]] + rule_block() + text[span[1]:]
        else:
            new_text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + rule_block() + "\n"
        if agent == "windsurf" and len(new_text) > 6000:
            report["write_status"] = "character_limit_exceeded"
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        # Resolve first: shared Codex/Claude symlinks remain intact.
        api._atomic_replace_bytes(path, new_text.encode())
        report["write_status"] = "removed" if args.remove else "installed"
        report["written_path"] = str(path)
    print(json.dumps({"reports": reports, "note": "静态文件检查，不证明当前会话已加载；未保存业务凭证"}, ensure_ascii=False))
