import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from agent_rules import AGENTS, rule_block


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def check(cli, agent, *extra, env=None):
    result = cli("agent-rule", "--agent", agent, *extra, extra_env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["reports"][0]


def test_current_agent_must_be_explicit(cli):
    result = cli("agent-rule")
    assert result.returncode == 1
    assert "--agent" in result.stderr
    inventory = cli("agent-rule", "--all")
    assert inventory.returncode == 0
    assert {r["agent"] for r in json.loads(inventory.stdout)["reports"]} == set(AGENTS)


def test_codex_override_and_symlink_do_not_imply_session_loaded(cli):
    base = cli.home / ".codex"
    shared = write(base / "AGENTS.md", rule_block())
    override = write(base / "AGENTS.override.md", "Only project rules today.\n")
    report = check(cli, "codex")
    assert report["status"] == "missing"
    assert next(f for f in report["files"] if f["path"] == str(shared))["applicability"] == "shadowed"
    override.unlink()
    claude = cli.home / ".claude/CLAUDE.md"
    claude.parent.mkdir(); claude.symlink_to(shared)
    report = check(cli, "claude-code")
    assert report["status"] == "managed_current_static"
    assert report["session_loaded"] == "not_verified"
    assert next(f for f in report["files"] if f["path"] == str(claude))["real_path"] == str(shared)
    # An explicitly authorized install updates the entity and preserves the symlink.
    shared.write_text("Keep user rules.\n<!-- secret-book:fallback-rule v1 -->\nold injection\n<!-- /secret-book:fallback-rule -->\n")
    result = cli("agent-rule", "--install", "--agent", "codex", "--agent", "claude-code")
    assert result.returncode == 0
    assert claude.is_symlink()
    assert shared.read_text().count("<!-- secret-book:fallback-rule v6 -->") == 1
    assert shared.read_text().startswith("Keep user rules.")
    assert ".worktrees/" not in shared.read_text()
    assert json.loads(result.stdout)["reports"][1]["write_status"] == "same_physical_file_already_processed"


def test_old_manual_conflicting_and_project_only_rules_are_distinct(cli):
    path = write(cli.home / ".codex/AGENTS.md", "<!-- secret-book:fallback-rule v1 -->\nrun --auto\n<!-- /secret-book:fallback-rule -->")
    before = path.read_bytes()
    assert check(cli, "codex")["status"] == "outdated"
    assert path.read_bytes() == before
    path.write_text("Use secret-book only for this account.")
    assert check(cli, "codex")["status"] == "custom_review"
    path.write_text(rule_block())
    project = write(cli.cwd / "AGENTS.md", "Do not use secret-book in this project.")
    assert check(cli, "codex")["status"] == "custom_review"
    project.write_text("<!-- secret-book:fallback-rule v1 -->\nrun --auto\n<!-- /secret-book:fallback-rule -->")
    assert check(cli, "codex")["status"] == "conflict_review"
    path.unlink(); project.write_text(rule_block())
    assert check(cli, "codex")["project_only"] is True


def test_custom_config_dirs_and_workbuddy_actual_file_name(cli):
    base = cli.home / "custom-agent"
    write(base / "AGENTS.override.md", rule_block())
    assert check(cli, "codex", env={"CODEX_HOME": str(base)})["status"] == "managed_current_static"
    work = write(cli.home / ".workbuddy/CODEBUDDY.md", rule_block())
    report = check(cli, "workbuddy")
    assert report["suggested_target"] == str(work)
    assert report["status"] == "managed_current_static"
    assert not any(f["path"].endswith("WORKBUDDY.md") for f in report["files"])


def test_openclaw_multiple_instances_json5_and_workspace_are_not_guessed(cli):
    config = write(cli.home / ".openclaw/openclaw.json", json.dumps({"agents": {"entries": [
        {"id": "a", "workspace": str(cli.home / "a")}, {"id": "b", "workspace": str(cli.home / "b")}]}}))
    assert check(cli, "openclaw")["status"] == "unknown"
    target = write(cli.home / "b/AGENTS.md", rule_block())
    assert check(cli, "openclaw", "--agent-id", "b")["suggested_target"] == str(target)
    config.write_text("{ // JSON5\n agents: {} }")
    assert check(cli, "openclaw")["status"] == "unknown"
    assert check(cli, "openclaw", "--workspace", str(target.parent))["status"] == "managed_current_static"


def test_hermes_cursor_and_disabled_rules_expose_limitations(cli):
    soul = write(cli.home / ".hermes/SOUL.md", "You are a helpful assistant.")
    report = check(cli, "hermes")
    assert any(f["path"] == str(soul) for f in report["files"])
    assert report["suggested_target"] == str(cli.cwd / ".hermes.md")
    assert check(cli, "cursor")["status"] == "unknown"
    write(cli.home / ".workbuddy/rules/secret.md", "---\nenabled: false\n---\n" + rule_block())
    report = check(cli, "workbuddy")
    assert report["status"] == "missing"
    assert any(f["applicability"] == "disabled" for f in report["files"])


def test_opencode_preserves_claude_fallback_and_reads_project_fallback(cli):
    target = write(cli.home / ".claude/CLAUDE.md", rule_block())
    report = check(cli, "opencode")
    assert report["suggested_target"] == str(target)
    assert not (cli.home / ".config/opencode/AGENTS.md").exists()
    disabled = check(cli, "opencode", env={"OPENCODE_DISABLE_CLAUDE_CODE": "1"})
    assert disabled["status"] == "missing"
