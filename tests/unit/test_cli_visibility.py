import json

import pytest


def _save_config(cli):
    args = (
        "config", "save",
        "--name", "工作",
        "--app-token", "app_test_work",
        "--table-id", "tbl_test_work",
        "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    saved = cli(*args, "--confirm-identity", token)
    assert saved.returncode == 0, saved.stderr


@pytest.mark.parametrize("visible_to", [
    {"id": "ou_other", "name": "Other User"},
    [{"name": "Missing ID"}],
    ["ou_other"],
])
def test_list_rejects_malformed_visible_to_instead_of_treating_record_as_unrestricted(
    cli, visible_to,
):
    _save_config(cli)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test_work": [{
        "_record_id": "rec_restricted",
        "id": "sec_restricted1",
        "name": "must-not-be-listed",
        "service": "example",
        "account": "other",
        "purpose": "restricted",
        "secret": "TOKEN=secret-value",
        "expires_at": None,
        "visible_to": visible_to,
    }]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")

    result = cli("list", "--use-global-config")

    assert result.returncode == 1
    assert "visible_to" in result.stderr
    assert "Traceback" not in result.stderr
    assert "must-not-be-listed" not in result.stdout


def test_list_returns_unrestricted_and_current_user_records_only(cli):
    _save_config(cli)
    base_record = {
        "service": "example",
        "account": "test",
        "purpose": "visibility check",
        "secret": "TOKEN=secret-value",
        "expires_at": None,
    }
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test_work": [
        {
            **base_record,
            "_record_id": "rec_open",
            "id": "sec_open000001",
            "name": "unrestricted",
            "visible_to": None,
        },
        {
            **base_record,
            "_record_id": "rec_mine",
            "id": "sec_mine000001",
            "name": "visible-to-me",
            "visible_to": [{"id": "ou_test_work", "name": "Test User"}],
        },
        {
            **base_record,
            "_record_id": "rec_other",
            "id": "sec_other0001",
            "name": "visible-to-other",
            "visible_to": [{"id": "ou_other", "name": "Other User"}],
        },
    ]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")

    result = cli("list", "--use-global-config")

    assert result.returncode == 0, result.stderr
    assert "unrestricted" in result.stdout
    assert "visible-to-me" in result.stdout
    assert "visible-to-other" not in result.stdout
