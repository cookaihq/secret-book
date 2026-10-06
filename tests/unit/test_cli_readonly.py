import json

import pytest


def prepare_record(cli, *, record_id=""):
    args = ("config", "save", "--name", "work", "--app-token", "app_test_work",
            "--table-id", "tbl_test_work", "--lark-profile", "work-profile")
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    assert cli(*args, "--confirm-identity", token).returncode == 0
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test_work": [{
        "_record_id": "rec_manual", "id": record_id, "name": "manual-entry",
        "service": "example", "account": "test", "purpose": "read only access",
        "secret": "TOKEN=synthetic-secret", "visible_to": None,
    }]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")


def assert_only_reads(cli):
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert all(call[1] == "+record-list" for call in calls if call[0] == "base")


def test_list_manual_entries_without_requiring_write_permission(cli):
    prepare_record(cli)
    result = cli("list", "--use-global-config")
    assert result.returncode == 0, result.stderr
    assert "manual-entry" in result.stdout
    assert "缺少 ID" in result.stdout
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert_only_reads(cli)


@pytest.mark.parametrize("action", ["get", "run", "copy"])
def test_missing_id_requires_maintainer_without_mutating_table_or_using_value(cli, action):
    prepare_record(cli)
    command = ("--", "must-not-be-executed") if action == "run" else ()
    result = cli(action, "--name", "manual-entry", "--use-global-config", *command)
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["status"] == "record_id_missing"
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert_only_reads(cli)


def test_get_existing_id_remains_read_only(cli):
    prepare_record(cli, record_id="sec_existing001")
    result = cli("get", "--name", "manual-entry", "--use-global-config")
    assert result.returncode == 0, result.stderr
    assert "TOKEN" in result.stdout
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert_only_reads(cli)
