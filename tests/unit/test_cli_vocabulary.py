import json


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


def test_cli_uses_token_vocabulary_and_does_not_offer_flat_config_writer(cli):
    for args in [(), ("save", "--help"), ("list", "--help"), ("init-create", "--help")]:
        result = cli(*args, "--help") if not args else cli(*args)
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "台账" not in output
        assert "令牌" in output
    top_help = cli("--help").stdout
    assert "config-write" not in top_help

    _save_work_config(cli)
    empty = cli("list", "--use-global-config")
    assert empty.returncode == 0, empty.stderr
    assert "令牌表为空" in empty.stdout
    assert "台账为空" not in empty.stdout


def test_lookup_and_binding_flag_conflicts_fail_explicitly(cli):
    get_many = cli("get", "--id", "sec_first0001", "--id", "sec_second002")
    copy_mixed = cli("copy", "--name", "one", "--id", "sec_first0001")
    auto_bind = cli("run", "--auto", "--bind", "--", "true")

    assert get_many.returncode == 1
    assert "一次只能指定一个 --id" in get_many.stderr
    assert copy_mixed.returncode == 1
    assert "--name 与 --id 互斥" in copy_mixed.stderr
    assert auto_bind.returncode == 1
    assert "--auto 与 --bind 互斥" in auto_bind.stderr
