import json
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest


def save_config(cli, *, name, app_token, table_id, profile):
    args = (
        "config", "save",
        "--name", name,
        "--app-token", app_token,
        "--table-id", table_id,
        "--lark-profile", profile,
    )
    pending = cli(*args)
    assert pending.returncode == 3
    token = json.loads(pending.stdout)["confirmation_token"]
    return cli(*args, "--confirm-identity", token), token


def test_first_named_config_requires_identity_confirmation_and_becomes_current(cli):
    args = (
        "config", "save",
        "--name", "工作",
        "--app-token", "app_test_work",
        "--table-id", "tbl_test_work",
        "--lark-profile", "work-profile",
    )

    pending = cli(*args)

    assert pending.returncode == 3
    guidance = json.loads(pending.stdout)
    assert guidance["schema_version"] == "secret-book.config-identity-confirmation/v1"
    assert guidance["observed_identity"] == {
        "lark_profile": "work-profile",
        "app_id": "cli_test_work",
        "user": "Test User",
        "open_id": "ou_test_work",
    }
    assert not (cli.home / ".config" / "secret-book" / ".env").exists()

    saved = cli(*args, "--confirm-identity", guidance["confirmation_token"])
    listed = cli("config", "list")

    assert saved.returncode == 0, saved.stderr
    assert "已保存令牌配置：工作" in saved.stdout
    assert listed.returncode == 0, listed.stderr
    assert "工作" in listed.stdout
    assert "当前配置" in listed.stdout
    assert "app_test_work" not in listed.stdout
    assert "tbl_test_work" not in listed.stdout
    (cli.home / ".config" / "secret-book" / ".env").read_text(encoding="ascii")


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_config_name_rejects_unicode_line_separators_before_writing(cli, separator):
    result = cli(
        "config", "save",
        "--name", f"工作{separator}个人",
        "--app-token", "app_test_work",
        "--table-id", "tbl_test_work",
        "--lark-profile", "work-profile",
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )

    assert result.returncode == 1
    assert "不可打印字符" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (cli.home / ".config" / "secret-book" / ".env").exists()
    assert not cli.log_path.exists() or cli.log_path.read_text(encoding="utf-8") == ""


def test_second_config_does_not_replace_current_and_duplicate_name_is_rejected(cli):
    first, work_token = save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    assert first.returncode == 0, first.stderr

    second, _ = save_config(
        cli,
        name="个人",
        app_token="app_test_personal",
        table_id="tbl_test_personal",
        profile="personal-profile",
    )
    assert second.returncode == 0, second.stderr

    listed = cli("config", "list")
    assert listed.stdout.count("是") == 1
    assert any("是" in line and "工作" in line for line in listed.stdout.splitlines())
    assert any("个人" in line and "是" not in line for line in listed.stdout.splitlines())

    config_path = cli.home / ".config" / "secret-book" / ".env"
    before = config_path.read_bytes()
    duplicate = cli(
        "config", "save",
        "--name", "工作",
        "--app-token", "app_test_other",
        "--table-id", "tbl_test_other",
        "--lark-profile", "work-profile",
        "--confirm-identity", work_token,
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )

    assert duplicate.returncode == 1
    assert "令牌配置名称已存在：工作" in duplicate.stderr
    assert config_path.read_bytes() == before


def test_use_persistently_switches_current_config_without_calling_lark(cli):
    assert save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )[0].returncode == 0
    assert save_config(
        cli,
        name="个人",
        app_token="app_test_personal",
        table_id="tbl_test_personal",
        profile="personal-profile",
    )[0].returncode == 0

    switched = cli(
        "config", "use", "--name", "个人",
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )
    listed = cli(
        "config", "list",
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )

    assert switched.returncode == 0, switched.stderr
    assert "当前配置已切换为：个人" in switched.stdout
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.count("是") == 1
    assert any("是" in line and "个人" in line for line in listed.stdout.splitlines())


