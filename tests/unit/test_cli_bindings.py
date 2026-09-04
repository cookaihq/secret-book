import json
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest


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


def test_automatic_binding_is_isolated_by_token_table_identity(cli, tmp_path):
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
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {
        "app_test_work": [{
            "_record_id": "rec_work",
            "id": "sec_same000001",
            "name": "same-id-work",
            "service": "example",
            "account": "work",
            "purpose": "work token",
            "secret": "TOKEN=work-secret-value",
            "expires_at": None,
            "visible_to": None,
        }],
        "app_test_personal": [{
            "_record_id": "rec_personal",
            "id": "sec_same000001",
            "name": "same-id-personal",
            "service": "example",
            "account": "personal",
            "purpose": "personal token",
            "secret": "TOKEN=personal-secret-value",
            "expires_at": None,
            "visible_to": None,
        }],
    }
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    output = tmp_path / "used-token.txt"
    command = (
        sys.executable,
        "-c",
        "import os, pathlib, sys; pathlib.Path(sys.argv[1]).write_text(os.environ['TOKEN'])",
        str(output),
    )

    bound = cli(
        "run", "--id", "sec_same000001", "--bind", "--use-global-config",
        "--", *command,
    )
    assert bound.returncode == 0, bound.stderr
    assert output.read_text(encoding="utf-8") == "work-secret-value"

    assert cli("config", "use", "--name", "个人").returncode == 0
    output.unlink()
    cli.log_path.write_text("", encoding="utf-8")
    wrong_config = cli("run", "--auto", "--use-global-config", "--", *command)

    assert wrong_config.returncode == 3
    assert not output.exists()
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:2] == ["base", "+record-list"] for call in calls)

    assert cli("config", "use", "--name", "工作").returncode == 0
    reused = cli("run", "--auto", "--use-global-config", "--", *command)
    assert reused.returncode == 0, reused.stderr
    assert output.read_text(encoding="utf-8") == "work-secret-value"

    bindings = (cli.home / ".config" / "secret-book" / "bindings.json").read_text(encoding="utf-8")
    assert '"version": 2' in bindings
    assert "app_test_work" not in bindings
    assert "tbl_test_work" not in bindings


def test_legacy_binding_is_reported_but_never_guessed_or_queried(cli, tmp_path):
    _save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    bindings_path = cli.home / ".config" / "secret-book" / "bindings.json"
    bindings_path.write_text(json.dumps({
        "version": 1,
        "bindings": [{
            "scope": str(cli.cwd.resolve()),
            "command": "python",
            "ids": ["sec_legacy0001"],
            "hits": 4,
        }],
    }), encoding="utf-8")
    output = tmp_path / "must-not-exist.txt"
    command = (
        sys.executable,
        "-c",
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('ran')",
        str(output),
    )
    cli.log_path.write_text("", encoding="utf-8")

    result = cli("run", "--auto", "--use-global-config", "--", *command)
    listed = cli("bindings")

    assert result.returncode == 3
    assert "旧版自动绑定" in result.stdout
    assert "无法判断属于哪套令牌配置" in result.stdout
    assert not output.exists()
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:2] == ["base", "+record-list"] for call in calls)
    assert "旧版-需重新绑定" in listed.stdout
    assert json.loads(bindings_path.read_text(encoding="utf-8"))["version"] == 1

    removed = cli(
        "unbind", "--command", "python", "--legacy",
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )
    assert removed.returncode == 0, removed.stderr
    assert json.loads(bindings_path.read_text(encoding="utf-8"))["bindings"] == []


