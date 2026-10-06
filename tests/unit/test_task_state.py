import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import secret_book as core
from task_state import Journal, TaskStateError


@pytest.fixture
def task(tmp_path):
    context = {"agent": "codex", "cwd": str(tmp_path), "platform": sys.platform,
               "installation": str(Path(core.__file__).resolve().parent.parent)}
    journal = Journal(core, tmp_path / "state.json")
    entry = journal.start("consumer", "inferred", "配置测试工具", context)
    return journal, entry["id"], context


def test_recovery_keeps_role_and_pending_identity_but_no_values(task):
    journal, task_id, context = task
    session = journal.begin(task_id, context, "connect", {"value": "test-secret"},
                            {"profile": "test-profile"})
    session.waiting({"status": "confirmation_required", "confirmation_token": "digest",
                     "observed_identity": {"open_id": "ou_test", "access_token": "hidden"},
                     "device_code": "test-device-code", "raw_response": "test-secret"})
    recovered = Journal(core, journal.path).status(context, task_id)
    assert recovered["role"] == "consumer"
    assert recovered["stage"] == "waiting"
    assert recovered["target"]["profile"] == "test-profile"
    assert recovered["pending"]["observed_identity"] == {"open_id": "ou_test"}
    content = journal.path.read_text(encoding="utf-8")
    assert all(value not in content for value in ("test-secret", "test-device-code", "access_token", "hidden"))


@pytest.mark.parametrize("field,value", [("agent", "workbuddy"), ("cwd", "another-project"),
                                        ("installation", "another-install"), ("platform", "another-os")])
def test_another_context_cannot_consume_pending_task(task, field, value):
    journal, task_id, context = task
    with pytest.raises(TaskStateError, match="宿主、工作目录或安装已变化"):
        journal.begin(task_id, {**context, field: value}, "connect", {})


def test_interrupted_write_is_durable_and_blocks_replay(task):
    journal, task_id, context = task
    session = journal.begin(task_id, context, "create", {})
    session.before_write("remote", "+base-create")
    # No completion callback: equivalent to termination after request dispatch.
    recovered = Journal(core, journal.path)
    assert recovered.status(context, task_id)["stage"] == "verification_required"
    with pytest.raises(TaskStateError, match="禁止重放"):
        recovered.begin(task_id, context, "create", {})
    with pytest.raises(TaskStateError, match="先核对"):
        recovered.close(task_id, context, "completed")
    closed = recovered.close(task_id, context, "cancelled")
    assert closed["last_write"]["destination"] == "+base-create"
    assert recovered.status(context) == []


def test_successful_write_cannot_be_repeated_but_next_step_is_allowed(task):
    journal, task_id, context = task
    session = journal.begin(task_id, context, "create", {})
    session.before_write("remote", "+base-create")
    session.set_target({"app_token": "app_created", "table_id": "tbl_created"})
    session.succeeded()
    replay = journal.begin(task_id, context, "create", {})
    with pytest.raises(TaskStateError, match="已经完成"):
        replay.before_write("remote", "+base-create")
    next_step = journal.begin(task_id, context, "save-connection", {})
    next_step.before_write("local", "config-file")
    next_step.succeeded()
    assert journal.close(task_id, context, "completed")["target"]["table_id"] == "tbl_created"


def test_new_invocation_invalidates_concurrent_stale_writer(task):
    journal, task_id, context = task
    first = journal.begin(task_id, context, "create", {})
    journal.begin(task_id, context, "connect", {})
    with pytest.raises(TaskStateError, match="另一调用"):
        first.before_write("remote", "+base-create")


def test_corrupt_state_is_preserved(task):
    journal, task_id, context = task
    journal.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(TaskStateError, match="无法解析"):
        journal.status(context, task_id)
    assert journal.path.read_text(encoding="utf-8") == "{broken"


def test_target_rejects_secret_fields_before_saving(task):
    journal, task_id, context = task
    session = journal.begin(task_id, context, "connect", {})
    with pytest.raises(TaskStateError, match="无密钥"):
        session.set_target({"device_code": "test-code"})
    assert "test-code" not in journal.path.read_text(encoding="utf-8")


def test_closed_task_does_not_become_permanent_role(task):
    journal, task_id, context = task
    journal.close(task_id, context, "completed")
    assert journal.status(context) == []
    with pytest.raises(TaskStateError, match="已经关闭"):
        journal.begin(task_id, context, "connect", {})
