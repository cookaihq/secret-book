import json
import shlex
import subprocess

import pytest


VALID_FIELDS = [
    {"name": "id", "type": "text"},
    {"name": "name", "type": "text"},
    {"name": "service", "type": "text"},
    {"name": "account", "type": "text"},
    {"name": "purpose", "type": "text"},
    {"name": "secret", "type": "text"},
    {"name": "expires_at", "type": "datetime"},
    {"name": "notes", "type": "text"},
    {"name": "visible_to", "type": "user", "multiple": True},
]


def _handoff_argv(stdout):
    line = next(line for line in stdout.splitlines() if line.startswith("uv run --project "))
    return shlex.split(line)


def test_init_create_requires_identity_confirmation_before_base_write(cli):
    args = ("init-create", "--lark-profile", "work-profile")

    pending = cli(*args)

    assert pending.returncode == 3
    guidance = json.loads(pending.stdout)
    assert guidance["schema_version"] == "secret-book.config-identity-confirmation/v1"
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:1] == ["base"] for call in calls)

    cli.log_path.write_text("", encoding="utf-8")
    created = cli(*args, "--confirm-identity", guidance["confirmation_token"])

    assert created.returncode == 0, created.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    create_call = next(call for call in calls if call[:2] == ["base", "+base-create"])
    assert "work-profile" in create_call
    assert "令牌表" in create_call
    handoff = _handoff_argv(created.stdout)
    assert handoff == [
        "uv", "run", "--project", str(cli.repo), str(cli.script),
        "config", "save", "--name", "<名称>",
        "--app-token", "app_test_created",
        "--table-id", "tbl_test_created",
        "--lark-profile", "work-profile",
        "--confirm-identity", guidance["confirmation_token"],
    ]

    handoff[handoff.index("<名称>")] = "工作"
    saved = subprocess.run(
        handoff, cwd=cli.cwd, env=cli.build_env(), text=True, capture_output=True,
    )
    assert saved.returncode == 0, saved.stderr


@pytest.mark.parametrize("response", [
    {},
    {"data": {}},
    {"data": {"base": {}, "table": {"id": "tbl_test_created"}}},
    {
        "data": {
            "base": {"app_token": {}},
            "table": {"id": "tbl_test_created"},
        }
    },
    {
        "data": {
            "base": {"app_token": "app_test_created"},
            "table": {"id": " tbl_test_created"},
        }
    },
])
def test_init_create_rejects_malformed_success_payload_without_fake_handoff(cli, response):
    args = ("init-create", "--lark-profile", "work-profile")
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["base_create_response"] = response
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")

    result = cli(*args, "--confirm-identity", token)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "config save " not in result.stdout


def test_init_adopt_requires_identity_confirmation_before_reading_table(cli):
    args = (
        "init-adopt",
        "--url", "https://example.feishu.cn/base/test",
        "--lark-profile", "work-profile",
    )

    pending = cli(*args)

    assert pending.returncode == 3
    guidance = json.loads(pending.stdout)
    assert guidance["schema_version"] == "secret-book.config-identity-confirmation/v1"
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:1] == ["base"] for call in calls)

    cli.log_path.write_text("", encoding="utf-8")
    adopted = cli(*args, "--confirm-identity", guidance["confirmation_token"])

    assert adopted.returncode == 0, adopted.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    base_calls = [call for call in calls if call[:1] == ["base"]]
    assert [call[1] for call in base_calls] == ["+url-resolve", "+field-list"]
    assert all("work-profile" in call for call in base_calls)
    handoff = _handoff_argv(adopted.stdout)
    assert handoff == [
        "uv", "run", "--project", str(cli.repo), str(cli.script),
        "config", "save", "--name", "<名称>",
        "--app-token", "app_test_adopted",
        "--table-id", "tbl_test_adopted",
        "--lark-profile", "work-profile",
        "--confirm-identity", guidance["confirmation_token"],
    ]

    handoff[handoff.index("<名称>")] = "个人"
    saved = subprocess.run(
        handoff, cwd=cli.cwd, env=cli.build_env(), text=True, capture_output=True,
    )
    assert saved.returncode == 0, saved.stderr


@pytest.mark.parametrize("state_update", [
    {"resolved_url": []},
    {"resolved_url": {"base_token": {}, "block_id": "tbl_test_adopted"}},
    {"resolved_url": {"base_token": " app_test_adopted", "block_id": "tbl_test_adopted"}},
    {"field_list_response": {}},
    {"field_list_response": {"data": {}}},
    {"fields": "not-a-list"},
    {"fields": [None]},
    {"fields": [{}]},
    {"fields": [{"name": "id"}]},
    {"fields": [
        {"name": "id", "type": "text"},
        {"name": "id", "type": "text"},
    ]},
])
def test_init_adopt_rejects_malformed_lark_payload_without_traceback(cli, state_update):
    args = (
        "init-adopt",
        "--url", "https://example.feishu.cn/base/test",
        "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state.update(state_update)
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")

    result = cli(*args, "--confirm-identity", token)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("[secret-book] error:")
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:2] == ["base", "+field-create"] for call in calls)


def test_init_adopt_validates_all_existing_fields_before_creating_missing_fields(cli):
    args = (
        "init-adopt",
        "--url", "https://example.feishu.cn/base/test",
        "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["fields"] = [{"name": "name", "type": "number"}]
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")

    result = cli(*args, "--confirm-identity", token)

    assert result.returncode == 1
    assert "字段 name 类型不符" in result.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:2] == ["base", "+field-create"] for call in calls)


def test_init_adopt_rejects_single_select_visible_to_before_writing(cli):
    args = (
        "init-adopt",
        "--url", "https://example.feishu.cn/base/test",
        "--lark-profile", "work-profile",
    )
    pending = cli(*args)
    token = json.loads(pending.stdout)["confirmation_token"]
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["fields"] = [dict(field) for field in VALID_FIELDS]
    state["fields"][-1]["multiple"] = False
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    cli.log_path.write_text("", encoding="utf-8")

    result = cli(*args, "--confirm-identity", token)

    assert result.returncode == 1
    assert "visible_to" in result.stderr
    assert "多选" in result.stderr
    calls = [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert not any(call[:2] == ["base", "+field-create"] for call in calls)