def test_legacy_explicit_ids_are_rejected_after_switch_without_querying_new_table(cli, tmp_path):
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
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {
        "app_test_work": [{
            "_record_id": "rec_work",
            "id": "sec_same000001",
            "name": "work",
            "service": "example",
            "account": "work",
            "purpose": "work token",
            "secret": "TOKEN=work-secret-value",
            "expires_at": None,
            "visible_to": None,
        }],
        "app_test_personal": [{
            "_record_id": "rec_personal",
            "id": "sec_same000001",
            "name": "personal",
            "service": "example",
            "account": "personal",
            "purpose": "personal token",
            "secret": "TOKEN=personal-secret-value",
            "expires_at": None,
            "visible_to": None,
        }],
    }
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    (cli.cwd / ".env.local").write_text(
        "SECRET_BOOK_IDS=sec_same000001\n",
        encoding="utf-8",
    )
    assert cli("config", "use", "--name", "个人").returncode == 0
    output = tmp_path / "must-not-exist.txt"
    command = (
        sys.executable,
        "-c",
        "import os, pathlib, sys; pathlib.Path(sys.argv[1]).write_text(os.environ['TOKEN'])",
        str(output),
    )
    cli.log_path.write_text("", encoding="utf-8")

    result = cli("run", "--auto", "--use-global-config", "--", *command)

    assert result.returncode == 3
    assert "裸记录 ID 无法证明属于当前令牌表" in result.stdout
    assert not output.exists()
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:2] == ["base", "+record-list"] for call in calls)


def test_concurrent_binding_updates_preserve_both_entries(cli, tmp_path):
    _save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    records = []
    commands = []
    for index in (1, 2):
        records.append({
            "_record_id": f"rec_{index}",
            "id": f"sec_parallel{index:02d}",
            "name": f"parallel-{index}",
            "service": "example",
            "account": "work",
            "purpose": f"parallel token {index}",
            "secret": f"TOKEN_{index}=value-{index}",
            "expires_at": None,
            "visible_to": None,
        })
        command = tmp_path / f"command-{index}"
        command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        command.chmod(0o755)
        commands.append(command)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test_work": records}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")

    def bind(index):
        return cli(
            "run", "--id", records[index]["id"], "--bind", "--use-global-config",
            "--", str(commands[index]),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(bind, (0, 1)))

    assert all(result.returncode == 0 for result in results), [result.stderr for result in results]
    data = json.loads(
        (cli.home / ".config" / "secret-book" / "bindings.json").read_text(encoding="utf-8")
    )
    assert {entry["command"] for entry in data["bindings"]} == {"command-1", "command-2"}


def test_binding_persistence_failure_does_not_override_successful_command_exit(cli, tmp_path):
    _save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test_work": [{
        "_record_id": "rec_work",
        "id": "sec_bindfail01",
        "name": "bind-failure",
        "service": "example",
        "account": "work",
        "purpose": "binding write failure",
        "secret": "TOKEN=value",
        "expires_at": None,
        "visible_to": None,
    }]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    marker = tmp_path / "command-succeeded.txt"
    bindings_path = cli.home / ".config" / "secret-book" / "bindings.json"
    command = (
        sys.executable,
        "-c",
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('done'); "
        "pathlib.Path(sys.argv[2]).mkdir(parents=True)",
        str(marker),
        str(bindings_path),
    )

    result = cli(
        "run", "--id", "sec_bindfail01", "--bind", "--use-global-config",
        "--", *command,
    )

    assert result.returncode == 0
    assert marker.read_text(encoding="utf-8") == "done"
    assert "被包装命令已经成功，但自动绑定保存失败" in result.stderr
    assert "本次仍返回命令的退出码 0" in result.stderr


def test_binding_reports_unknown_durability_without_overriding_command_success(cli, tmp_path):
    _save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test_work": [{
        "_record_id": "rec_work",
        "id": "sec_bindunknown",
        "name": "bind-unknown",
        "service": "example",
        "account": "work",
        "purpose": "binding durability unknown",
        "secret": "TOKEN=value",
        "expires_at": None,
        "visible_to": None,
    }]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    command = tmp_path / "successful-command"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)

    result = cli(
        "run", "--id", "sec_bindunknown", "--bind", "--use-global-config",
        "--", str(command),
        extra_env={"SECRET_BOOK_TEST_ATOMIC_FAULT": "directory-fsync"},
    )

    assert result.returncode == 0
    assert "自动绑定持久化结果不明" in result.stderr
    assert "未建立绑定" not in result.stderr
    assert "Traceback" not in result.stderr
    bindings_path = cli.home / ".config" / "secret-book" / "bindings.json"
    assert json.loads(bindings_path.read_text(encoding="utf-8"))["bindings"]


