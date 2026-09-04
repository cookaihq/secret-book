import json

import pytest


def _save_work_config(cli):
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


@pytest.mark.parametrize(
    ("failure", "error_kind"),
    [
        ("missing_profile", "feishu_profile_not_found"),
        ("profile_list_nonzero", "feishu_profile_list_unavailable"),
        ("invalid_token", "feishu_profile_not_authenticated"),
        ("auth_nonzero", "feishu_profile_not_authenticated"),
        ("invalid_status_shape", "feishu_profile_not_authenticated"),
        ("invalid_identities_shape", "feishu_profile_not_authenticated"),
        ("changed_app", "feishu_identity_mismatch"),
        ("changed_user", "feishu_identity_mismatch"),
    ],
)
def test_identity_problem_blocks_before_any_base_call(cli, failure, error_kind):
    _save_work_config(cli)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    if failure == "missing_profile":
        state["profiles"] = [p for p in state["profiles"] if p["name"] != "work-profile"]
    elif failure == "profile_list_nonzero":
        state["profile_exit"] = 1
    elif failure == "invalid_token":
        next(p for p in state["profiles"] if p["name"] == "work-profile")["tokenStatus"] = "expired"
    elif failure == "auth_nonzero":
        state["auth_exit"] = {"work-profile": 1}
    elif failure == "invalid_status_shape":
        state["auth"]["work-profile"] = []
    elif failure == "invalid_identities_shape":
        state["auth"]["work-profile"] = {"identity": "user", "identities": "invalid"}
    elif failure == "changed_app":
        next(p for p in state["profiles"] if p["name"] == "work-profile")["appId"] = "cli_test_other"
    else:
        state["auth"]["work-profile"]["identities"]["user"]["openId"] = "ou_test_other"
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")

    result = cli("list", "--use-global-config")

    assert result.returncode == 3
    guidance = json.loads(result.stdout)
    assert guidance["schema_version"] == "secret-book.profile-guidance/v1"
    assert guidance["error_kind"] == error_kind
    assert guidance["configured_profile"] == "work-profile"
    assert guidance["fix_actions"]
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:1] == ["base"] for call in calls)


@pytest.mark.parametrize("identity_lines", [
    "",
    "SECRET_BOOK_FEISHU_APP_ID=cli_test_work\n",
    "SECRET_BOOK_FEISHU_USER_OPEN_ID=ou_test_work\n",
])
def test_project_config_missing_identity_values_returns_same_layer_guidance(cli, identity_lines):
    project_env = cli.cwd / ".env.local"
    project_env.write_text(
        "SECRET_BOOK_APP_TOKEN=app_test_project\n"
        "SECRET_BOOK_TABLE_ID=tbl_test_project\n"
        "SECRET_BOOK_LARK_PROFILE=work-profile\n"
        + identity_lines,
        encoding="utf-8",
    )

    result = cli("list")

    assert result.returncode == 3
    guidance = json.loads(result.stdout)
    assert guidance["error_kind"] == "feishu_identity_values_missing"
    assert guidance["observed_identity"]["app_id"] == "cli_test_work"
    assert guidance["observed_identity"]["open_id"] == "ou_test_work"
    assert guidance["confirmation_token"]
    assert guidance["config_write_target"] == {
        "source": ".env.local",
        "path": str(project_env),
        "config_id": None,
        "config_name": None,
        "keys": ["SECRET_BOOK_FEISHU_APP_ID", "SECRET_BOOK_FEISHU_USER_OPEN_ID"],
    }
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:1] == ["base"] for call in calls)


def test_process_environment_missing_identity_values_has_executable_guidance(cli):
    result = cli(
        "list",
        extra_env={
            "SECRET_BOOK_APP_TOKEN": "app_test_process",
            "SECRET_BOOK_TABLE_ID": "tbl_test_process",
            "SECRET_BOOK_LARK_PROFILE": "work-profile",
        },
    )

    assert result.returncode == 3
    guidance = json.loads(result.stdout)
    assert guidance["error_kind"] == "feishu_identity_values_missing"
    assert guidance["config_write_target"]["source"] == "process_env"
    assert guidance["config_write_target"]["path"] is None
    assert "同一进程环境" in guidance["fix_actions"][0]["description"]
    assert "指定文件" not in guidance["fix_actions"][0]["description"]
