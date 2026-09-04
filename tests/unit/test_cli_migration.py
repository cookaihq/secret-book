import json


def test_legacy_global_config_migrates_only_after_name_and_identity_confirmation(cli):
    config_path = cli.home / ".config" / "secret-book" / ".env"
    config_path.parent.mkdir(parents=True)
    original = (
        "# keep this comment\n"
        "AUTO_UPDATE_CHECK=0\n"
        "UNKNOWN_SETTING=keep-me\n"
        "SECRET_BOOK_APP_TOKEN=app_test_work\n"
        "SECRET_BOOK_TABLE_ID=tbl_test_work\n"
        "SECRET_BOOK_LARK_PROFILE=work-profile\n"
        "SECRET_BOOK_IDS=sec_legacy001\n"
    )
    config_path.write_text(original, encoding="utf-8")
    args = ("config", "migrate", "--name", "工作")

    pending = cli(*args)

    assert pending.returncode == 3
    guidance = json.loads(pending.stdout)
    assert guidance["schema_version"] == "secret-book.config-identity-confirmation/v1"
    assert config_path.read_text(encoding="utf-8") == original

    migrated = cli(*args, "--confirm-identity", guidance["confirmation_token"])
    after = config_path.read_text(encoding="utf-8")
    listed = cli("config", "list")

    assert migrated.returncode == 0, migrated.stderr
    assert "旧版令牌配置已迁移为：工作" in migrated.stdout
    assert "# keep this comment\n" in after
    assert "AUTO_UPDATE_CHECK=0\n" in after
    assert "UNKNOWN_SETTING=keep-me\n" in after
    assert "SECRET_BOOK_CONFIGS_JSON=" in after
    assert "SECRET_BOOK_APP_TOKEN=" not in after
    assert "SECRET_BOOK_TABLE_ID=" not in after
    assert "SECRET_BOOK_LARK_PROFILE=" not in after
    assert "SECRET_BOOK_IDS=" not in after
    assert "工作" in listed.stdout and "是" in listed.stdout

    before_repeat = config_path.read_bytes()
    repeated = cli(*args, extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert repeated.returncode == 1
    assert "已经使用命名令牌配置" in repeated.stderr
    assert config_path.read_bytes() == before_repeat


def test_migration_profile_flag_cannot_override_existing_legacy_profile(cli):
    config_path = cli.home / ".config" / "secret-book" / ".env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "SECRET_BOOK_APP_TOKEN=app_test_work\n"
        "SECRET_BOOK_TABLE_ID=tbl_test_work\n"
        "SECRET_BOOK_LARK_PROFILE=work-profile\n",
        encoding="utf-8",
    )

    result = cli(
        "config", "migrate",
        "--name", "工作",
        "--lark-profile", "personal-profile",
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )

    assert result.returncode == 1
    assert "只能在旧配置缺少该字段时补充，不能覆盖" in result.stderr
    assert "SECRET_BOOK_CONFIGS_JSON" not in config_path.read_text(encoding="utf-8")


def test_migrate_cleans_flat_keys_that_coexist_with_named_config(cli):
    args = (
        "config", "save",
        "--name", "工作",
        "--app-token", "app_test_work",
        "--table-id", "tbl_test_work",
        "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    assert cli(*args, "--confirm-identity", token).returncode == 0
    config_path = cli.home / ".config" / "secret-book" / ".env"
    original = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        original
        + "SECRET_BOOK_APP_TOKEN=stale_app\n"
        + "SECRET_BOOK_TABLE_ID=stale_table\n"
        + "SECRET_BOOK_IDS=sec_stale0001\n",
        encoding="utf-8",
    )

    cleaned = cli(
        "config", "migrate", "--name", "工作",
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )
    after = config_path.read_text(encoding="utf-8")

    assert cleaned.returncode == 0, cleaned.stderr
    assert "已清理" in cleaned.stdout
    assert "SECRET_BOOK_CONFIGS_JSON=" in after
    assert "SECRET_BOOK_APP_TOKEN=" not in after
    assert "SECRET_BOOK_TABLE_ID=" not in after
    assert "SECRET_BOOK_IDS=" not in after


def test_empty_named_store_with_legacy_values_migrates_without_manual_repair(cli):
    config_path = cli.home / ".config" / "secret-book" / ".env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "SECRET_BOOK_CONFIGS_JSON='{\"schema_version\":1,\"active_id\":null,\"configs\":{}}'\n"
        "SECRET_BOOK_APP_TOKEN=app_test_work\n"
        "SECRET_BOOK_TABLE_ID=tbl_test_work\n"
        "SECRET_BOOK_LARK_PROFILE=work-profile\n",
        encoding="utf-8",
    )
    args = ("config", "migrate", "--name", "工作")

    pending = cli(*args)
    assert pending.returncode == 3
    token = json.loads(pending.stdout)["confirmation_token"]
    migrated = cli(*args, "--confirm-identity", token)
    listed = cli("config", "list")

    assert migrated.returncode == 0, migrated.stderr
    assert "工作" in listed.stdout and "是" in listed.stdout
    after = config_path.read_text(encoding="utf-8")
    assert "SECRET_BOOK_CONFIGS_JSON=" in after
    assert "SECRET_BOOK_APP_TOKEN=" not in after
    assert "SECRET_BOOK_TABLE_ID=" not in after
