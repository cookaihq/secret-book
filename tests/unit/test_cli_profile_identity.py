import json

import pytest


@pytest.mark.parametrize("source", ["hermes", "openclaw", "lark-channel"])
@pytest.mark.parametrize("stdout", [False, True])
def test_context_guidance_uses_cli_evidence_without_guessing_unknown_host(cli, source, stdout):
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["auth_errors"] = {"work-profile": {
        "type": "config", "subtype": "not_configured",
        "message": f"{source} context detected but lark-cli is not bound to it",
    }}
    state["auth_error_stdout"] = stdout
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    result = cli("config", "save", "--name", "test", "--app-token", "app_test",
                 "--table-id", "tbl_test", "--lark-profile", "work-profile")
    assert result.returncode == 3, result.stderr
    guidance = json.loads(result.stdout)
    assert guidance["error_kind"] == "feishu_cli_context_unbound"
    assert guidance["cli_context"] == {"source": source, "agent": None, "matches_agent": None}
    assert all(action["kind"] != "auth_split_flow" for action in guidance["fix_actions"])


def test_ambient_hermes_variable_alone_does_not_override_valid_cli_identity(cli):
    result = cli("config", "save", "--name", "test", "--app-token", "app_test",
                 "--table-id", "tbl_test", "--lark-profile", "work-profile",
                 extra_env={"HERMES_HOME": "some-other-installation"})
    assert result.returncode == 3
    guidance = json.loads(result.stdout)
    assert guidance["status"] == "confirmation_required"
    assert guidance["observed_identity"]["app_id"] == "cli_test_work"


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


@pytest.mark.parametrize("listed_token,auth_token", [
    ("needs_refresh", "needs_refresh"), ("valid", "needs_refresh"),
    ("needs_refresh", "valid"), ("expired", "valid"), ("valid", "valid"),
])
def test_refreshable_user_queries_original_table_without_login(cli, listed_token, auth_token):
    _save_work_config(cli)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["after_field_list"] = json.loads(json.dumps({
        "profiles": state["profiles"], "auth": state["auth"],
    }))
    state["profiles"][0]["tokenStatus"] = listed_token
    state["auth"]["work-profile"]["identities"]["user"].update(
        status="needs_refresh" if auth_token == "needs_refresh" else "ready",
        available=True, tokenStatus=auth_token,
    )
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")

    result = cli("list", "--config-name", "工作")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    base_calls = [call for call in calls if call[0] == "base"]
    assert [call[1] for call in base_calls] == (
        ["+field-list", "+record-list"] if auth_token == "needs_refresh" else ["+record-list"]
    )
    for call in base_calls:
        for flag, value in [("--profile", "work-profile"), ("--as", "user"),
                            ("--base-token", "app_test_work"), ("--table-id", "tbl_test_work")]:
            assert call[call.index(flag) + 1] == value
        assert "secret" not in call
    assert not any(call[:2] == ["auth", "login"] for call in calls)


@pytest.mark.parametrize("action", ["init-connect", "init-adopt"])
def test_first_connection_refreshes_after_identity_confirmation(cli, action):
    from test_cli_workflows import start, flags

    task = start(cli, role="administrator" if action == "init-adopt" else "consumer")
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["after_field_list"] = json.loads(json.dumps({"profiles": state["profiles"], "auth": state["auth"]}))
    state["profiles"][0]["tokenStatus"] = "needs_refresh"
    state["auth"]["work-profile"]["identities"]["user"].update(
        status="needs_refresh", tokenStatus="needs_refresh")
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    command = (action, "--url", "https://example.feishu.cn/base/test", "--lark-profile", "work-profile", *flags(task))
    pending = cli(*command)
    assert pending.returncode == 3
    confirmation = json.loads(pending.stdout)
    assert confirmation["status"] == "confirmation_required"
    assert "base" not in cli.log_path.read_text(encoding="utf-8")

    result = cli(*command, "--confirm-identity", confirmation["confirmation_token"])

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    field_index = next(i for i, call in enumerate(calls) if call[:2] == ["base", "+field-list"])
    assert any(call[:2] == ["auth", "status"] for call in calls[field_index + 1:])
    assert not any(call[1] in ("+field-create", "+base-create", "+record-batch-update") for call in calls)
    assert "secret" not in json.dumps(calls)