def test_rebind_updates_identity_without_changing_config_id_or_current_selection(cli):
    assert save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )[0].returncode == 0
    before = cli("config", "list").stdout
    config_id = next(line.split()[1] for line in before.splitlines() if "工作" in line)

    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    next(p for p in state["profiles"] if p["name"] == "work-profile")["appId"] = "cli_test_rebound"
    state["auth"]["work-profile"]["identities"]["user"]["openId"] = "ou_test_rebound"
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")

    blocked = cli("list", "--use-global-config")
    assert blocked.returncode == 3
    blocked_guidance = json.loads(blocked.stdout)
    assert any(action["kind"] == "rebind_named_config"
               for action in blocked_guidance["fix_actions"])

    args = (
        "config", "rebind",
        "--name", "工作",
        "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    confirmation = json.loads(pending.stdout)["confirmation_token"]
    rebound = cli(*args, "--confirm-identity", confirmation)
    after = cli("config", "list").stdout

    assert rebound.returncode == 0, rebound.stderr
    assert config_id in rebound.stdout
    assert any("是" in line and config_id in line and "工作" in line
               for line in after.splitlines())
    assert cli("list", "--use-global-config").returncode == 0


def test_rename_and_remove_preserve_current_config_invariant(cli):
    assert save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )[0].returncode == 0
    assert save_config(
        cli,
        name="个人",
        app_token="app_test_personal",
        table_id="tbl_test_personal",
        profile="personal-profile",
    )[0].returncode == 0
    before = cli("config", "list").stdout
    work_id = next(line.split()[1] for line in before.splitlines() if "工作" in line)

    renamed = cli(
        "config", "rename", "--name", "工作", "--new-name", "公司",
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )
    after = cli("config", "list", extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"}).stdout

    assert renamed.returncode == 0, renamed.stderr
    current_line = next(line for line in after.splitlines() if "公司" in line)
    assert "是" in current_line
    assert work_id in current_line

    refused = cli("config", "remove", "--name", "公司")
    assert refused.returncode == 1
    assert "请先用 config use 切换到另一套" in refused.stderr

    removed_other = cli("config", "remove", "--name", "个人")
    assert removed_other.returncode == 0, removed_other.stderr
    assert "已删除令牌配置：个人" in removed_other.stdout

    removed_last = cli("config", "remove", "--name", "公司")
    empty = cli("config", "list")
    business = cli("list", "--use-global-config", extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert removed_last.returncode == 0, removed_last.stderr
    assert empty.returncode == 0
    assert "还没有令牌配置" in empty.stdout
    assert business.returncode == 1
    assert "没有可用的完整令牌配置" in business.stderr
    assert "Traceback" not in business.stderr


def test_concurrent_saves_preserve_both_configs_and_private_file_mode(cli):
    specs = [
        ("工作", "app_test_work", "tbl_test_work", "work-profile"),
        ("个人", "app_test_personal", "tbl_test_personal", "personal-profile"),
    ]
    confirmed_args = []
    for name, app_token, table_id, profile in specs:
        args = (
            "config", "save", "--name", name, "--app-token", app_token,
            "--table-id", table_id, "--lark-profile", profile,
        )
        pending = cli(*args)
        token = json.loads(pending.stdout)["confirmation_token"]
        confirmed_args.append((*args, "--confirm-identity", token))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: cli(*args), confirmed_args))

    assert all(result.returncode == 0 for result in results)
    listed = cli("config", "list")
    assert "工作" in listed.stdout
    assert "个人" in listed.stdout
    assert listed.stdout.count("是") == 1
    config_path = cli.home / ".config" / "secret-book" / ".env"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    raw_json = next(
        line.split("=", 1)[1].strip("'") for line in config_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("SECRET_BOOK_CONFIGS_JSON=")
    )
    assert len(json.loads(raw_json)["configs"]) == 2


def test_invalid_named_config_schema_stops_before_lark_call(cli):
    config_path = cli.home / ".config" / "secret-book" / ".env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "SECRET_BOOK_CONFIGS_JSON='{\"schema_version\":999,\"active_id\":null,\"configs\":{}}'\n",
        encoding="utf-8",
    )

    result = cli("list", "--use-global-config", extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})

    assert result.returncode == 1
    assert "schema_version 不受支持" in result.stderr
    assert not cli.log_path.exists() or cli.log_path.read_text(encoding="utf-8") == ""


def test_config_save_rejects_empty_resource_ids_before_writing(cli):
    result = cli(
        "config", "save",
        "--name", "工作",
        "--app-token", "",
        "--table-id", "tbl_test_work",
        "--lark-profile", "work-profile",
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )

    assert result.returncode == 1
    assert "--app-token 不能为空" in result.stderr
    assert not (cli.home / ".config" / "secret-book" / ".env").exists()


def test_named_config_store_requires_explicit_active_id_even_when_empty(cli):
    config_path = cli.home / ".config" / "secret-book" / ".env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "SECRET_BOOK_CONFIGS_JSON='{\"schema_version\":1,\"configs\":{}}'\n",
        encoding="utf-8",
    )

    result = cli("config", "list", extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})

    assert result.returncode == 1
    assert ".active_id 必须存在" in result.stderr


def test_config_save_replace_failure_preserves_previous_file_without_traceback(cli):
    config_path = cli.home / ".config" / "secret-book" / ".env"
    config_path.parent.mkdir(parents=True)
    original = b"# keep me\nAUTO_UPDATE_CHECK=0\n"
    config_path.write_bytes(original)
    args = (
        "config", "save",
        "--name", "工作",
        "--app-token", "app_test_work",
        "--table-id", "tbl_test_work",
        "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]

    result = cli(
        *args,
        "--confirm-identity", token,
        extra_env={"SECRET_BOOK_TEST_ATOMIC_FAULT": "replace"},
    )

    assert result.returncode == 1
    assert "本地文件写入失败" in result.stderr
    assert "Traceback" not in result.stderr
    assert config_path.read_bytes() == original


def test_config_save_reports_unknown_durability_after_successful_replace(cli):
    args = (
        "config", "save",
        "--name", "工作",
        "--app-token", "app_test_work",
        "--table-id", "tbl_test_work",
        "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]

    result = cli(
        *args,
        "--confirm-identity", token,
        extra_env={"SECRET_BOOK_TEST_ATOMIC_FAULT": "directory-fsync"},
    )

    assert result.returncode == 1
    assert "本地写入结果不明" in result.stderr
    assert "Traceback" not in result.stderr
    config_path = cli.home / ".config" / "secret-book" / ".env"
    assert "SECRET_BOOK_CONFIGS_JSON=" in config_path.read_text(encoding="utf-8")
