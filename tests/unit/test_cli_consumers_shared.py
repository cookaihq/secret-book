"""Plugin shared reports preserve explicit null callers across public CLI recovery."""
import json

import pytest

from test_cli_consumers import args, configure, consumer, inspect, write
from test_cli_workflows import calls, flags, start, status


def test_shared_save_and_public_confirmation_resume(cli, consumer):
    sibling = write(cli.home / ".config/example/example-b/.env", "EXAMPLE_KEY=sibling-only\n")
    ordinary = write(cli.home / ".config/example-a/.env", "EXAMPLE_KEY=ordinary-only\n")
    report = inspect(cli, skill=None)
    command = args(consumer, report)
    preview = cli(*command)
    assert preview.returncode == 3, preview.stdout + preview.stderr
    review = json.loads(preview.stdout)
    assert review["review"]["skill"] is None
    target = cli.home / ".config/example/.env"
    assert review["review"]["changes"][0]["path"] == str(target)
    assert not target.exists()
    token = review["confirmation_token"]
    pending = cli("configure-status", "--requirements", str(consumer),
                  extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert json.loads(pending.stdout)["pending"][token]["review"]["skill"] is None
    saved = cli(*command, "--confirm", token)
    assert saved.returncode == 0, saved.stdout + saved.stderr
    assert target.read_text() == "EXAMPLE_KEY='synthetic-secret-one'\n"
    assert sibling.read_text() == "EXAMPLE_KEY=sibling-only\n"
    assert ordinary.read_text() == "EXAMPLE_KEY=ordinary-only\n"
    cli.assert_private(target)


@pytest.mark.parametrize("location,filename", [
    ("project", ".env.local"), ("project", ".env"),
    ("global", ".env.local"), ("global", ".env"),
])
def test_shared_repair_preserves_source_despite_requested_scope(cli, consumer, location, filename):
    root = cli.cwd if location == "project" else cli.home / ".config/example"
    target = write(root / filename, "# keep\nEXAMPLE_KEY=old\nOTHER=keep\n")
    report = inspect(cli, skill=None, global_enabled=location == "global")
    preview, saved = configure(cli, consumer, report, "--scope", "global" if location == "project" else "project")
    assert preview["review"]["skill"] is None
    assert saved["written"] == [{"path": str(target), "keys": ["EXAMPLE_KEY"]}]
    assert target.read_text() == "# keep\nEXAMPLE_KEY='synthetic-secret-one'\nOTHER=keep\n"
    alternate = cli.home / ".config/example/.env" if location == "project" else cli.cwd / ".env.local"
    assert not alternate.exists()


@pytest.mark.parametrize("agent", ["codex", "claude-code", "workbuddy"])
@pytest.mark.parametrize("outcome", ["completed", "cancelled"])
def test_shared_workflow_public_recovery_and_cancellation(cli, consumer, agent, outcome):
    """Host labels test the CLI context contract, not client discovery or UI behavior."""
    task_id = start(cli, agent=agent)
    command = list(args(consumer, inspect(cli, skill=None)))
    command[command.index("--agent") + 1] = agent
    command += flags(task_id, agent)
    preview = cli(*command)
    assert preview.returncode == 3, preview.stderr
    review = json.loads(preview.stdout)["review"]
    assert review["skill"] is None
    recovered = status(cli, task_id, agent)
    token = recovered["pending"]["confirmation_token"]
    assert token == json.loads(preview.stdout)["confirmation_token"]
    pending = cli("configure-status", "--requirements", str(consumer), *flags(task_id, agent),
                  extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert pending.returncode == 0, pending.stderr
    assert [item["review"] for item in json.loads(pending.stdout)["pending"].values()] == [review]
    assert status(cli, task_id, agent)["pending"]["confirmation_token"] == token
    target = cli.home / ".config/example/.env"
    assert not target.exists()
    outputs = preview.stdout + preview.stderr + pending.stdout + pending.stderr + json.dumps(recovered)
    if outcome == "completed":
        saved = cli(*command, "--confirm", token)
        assert saved.returncode == 0, saved.stdout + saved.stderr
        assert target.read_text() == "EXAMPLE_KEY='synthetic-secret-one'\n"
        outputs += saved.stdout + saved.stderr
    finished = cli("workflow", "finish", "--id", task_id, "--agent", agent, "--outcome", outcome)
    assert finished.returncode == 0, finished.stderr
    closed = status(cli, task_id, agent)
    assert closed["closed"] and closed["outcome"] == outcome
    if outcome == "cancelled":
        replay = cli(*command, "--confirm", token, extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
        assert replay.returncode == 3
        assert json.loads(replay.stdout)["status"] == "task_closed"
        assert not target.exists()
        outputs += replay.stdout + replay.stderr
    outputs += finished.stdout + json.dumps(closed) + cli.log_path.read_text(encoding="utf-8")
    assert "synthetic-secret-one" not in outputs
    base_calls = [call for call in calls(cli) if call[0] == "base"]
    assert base_calls and all(call[1] == "+record-list" for call in base_calls)
    assert all(call[call.index("--profile") + 1] == "work-profile" for call in base_calls)


def test_shared_global_disabled_requires_explicit_project_target(cli, consumer):
    report = inspect(cli, skill=None, global_enabled=False)
    blocked = cli(*args(consumer, report), extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert json.loads(blocked.stdout)["status"] == "global_disabled"
    _, saved = configure(cli, consumer, report, "--scope", "project")
    assert saved["written"][0]["path"] == str(cli.cwd / ".env.local")
    assert not (cli.home / ".config/example/.env").exists()


@pytest.mark.parametrize("existing", [False, True])
def test_shared_skill_only_is_rejected_before_any_lookup(cli, consumer, existing):
    if existing:
        write(cli.cwd / ".env.local", "EXAMPLE_KEY=old\n")
    report = inspect(cli, skill=None)
    result = cli(*args(consumer, report, "--skill-only"), extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode == 1
    assert "--skill-only" in result.stderr and "真实" in result.stderr
    assert not (cli.home / ".config/example/.env.None").exists()


@pytest.mark.parametrize("scope,relative", [
    ("cwd", ".env.None"), ("cwd", ".env.example-a"),
    ("home", ".config/example/.env.example-a"),
    ("home", ".config/example/example-b/.env"),
    ("home", ".config/example-a/.env"),
])
def test_shared_rejects_all_skill_specific_layers(cli, consumer, scope, relative):
    path = inspect(cli, skill=None)
    report = json.loads(path.read_text())
    forbidden = (cli.cwd if scope == "cwd" else cli.home) / relative
    report["layers"].append({"path": str(forbidden), "revision": None})
    path.write_text(json.dumps(report))
    result = cli(*args(consumer, path), extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode == 1 and "超出" in result.stderr


@pytest.mark.parametrize("kind,omit_skill", [("plugin", True), ("skill", False)])
def test_null_caller_requires_explicit_plugin_shared_identity(cli, consumer, kind, omit_skill):
    path = inspect(cli, skill=None)
    report = json.loads(path.read_text())
    if omit_skill:
        del report["skill"]
    declaration = json.loads(consumer.read_text())
    declaration["consumer"]["kind"] = kind
    report["consumer"]["kind"] = kind
    consumer.write_text(json.dumps(declaration))
    path.write_text(json.dumps(report))
    result = cli(*args(consumer, path), extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode == 1 and "调用者" in result.stderr