@pytest.mark.parametrize("change", ["app", "user", "still_refreshable"])
def test_refresh_must_finish_with_original_identity_before_reading_records(cli, change):
    _save_work_config(cli)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["profiles"][0]["tokenStatus"] = "needs_refresh"
    state["auth"]["work-profile"]["identities"]["user"].update(
        status="needs_refresh", tokenStatus="needs_refresh",
    )
    after = json.loads(json.dumps({"profiles": state["profiles"], "auth": state["auth"]}))
    if change == "app":
        after["profiles"][0]["appId"] = "cli_different"
    elif change == "user":
        after["auth"]["work-profile"]["identities"]["user"]["openId"] = "ou_different"
    if change != "still_refreshable":
        after["profiles"][0]["tokenStatus"] = "valid"
        after["auth"]["work-profile"]["identities"]["user"].update(status="ready", tokenStatus="valid")
    state["after_field_list"] = after
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")

    result = cli("get", "--id", "sec_example", "--config-name", "工作")

    assert result.returncode == 3, result.stderr
    error = json.loads(result.stdout)
    assert error["error_kind"] == ("feishu_profile_refresh_failed" if change == "still_refreshable"
                                   else "feishu_identity_mismatch")
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert [call[1] for call in calls if call[0] == "base"] == ["+field-list"]


@pytest.mark.parametrize("error,kind,attempts", [
    ({"type": "authentication", "subtype": "refresh_token_revoked"}, "feishu_profile_not_authenticated", 1),
    ({"type": "authentication", "subtype": "refresh_token_expired"}, "feishu_profile_not_authenticated", 1),
    ({"type": "authentication", "subtype": "refresh_server_error", "retryable": True}, "feishu_profile_refresh_failed", 3),
    ({"type": "authorization", "subtype": "permission_denied"}, "feishu_permission_denied", 1),
    ({"type": "authorization", "subtype": "missing_scope"}, "feishu_permission_denied", 1),
    ({"type": "api", "subtype": "rate_limit", "retryable": True}, "feishu_rate_limited", 3),
    ({"type": "network", "subtype": "transport"}, "feishu_network_error", 3),
    ({"type": "network", "subtype": "protocol"}, "feishu_network_error", 1),
    ({"type": "api", "subtype": "server_error", "retryable": True}, "feishu_network_error", 3),
    ({"type": "internal", "subtype": "storage"}, "feishu_profile_status_unknown", 1),
])
def test_refresh_failure_classification_and_bounded_retry(cli, error, kind, attempts):
    _save_work_config(cli)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["profiles"][0]["tokenStatus"] = "needs_refresh"
    state["auth"]["work-profile"]["identities"]["user"].update(
        status="needs_refresh", tokenStatus="needs_refresh")
    state["shortcut_errors"] = {"+field-list": {**error, "message": "synthetic-upstream-secret"}}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")

    result = cli("list", "--config-name", "工作")

    assert result.returncode == 3, result.stderr
    guidance = json.loads(result.stdout)
    assert guidance["error_kind"] == kind
    assert any(a["kind"] == "auth_split_flow" for a in guidance["fix_actions"]) == (kind == "feishu_profile_not_authenticated")
    assert "synthetic-upstream-secret" not in result.stdout + result.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert [call[1] for call in calls if call[0] == "base"] == ["+field-list"] * attempts


def test_read_timeout_is_network_guidance_after_three_bounded_attempts(cli):
    _save_work_config(cli)
    cli.log_path.write_text("", encoding="utf-8")
    result = cli("list", "--config-name", "工作",
                 extra_env={"FAKE_LARK_TIMEOUT_SHORTCUT": "+record-list"})
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["error_kind"] == "feishu_network_error"
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert sum(call[1] == "+record-list" for call in calls) == 3


