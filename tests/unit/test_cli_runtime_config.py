import json


def _save_config(cli, *, name, app_token, table_id, profile):
    args = (
        "config", "save",
        "--name", name,
        "--app-token", app_token,
        "--table-id", table_id,
        "--lark-profile", profile,
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    saved = cli(*args, "--confirm-identity", token)
    assert saved.returncode == 0, saved.stderr


def test_business_command_uses_only_the_current_named_config(cli):
    _save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    _save_config(
        cli,
        name="个人",
        app_token="app_test_personal",
        table_id="tbl_test_personal",
        profile="personal-profile",
    )
    assert cli("config", "use", "--name", "个人").returncode == 0
    cli.log_path.write_text("", encoding="utf-8")

    result = cli("list", "--use-global-config")

    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert calls[0] == ["profile", "list"]
    assert calls[1] == ["auth", "status", "--json", "--profile", "personal-profile"]
    base_calls = [call for call in calls if call[:2] == ["base", "+record-list"]]
    assert len(base_calls) == 1
    assert "personal-profile" in base_calls[0]
    assert "app_test_personal" in base_calls[0]
    assert "tbl_test_personal" in base_calls[0]
    rendered_calls = json.dumps(calls)
    assert "app_test_work" not in rendered_calls
    assert "tbl_test_work" not in rendered_calls


def test_project_resource_config_is_atomic_and_overrides_global_current(cli):
    _save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    _save_config(
        cli,
        name="个人",
        app_token="app_test_personal",
        table_id="tbl_test_personal",
        profile="personal-profile",
    )
    project_env = cli.cwd / ".env.local"
    project_env.write_text("SECRET_BOOK_APP_TOKEN=app_test_project\n", encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")

    incomplete = cli("list", "--use-global-config")

    assert incomplete.returncode == 1
    assert ".env.local 中的令牌配置不完整" in incomplete.stderr
    assert "五个字段必须来自同一层" in incomplete.stderr
    assert cli.log_path.read_text(encoding="utf-8") == ""

    project_env.write_text(
        "\n".join([
            "SECRET_BOOK_APP_TOKEN=app_test_project",
            "SECRET_BOOK_TABLE_ID=tbl_test_project",
            "SECRET_BOOK_LARK_PROFILE=work-profile",
            "SECRET_BOOK_FEISHU_APP_ID=cli_test_work",
            "SECRET_BOOK_FEISHU_USER_OPEN_ID=ou_test_work",
        ]) + "\n",
        encoding="utf-8",
    )
    cli.log_path.write_text("", encoding="utf-8")

    overridden = cli("list", "--use-global-config")
    switched = cli("config", "use", "--name", "个人")

    assert overridden.returncode == 0, overridden.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    base_call = next(call for call in calls if call[:2] == ["base", "+record-list"])
    assert "app_test_project" in base_call
    assert "tbl_test_project" in base_call
    assert "app_test_work" not in json.dumps(calls)
    assert switched.returncode == 0, switched.stderr
    assert "当前目录的 .env.local 定义了令牌配置" in switched.stderr
    assert "业务命令仍会优先使用项目配置" in switched.stderr