def test_rebind_leaves_old_namespace_binding_for_explicit_cleanup(cli, tmp_path):
    _save_config(
        cli,
        name="工作",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test_work": [{
        "_record_id": "rec_work",
        "id": "sec_rebind001",
        "name": "rebind",
        "service": "example",
        "account": "work",
        "purpose": "rebind cleanup",
        "secret": "TOKEN=value",
        "expires_at": None,
        "visible_to": None,
    }]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    command = tmp_path / "bound-command"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)
    bound = cli(
        "run", "--id", "sec_rebind001", "--bind", "--use-global-config",
        "--", str(command),
    )
    assert bound.returncode == 0, bound.stderr

    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    next(p for p in state["profiles"] if p["name"] == "work-profile")["appId"] = "cli_rebound"
    state["auth"]["work-profile"]["identities"]["user"]["openId"] = "ou_rebound"
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    args = (
        "config", "rebind", "--name", "工作", "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    rebound = cli(*args, "--confirm-identity", token)

    assert rebound.returncode == 0, rebound.stderr
    data = json.loads(
        (cli.home / ".config" / "secret-book" / "bindings.json").read_text(encoding="utf-8")
    )
    assert len(data["bindings"]) == 1
    prefix = data["bindings"][0]["resource_namespace"][:12]

    removed = cli(
        "unbind", "--command", "bound-command", "--namespace", prefix,
        extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"},
    )
    assert removed.returncode == 0, removed.stderr
    data = json.loads(
        (cli.home / ".config" / "secret-book" / "bindings.json").read_text(encoding="utf-8")
    )
    assert data["bindings"] == []


def test_shared_namespace_binding_survives_other_config_remove_and_rebind(cli, tmp_path):
    for name in ("工作", "工作副本"):
        _save_config(
            cli,
            name=name,
            app_token="app_test_work",
            table_id="tbl_test_work",
            profile="work-profile",
        )
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test_work": [{
        "_record_id": "rec_work",
        "id": "sec_shared0001",
        "name": "shared",
        "service": "example",
        "account": "work",
        "purpose": "shared namespace",
        "secret": "TOKEN=value",
        "expires_at": None,
        "visible_to": None,
    }]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    command = tmp_path / "shared-command"
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)
    assert cli(
        "run", "--id", "sec_shared0001", "--bind", "--use-global-config",
        "--", str(command),
    ).returncode == 0
    bindings_path = cli.home / ".config" / "secret-book" / "bindings.json"

    removed = cli("config", "remove", "--name", "工作副本")
    assert removed.returncode == 0, removed.stderr
    assert len(json.loads(bindings_path.read_text(encoding="utf-8"))["bindings"]) == 1

    _save_config(
        cli,
        name="工作副本",
        app_token="app_test_work",
        table_id="tbl_test_work",
        profile="work-profile",
    )
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    next(p for p in state["profiles"] if p["name"] == "work-profile")["appId"] = "cli_rebound"
    state["auth"]["work-profile"]["identities"]["user"]["openId"] = "ou_rebound"
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    args = (
        "config", "rebind", "--name", "工作副本", "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    rebound = cli(*args, "--confirm-identity", token)

    assert rebound.returncode == 0, rebound.stderr
    assert len(json.loads(bindings_path.read_text(encoding="utf-8"))["bindings"]) == 1


@pytest.mark.parametrize("payload", [
    {"version": True, "bindings": []},
    {"version": 1.0, "bindings": []},
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": ["sec_example001"],
            "resource_namespace": 123,
        }],
    },
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": ["sec_example001"],
            "resource_namespace": "abcd",
        }],
    },
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": [],
        }],
    },
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": ["sec_example001"],
            "hits": "1",
        }],
    },
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": ["sec_example001"],
            "hits": True,
        }],
    },
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": ["sec_example001"],
            "hits": 1.5,
        }],
    },
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": ["sec_example001"],
            "hits": -1,
        }],
    },
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": ["sec_example001"],
            "created": 1,
        }],
    },
    {
        "version": 2,
        "bindings": [{
            "scope": "/tmp/project",
            "command": "example",
            "ids": ["sec_example001"],
            "last_used": False,
        }],
    },
])
def test_bindings_rejects_malformed_schema_without_traceback(cli, payload):
    bindings_path = cli.home / ".config" / "secret-book" / "bindings.json"
    bindings_path.parent.mkdir(parents=True)
    bindings_path.write_text(json.dumps(payload), encoding="utf-8")

    result = cli("bindings", extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "无效" in result.stderr or "schema version" in result.stderr