def test_transient_refresh_recovers_within_bound_and_reads_once(cli):
    _save_work_config(cli)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["after_field_list"] = json.loads(json.dumps({"profiles": state["profiles"], "auth": state["auth"]}))
    state["profiles"][0]["tokenStatus"] = "needs_refresh"
    state["auth"]["work-profile"]["identities"]["user"].update(status="needs_refresh", tokenStatus="needs_refresh")
    state["shortcut_errors"] = {"+field-list": [{"type": "network", "subtype": "timeout"}]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")
    result = cli("list", "--config-name", "工作")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert [call[1] for call in calls if call[0] == "base"] == ["+field-list", "+field-list", "+record-list"]


@pytest.mark.parametrize(
    ("failure", "error_kind"),
    [
        ("missing_profile", "feishu_profile_not_found"),
        ("profile_list_nonzero", "feishu_profile_list_unavailable"),
        ("malformed_profile_list", "feishu_profile_list_unavailable"),
        ("duplicate_profile_list", "feishu_profile_list_unavailable"),
        ("invalid_token", "feishu_profile_not_authenticated"),
        ("auth_nonzero", "feishu_profile_status_unknown"),
        ("invalid_status_shape", "feishu_profile_status_unknown"),
        ("invalid_identities_shape", "feishu_profile_status_unknown"),
        ("unknown_token", "feishu_profile_status_unknown"),
        ("unknown_user_status", "feishu_profile_status_unknown"),
        ("missing_user", "feishu_profile_not_authenticated"),
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
    elif failure == "malformed_profile_list":
        state["profiles"] = [None]
    elif failure == "duplicate_profile_list":
        state["profiles"].append(state["profiles"][0])
    elif failure == "invalid_token":
        next(p for p in state["profiles"] if p["name"] == "work-profile")["tokenStatus"] = "expired"
        state["auth"]["work-profile"]["identities"]["user"].update(
            status="missing", available=False, tokenStatus="expired")
    elif failure == "unknown_token":
        state["profiles"][0]["tokenStatus"] = "new-unknown-state"
    elif failure == "unknown_user_status":
        state["auth"]["work-profile"]["identities"]["user"]["status"] = "unknown"
    elif failure == "missing_user":
        state["auth"]["work-profile"]["identities"]["user"] = {"status": "missing", "available": False}
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
    assert guidance["schema_version"] == "secret-book.profile-guidance/v2"
    assert guidance["error_kind"] == error_kind
    assert guidance["configured_profile"] == "work-profile"
    assert guidance["fix_actions"]
    if error_kind in {
        "feishu_profile_not_found",
        "feishu_profile_not_authenticated",
        "feishu_identity_mismatch",
    }:
        split_actions = [
            action for action in guidance["fix_actions"]
            if action["kind"] == "auth_split_flow"
        ]
        assert len(split_actions) == 1
        action = split_actions[0]
        assert action["profile"] == (
            "<profile-name>" if error_kind == "feishu_profile_not_found"
            else "work-profile"
        )
        assert action["authorization"]["domain"] == "base"
        assert action["authorization"]["scope_hint"] == "base"
        assert action["start_argv_template"] == [
            "lark-cli", "auth", "login", "--profile", action["profile"],
            "--domain", "base", "--no-wait", "--json",
        ]
        assert action["resume_argv_template"] == [
            "lark-cli", "auth", "login", "--profile", action["profile"],
            "--device-code", "<current-device-code>", "--json",
        ]
        assert action["qrcode_argv_template"] == [
            "lark-cli", "auth", "qrcode", "<verification-url>",
            "--profile", action["profile"], "--output", "<temporary-qr-path>",
        ]
        assert action["status_argv"] == [
            "lark-cli", "auth", "status", "--json", "--profile", action["profile"],
        ]
        assert action["request_fields"] == [
            "verification_url", "device_code", "expires_in",
        ]
        assert action["renewal_policy"] == {
            "allowed_reasons": ["expired", "revoked", "user_requested"],
            "requires_user_confirmation": True,
            "max_new_requests_per_task": 1,
            "discard_previous_code_after_creation": True,
            "context_loss": "stop_and_wait_for_explicit_reauthorization",
        }
        assert "<current-device-code>" in json.dumps(action, ensure_ascii=False)
        assert "device-secret-test-123" not in json.dumps(guidance, ensure_ascii=False)
        assert not any(
            item["kind"] in {
                "login_new_profile", "login_profile", "login_expected_app_profile",
            }
            for item in guidance["fix_actions"]
        )
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:1] == ["base"] for call in calls)


@pytest.mark.parametrize("identity_lines", [
    "",
    "SECRET_BOOK_FEISHU_APP_ID=cli_test_work\n",
    "SECRET_BOOK_FEISHU_USER_OPEN_ID=ou_test_work\n",
])
@pytest.mark.parametrize("filename", [".env.local", ".env.secret-book"])
def test_project_config_missing_identity_values_returns_same_layer_guidance(cli, identity_lines, filename):
    project_env = cli.cwd / filename
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
        "source": filename,
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
