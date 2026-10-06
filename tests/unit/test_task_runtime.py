import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import secret_book as core
from task_state import Journal, TaskStateError
from task_runtime import context, execute


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(core, "_capture_profile_identity", lambda profile: {
        "lark_profile": profile, "app_id": "cli_test", "open_id": "ou_test", "user": "Test User"})
    journal = Journal(core, tmp_path / "state.json")
    return journal


@pytest.mark.parametrize("agent", ["codex", "claude-code", "workbuddy"])
def test_consumer_cannot_create_remote_table_in_task(runtime, agent):
    entry = runtime.start("consumer", "explicit", "取用已有凭证", context(core, agent))
    args = core.build_parser().parse_args(["init-create", "--lark-profile", "test"])
    with pytest.raises(TaskStateError, match="当前任务角色"):
        execute(core, args, runtime, entry["id"], agent)
    assert runtime.status(context(core, agent), entry["id"])["stage"] == "ready"


def test_developer_without_real_login_can_start_and_finish_local_work(runtime):
    entry = runtime.start("developer", "inferred", "开发凭证声明与模拟测试", context(core, "codex"))
    assert runtime.close(entry["id"], context(core, "codex"), "completed")["outcome"] == "completed"


def test_changed_create_input_requires_new_confirmation_before_write(runtime, monkeypatch):
    # The real initialization action reaches its identity gate; a write would fail the test.
    monkeypatch.setattr(core, "_lark_exec", lambda *args: pytest.fail("must not access Base"))
    entry = runtime.start("administrator", "explicit", "创建测试表", context(core, "codex"))
    argv = ["init-create", "--lark-profile", "test", "--base-name", "first"]
    with pytest.raises(core.ProfileGuidance) as pending:
        execute(core, core.build_parser().parse_args(argv), runtime, entry["id"], "codex")
    token = pending.value.payload["confirmation_token"]
    argv[-1] = "changed"
    with pytest.raises(core.ProfileGuidance) as changed:
        execute(core, core.build_parser().parse_args([*argv, "--confirm-identity", token]),
                runtime, entry["id"], "codex")
    assert changed.value.payload["status"] == "confirmation_required"
    assert changed.value.payload["confirmation_token"] != token
    assert runtime.status(context(core, "codex"), entry["id"])["target"]["base_name"] == "changed"


def test_pending_identity_survives_read_only_diagnostic(runtime, monkeypatch):
    monkeypatch.setattr(core, "_lark_exec", lambda *args: pytest.fail("must not access Base"))
    entry = runtime.start("administrator", "explicit", "创建测试表", context(core, "codex"))
    args = core.build_parser().parse_args(["init-create", "--lark-profile", "test"])
    with pytest.raises(core.ProfileGuidance):
        execute(core, args, runtime, entry["id"], "codex")
    before = runtime.status(context(core, "codex"), entry["id"])
    monkeypatch.setattr(core, "cmd_config_list", lambda args: None)
    execute(core, core.build_parser().parse_args(["config", "list"]), runtime, entry["id"], "codex")
    assert runtime.status(context(core, "codex"), entry["id"])["pending"] == before["pending"]


def test_interruption_after_request_dispatch_retains_recovery_target(runtime, monkeypatch):
    def interrupted(args):
        core._task_target(app_token="app_created", table_id="tbl_created")
        core._task_before_write("remote", "+base-create")
        raise KeyboardInterrupt
    monkeypatch.setattr(core, "cmd_init_create", interrupted)
    entry = runtime.start("administrator", "explicit", "创建测试表", context(core, "codex"))
    args = core.build_parser().parse_args(["init-create", "--lark-profile", "test"])
    with pytest.raises(KeyboardInterrupt):
        execute(core, args, runtime, entry["id"], "codex")
    recovered = runtime.status(context(core, "codex"), entry["id"])
    assert recovered["stage"] == "verification_required"
    assert recovered["target"]["table_id"] == "tbl_created"
    assert core._ACTIVE_TASK is None
    with pytest.raises(TaskStateError, match="禁止重放"):
        execute(core, args, runtime, entry["id"], "codex")
