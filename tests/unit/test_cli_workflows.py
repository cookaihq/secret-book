"""Role routing and resumption through independent CLI processes; no live accounts."""
import json
import shlex

import pytest

from test_cli_readonly import prepare_record
from test_cli_consumers import consumer, inspect, args as consumer_args


def start(cli, role="consumer", agent="codex"):
    result = cli("workflow", "start", "--role", role, "--basis", "inferred",
                 "--goal", "测试角色与首次使用", "--agent", agent,
                 extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["task"]["id"]


def flags(task_id, agent="codex"):
    return ("--workflow", task_id, "--workflow-agent", agent)


def status(cli, task_id, agent="codex"):
    result = cli("workflow", "status", "--id", task_id, "--agent", agent,
                 extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["task"]


def calls(cli):
    return [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()] if cli.log_path.exists() else []


def test_ambiguous_role_requests_selection_before_login_or_state_write(cli):
    result = cli("workflow", "start", "--goal", "使用 Secret Book", "--agent", "codex",
                 extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "role_required"
    assert not calls(cli)
    assert not (cli.home / ".config/secret-book/workflows.json").exists()


@pytest.mark.parametrize("role", ["administrator", "maintainer", "consumer", "developer"])
def test_each_role_can_start_without_connection_or_login_and_close(cli, role):
    task_id = start(cli, role)
    state_path = cli.home / ".config/secret-book/workflows.json"
    cli.assert_private(state_path)
    assert not (state_path.parent / ".env").exists()
    assert status(cli, task_id)["role"] == role
    assert cli("workflow", "finish", "--id", task_id, "--agent", "codex").returncode == 0
    open_tasks = cli("workflow", "status", "--agent", "codex")
    assert json.loads(open_tasks.stdout)["tasks"] == []
    assert not calls(cli)


@pytest.mark.parametrize("agent", ["codex", "claude-code", "workbuddy"])
def test_consumer_connects_read_only_and_saves_own_first_connection_after_resume(cli, agent):
    task_id = start(cli, agent=agent)
    command = ("init-connect", "--url", "https://example.feishu.cn/base/test",
               "--lark-profile", "work-profile", *flags(task_id, agent))
    pending = cli(*command)
    assert pending.returncode == 3, pending.stderr
    guidance = json.loads(pending.stdout)
    recovered = status(cli, task_id, agent)
    assert recovered["pending"]["confirmation_token"] == guidance["confirmation_token"]
    assert recovered["pending"]["observed_identity"]["open_id"] == "ou_test_work"
    assert "source_confirmation_token" not in recovered["pending"]
    assert not any(call[0] == "base" for call in calls(cli))

    connected = cli(*command, "--confirm-identity", recovered["pending"]["confirmation_token"])
    assert connected.returncode == 0, connected.stderr
    result = json.loads(connected.stdout)
    assert result["status"] == "connected_read_only"
    assert result["visible_records"] == 0
    assert not (cli.home / ".config/secret-book/.env").exists()
    base_calls = [call for call in calls(cli) if call[0] == "base"]
    assert [call[1] for call in base_calls] == ["+url-resolve", "+field-list", "+record-list"]
    assert all("secret" not in call for call in base_calls)

    handoff = shlex.split(result["save_command"])[5:]
    handoff[handoff.index("<名称>")] = "我的连接"
    saved = cli(*handoff)
    assert saved.returncode == 0, saved.stderr
    config_path = cli.home / ".config/secret-book/.env"
    assert config_path.exists()
    assert "ou_test_work" in config_path.read_text(encoding="utf-8")
    current = cli("config", "list")
    assert "我的连接" in current.stdout
    assert "是" in current.stdout
    assert status(cli, task_id, agent)["stage"] == "step_complete"


def test_consumer_missing_columns_never_adopts_or_creates_table(cli):
    task_id = start(cli)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["fields"] = [{"name": "id", "type": "text"}]
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    command = ("init-connect", "--url", "https://example.feishu.cn/base/test",
               "--lark-profile", "work-profile", *flags(task_id))
    token = json.loads(cli(*command).stdout)["confirmation_token"]
    checked = cli(*command, "--confirm-identity", token)
    assert checked.returncode == 3
    assert json.loads(checked.stdout)["status"] == "table_fields_missing"
    assert all(call[1] in ("+url-resolve", "+field-list") for call in calls(cli) if call[0] == "base")
    refused = cli("init-adopt", "--url", "https://example.feishu.cn/base/test",
                  "--lark-profile", "work-profile", *flags(task_id))
    assert json.loads(refused.stdout)["status"] == "role_mismatch"


def test_maintainer_repairs_one_id_without_reading_secret_and_consumer_cannot(cli):
    prepare_record(cli)
    user_task = start(cli)
    denied = cli("repair-ids", "--name", "manual-entry", "--use-global-config", *flags(user_task))
    assert denied.returncode == 3
    assert json.loads(denied.stdout)["status"] == "role_mismatch"
    assert not calls(cli)
    task_id = start(cli, "maintainer")
    command = ("repair-ids", "--name", "manual-entry", "--use-global-config", *flags(task_id))
    pending = cli(*command)
    assert pending.returncode == 3
    assert not any(call[1] == "+record-batch-update" for call in calls(cli))
    token = status(cli, task_id)["pending"]["confirmation_token"]
    repaired = cli(*command, "--confirm", token)
    assert repaired.returncode == 0, repaired.stderr
    assert json.loads(repaired.stdout)["status"] == "id_repaired"
    assert all("secret" not in call for call in calls(cli))
    assert cli("get", "--name", "manual-entry", "--use-global-config", *flags(user_task)).returncode == 0


def test_admin_creation_result_is_recovered_and_repeat_creation_is_blocked(cli):
    task_id = start(cli, "administrator")
    command = ("init-create", "--lark-profile", "work-profile", *flags(task_id))
    token = json.loads(cli(*command).stdout)["confirmation_token"]
    created = cli(*command, "--confirm-identity", token)
    assert created.returncode == 0, created.stderr
    recovered = status(cli, task_id)
    assert recovered["target"]["table_id"] == "tbl_test_created"
    # Even after another identity preview, the completed write must not execute again.
    retry_pending = cli(*command)
    retry_token = json.loads(retry_pending.stdout)["confirmation_token"]
    repeated = cli(*command, "--confirm-identity", retry_token)
    assert repeated.returncode == 3
    assert json.loads(repeated.stdout)["status"] == "operation_completed"
    assert sum(call[1] == "+base-create" for call in calls(cli)) == 1


def test_ambiguous_remote_write_survives_process_exit_and_blocks_replay(cli):
    task_id = start(cli, "administrator")
    command = ("init-create", "--lark-profile", "work-profile", *flags(task_id))
    token = json.loads(cli(*command).stdout)["confirmation_token"]
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["shortcut_errors"] = {"+base-create": {"type": "network", "message": "connection reset"}}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    failed = cli(*command, "--confirm-identity", token)
    assert failed.returncode == 121
    assert status(cli, task_id)["stage"] == "verification_required"
    retried = cli(*command, "--confirm-identity", token)
    assert json.loads(retried.stdout)["status"] == "verification_required"
    assert sum(call[1] == "+base-create" for call in calls(cli)) == 1
    closed = cli("workflow", "finish", "--id", task_id, "--agent", "codex")
    assert closed.returncode == 3
    cancelled = cli("workflow", "finish", "--id", task_id, "--agent", "codex", "--outcome", "cancelled")
    assert cancelled.returncode == 0
    assert json.loads(cancelled.stdout)["task"]["last_write"]["destination"] == "+base-create"


def test_another_host_or_task_cannot_use_pending_identity_confirmation(cli):
    task_id = start(cli)
    command = ("init-connect", "--url", "https://example.feishu.cn/base/test", "--lark-profile", "work-profile")
    token = json.loads(cli(*command, *flags(task_id)).stdout)["confirmation_token"]
    wrong_host = cli(*command, *flags(task_id, "workbuddy"), "--confirm-identity", token)
    assert json.loads(wrong_host.stdout)["status"] == "context_changed"
    another_task = start(cli)
    wrong_task = cli(*command, *flags(another_task), "--confirm-identity", token)
    assert wrong_task.returncode == 3
    assert json.loads(wrong_task.stdout)["confirmation_token"] != token
    assert not any(call[0] == "base" for call in calls(cli))


def test_consumer_file_confirmation_resumes_in_same_task_and_stores_no_values(cli, consumer):
    task_id = start(cli)
    report = inspect(cli)
    command = (*consumer_args(consumer, report), *flags(task_id))
    preview = cli(*command)
    assert preview.returncode == 3, preview.stderr
    token = status(cli, task_id)["pending"]["confirmation_token"]
    checked = cli("configure-status", "--requirements", str(consumer), *flags(task_id))
    assert checked.returncode == 0
    assert status(cli, task_id)["pending"]["confirmation_token"] == token
    written = cli(*command, "--confirm", token)
    assert written.returncode == 0, written.stderr
    assert json.loads(written.stdout)["status"] == "written"
    assert "synthetic-secret-one" in (cli.home / ".config/example/.env").read_text(encoding="utf-8")
    saved_state = (cli.home / ".config/secret-book/workflows.json").read_text(encoding="utf-8")
    assert "synthetic-secret-one" not in saved_state
    assert status(cli, task_id)["stage"] == "step_complete"


def test_empty_global_config_does_not_hide_complete_project_connection(cli):
    prepare_record(cli, record_id="sec_existing")
    (cli.home / ".config/secret-book/.env").unlink()
    (cli.cwd / ".env.local").write_text(
        "SECRET_BOOK_APP_TOKEN=app_test_work\nSECRET_BOOK_TABLE_ID=tbl_test_work\n"
        "SECRET_BOOK_LARK_PROFILE=work-profile\nSECRET_BOOK_FEISHU_APP_ID=cli_test_work\n"
        "SECRET_BOOK_FEISHU_USER_OPEN_ID=ou_test_work\n", encoding="utf-8")
    task_id = start(cli)
    listed = cli("list", *flags(task_id))
    assert listed.returncode == 0, listed.stderr
    assert "manual-entry" in listed.stdout
    assert all(call[1] == "+record-list" for call in calls(cli) if call[0] == "base")


@pytest.mark.parametrize("field,value", [("stage", {}), ("target", []), ("pending", {})])
def test_corrupt_task_state_is_reported_without_overwrite_or_traceback(cli, field, value):
    task_id = start(cli)
    path = cli.home / ".config/secret-book/workflows.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tasks"][task_id][field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    result = cli("workflow", "status", "--id", task_id, "--agent", "codex")
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "state_invalid"
    assert "Traceback" not in result.stderr
    assert path.read_bytes() == before
