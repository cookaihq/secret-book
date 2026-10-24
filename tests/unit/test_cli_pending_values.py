"""Incomplete records remain editable without making empty credentials usable."""

import json
import os
import sys

import pytest

from test_cli_bindings import _save_config
from test_cli_consumers import args as configure_args
from test_cli_consumers import consumer, inspect, update_record
from test_cli_readonly import prepare_record


def calls(cli):
    return [json.loads(line) for line in cli.log_path.read_text(encoding="utf-8").splitlines()]


def empty_table(cli):
    _save_config(cli, name="work", app_token="app_test_work", table_id="tbl_test_work",
                 profile="work-profile")
    cli.log_path.write_text("", encoding="utf-8")


def save(cli, payload):
    return cli("save", "--name", "aihub-main", "--service", "aihub",
               "--purpose", "confirmed media generation", "--use-global-config",
               input_text=payload)


def replace_secret(cli, payload):
    prepare_record(cli, record_id="sec_existing001")
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"]["app_test_work"][0]["secret"] = payload
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")


@pytest.mark.parametrize("payload", [
    "AIHUB_API_KEY=\n",
    "AIHUB_API_KEY=   \n",
    "AIHUB_API_KEY=\nOTHER_TOKEN=synthetic-preserved-value\n",
])
def test_save_creates_one_record_with_pending_values_and_generated_id(cli, payload):
    empty_table(cli)

    result = save(cli, payload)

    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads(result.stdout)
    assert saved["status"] == "record_created"
    assert saved["missing_keys"] == ["AIHUB_API_KEY"]
    assert "AIHUB_API_KEY" in saved["keys"]
    records = json.loads(cli.state_path.read_text(encoding="utf-8"))["records"]["app_test_work"]
    assert len(records) == 1
    record = records[0]
    assert record["name"] == "aihub-main"
    assert record["service"] == "aihub"
    assert record["purpose"] == "confirmed media generation"
    assert record["id"]
    assert saved["id"] == record["id"]
    assert saved["record_id"] == record["_record_id"]
    assert record["secret"] == payload.rstrip("\r\n")
    assert saved["record_url"] == (
        "https://example.feishu.cn/base/app_test_work?table=tbl_test_work&record=rec_created")
    output = result.stdout + result.stderr
    assert "AIHUB_API_KEY" in output
    assert "待补" in output
    assert record["id"] in output
    assert "app_test_work" in output and "tbl_test_work" in output
    assert "synthetic-preserved-value" not in output
    assert sum(call[:2] == ["base", "+record-batch-create"] for call in calls(cli)) == 1
    assert not any(call[:2] == ["base", "+record-batch-update"] for call in calls(cli))

    # Metadata access must not ask a maintainer to create or number another row.
    cli.log_path.write_text("", encoding="utf-8")
    listed = cli("list", "--use-global-config")
    retrieved = cli("get", "--name", "aihub-main", "--use-global-config")
    assert listed.returncode == 0, listed.stderr
    assert retrieved.returncode == 0, retrieved.stderr
    assert "aihub-main" in listed.stdout
    assert "AIHUB_API_KEY" in retrieved.stdout
    assert any("missing_keys" in line and "AIHUB_API_KEY" in line
               for line in retrieved.stdout.splitlines())
    assert "synthetic-preserved-value" not in listed.stdout + retrieved.stdout + retrieved.stderr
    assert all(call[1] == "+record-list" for call in calls(cli) if call[0] == "base")


@pytest.mark.parametrize("link_failure", ["missing", "denied"])
def test_record_link_failure_preserves_created_row_and_does_not_retry_creation(cli, link_failure):
    empty_table(cli)
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    if link_failure == "missing":
        state["record_share_link_response"] = {"data": {"record_share_links": {}}}
    else:
        state["shortcut_errors"] = {
            "+record-share-link-create": {"type": "permission", "message": "access denied"}}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")

    result = save(cli, "AIHUB_API_KEY=")

    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads(result.stdout)
    assert saved["status"] == "record_created"
    assert saved["missing_keys"] == ["AIHUB_API_KEY"]
    assert saved["record_id"] == "rec_created"
    assert saved["record_url"] is None
    assert saved["link_status"]
    assert saved["app_token"] == "app_test_work" and saved["table_id"] == "tbl_test_work"
    assert len(json.loads(cli.state_path.read_text(encoding="utf-8"))["records"]["app_test_work"]) == 1
    assert sum(call[:2] == ["base", "+record-batch-create"] for call in calls(cli)) == 1

    repeated = save(cli, "AIHUB_API_KEY=")
    assert repeated.returncode != 0
    assert "已存在" in repeated.stderr
    assert sum(call[:2] == ["base", "+record-batch-create"] for call in calls(cli)) == 1


def test_duplicate_pending_name_does_not_create_another_record(cli):
    empty_table(cli)
    assert save(cli, "AIHUB_API_KEY=").returncode == 0

    repeated = save(cli, "AIHUB_API_KEY=")

    assert repeated.returncode != 0
    assert "已存在" in repeated.stderr
    assert len(json.loads(cli.state_path.read_text(encoding="utf-8"))["records"]["app_test_work"]) == 1


def test_save_applies_confirmed_visibility_in_the_initial_create(cli):
    empty_table(cli)

    result = cli("save", "--name", "aihub-main", "--service", "aihub", "--purpose", "confirmed use",
                 "--visible-to", "ou_test_work", "--visible-to", "ou_other_user", "--use-global-config",
                 input_text="AIHUB_API_KEY=synthetic-restricted-value\nOTHER_TOKEN=")

    assert result.returncode == 0, result.stdout + result.stderr
    records = json.loads(cli.state_path.read_text(encoding="utf-8"))["records"]["app_test_work"]
    assert len(records) == 1
    assert records[0]["visible_to"] == [{"id": "ou_test_work"}, {"id": "ou_other_user"}]
    writes = [call for call in calls(cli) if call[:2] == ["base", "+record-batch-create"]]
    assert len(writes) == 1
    submitted = json.loads(writes[0][writes[0].index("--json") + 1])["create_records"]
    assert submitted[0]["visible_to"] == records[0]["visible_to"]
    assert not any(call[:2] == ["base", "+record-batch-update"] for call in calls(cli))
    assert "synthetic-restricted-value" not in result.stdout + result.stderr


@pytest.mark.parametrize("payload", ["AIHUB_API_KEY=", "AIHUB_API_KEY=\nOTHER_TOKEN=synthetic-preserved-value"])
def test_run_requests_missing_values_without_executing_child_or_saving_binding(cli, payload):
    replace_secret(cli, payload)
    marker = cli.cwd / "child-executed"

    result = cli("run", "--name", "manual-entry", "--bind", "--use-global-config", "--",
                 sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker))

    assert result.returncode == 3, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "AIHUB_API_KEY" in output
    assert "待补" in output or "填写" in output
    assert "synthetic-preserved-value" not in output
    assert not marker.exists()
    assert not (cli.home / ".config/secret-book/bindings.json").exists()
    assert all(call[1] == "+record-list" for call in calls(cli) if call[0] == "base")


@pytest.fixture
def clipboard_boundary(cli):
    """Capture the external clipboard command without touching the user's clipboard."""
    directory = cli.cwd / "clipboard-probe"
    directory.mkdir()
    marker = directory / "clipboard.bin"
    existing_sitecustomize = cli.repo / "tests/support/sitecustomize.py"
    (directory / "sitecustomize.py").write_text(
        "import runpy, subprocess\n"
        "from pathlib import Path\n"
        f"runpy.run_path({str(existing_sitecustomize)!r})\n"
        "_original_run = subprocess.run\n"
        "def run(args, *positional, **kwargs):\n"
        "    if isinstance(args, (list, tuple)) and args and args[0] in ('clip.exe', 'pbcopy', 'wl-copy', 'xclip'):\n"
        f"        Path({str(marker)!r}).write_bytes(kwargs['input'])\n"
        "        return subprocess.CompletedProcess(args, 0, stdout=b'', stderr=b'')\n"
        "    return _original_run(args, *positional, **kwargs)\n"
        "subprocess.run = run\n",
        encoding="utf-8",
    )
    return marker, {"PYTHONPATH": str(directory)}


def test_copy_missing_value_does_not_write_clipboard(cli, clipboard_boundary):
    replace_secret(cli, "AIHUB_API_KEY=\nOTHER_TOKEN=synthetic-preserved-value")
    marker, env = clipboard_boundary

    result = cli("copy", "--name", "manual-entry", "--key", "AIHUB_API_KEY",
                 "--use-global-config", extra_env=env)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "AIHUB_API_KEY" in result.stdout
    assert "synthetic-preserved-value" not in result.stdout + result.stderr
    assert not marker.exists()


def test_copy_completed_key_is_usable_with_other_values_pending(cli, clipboard_boundary):
    replace_secret(cli, "AIHUB_API_KEY=\nOTHER_TOKEN=synthetic-preserved-value")
    marker, env = clipboard_boundary

    result = cli("copy", "--name", "manual-entry", "--key", "OTHER_TOKEN",
                 "--use-global-config", extra_env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_bytes().decode("utf-16" if os.name == "nt" else "utf-8") == "synthetic-preserved-value"
    assert "synthetic-preserved-value" not in result.stdout + result.stderr


@pytest.mark.parametrize("source_key", ["EXAMPLE_KEY", "AIHUB_API_KEY"])
def test_configure_missing_required_value_guides_completion_without_writing(cli, consumer, source_key):
    update_record(cli, secret=f"{source_key}=\nUNRELATED=synthetic-preserved-value")
    report = inspect(cli)
    extra = ("--map", "EXAMPLE_KEY=AIHUB_API_KEY") if source_key == "AIHUB_API_KEY" else ()

    result = cli(*configure_args(consumer, report, *extra))

    assert result.returncode == 3, result.stdout + result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "missing_keys"
    assert output["missing_keys"] == ["EXAMPLE_KEY"]
    assert any(row["source_key"] == source_key and not row["present"] for row in output["keys"])
    assert not (cli.home / ".config/example/.env").exists()
    assert not (cli.cwd / ".env.local").exists()
    assert "synthetic-preserved-value" not in result.stdout + result.stderr


def test_configure_uses_completed_required_value_when_unrelated_value_is_pending(cli, consumer):
    update_record(cli, secret="EXAMPLE_KEY=synthetic-complete-value\nUNRELATED=")
    report = inspect(cli)
    command = configure_args(consumer, report)

    preview = cli(*command)
    assert preview.returncode == 3, preview.stdout + preview.stderr
    review = json.loads(preview.stdout)
    assert review["status"] == "confirmation_required"
    assert not (cli.home / ".config/example/.env").exists()
    saved = cli(*command, "--confirm", review["confirmation_token"])

    assert saved.returncode == 0, saved.stdout + saved.stderr
    text = (cli.home / ".config/example/.env").read_text(encoding="utf-8")
    assert "synthetic-complete-value" in text
    assert "UNRELATED" not in text
    assert "synthetic-complete-value" not in preview.stdout + saved.stdout + saved.stderr


def test_filled_record_save_and_run_remain_usable(cli):
    empty_table(cli)
    saved = save(cli, "AIHUB_API_KEY=synthetic-complete-value")
    assert saved.returncode == 0, saved.stdout + saved.stderr
    marker = cli.cwd / "child-result"

    ran = cli("run", "--name", "aihub-main", "--use-global-config", "--", sys.executable, "-c",
              "import os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(os.environ['AIHUB_API_KEY'])", str(marker))

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert marker.read_text(encoding="utf-8") == "synthetic-complete-value"
    assert "synthetic-complete-value" not in saved.stdout + saved.stderr + ran.stdout + ran.stderr


@pytest.mark.parametrize("value", ["record_created", "status"])
def test_save_value_matching_json_protocol_does_not_change_result_structure(cli, value):
    empty_table(cli)

    result = save(cli, f"AIHUB_API_KEY={value}\nOTHER_TOKEN=")

    assert result.returncode == 0, result.stdout + result.stderr
    saved = json.loads(result.stdout)
    assert saved["status"] == "record_created"
    assert saved["keys"] == ["AIHUB_API_KEY", "OTHER_TOKEN"]
    assert saved["missing_keys"] == ["OTHER_TOKEN"]
    record = json.loads(cli.state_path.read_text(encoding="utf-8"))["records"]["app_test_work"][0]
    assert saved["id"] == record["id"]
    assert saved["record_id"] == record["_record_id"] == "rec_created"
    assert saved["record_url"] == (
        "https://example.feishu.cn/base/app_test_work?table=tbl_test_work&record=rec_created")
    assert record["secret"] == f"AIHUB_API_KEY={value}\nOTHER_TOKEN="


def test_user_fills_value_in_existing_record_then_runs_without_recreating(cli):
    empty_table(cli)
    saved = save(cli, "AIHUB_API_KEY=")
    assert saved.returncode == 0, saved.stdout + saved.stderr
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    record = state["records"]["app_test_work"][0]
    original_id = record["id"]
    # This external edit represents the user filling Value in the existing row.
    record["secret"] = "AIHUB_API_KEY=synthetic-entered-later"
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    marker = cli.cwd / "completed-later"

    ran = cli("run", "--id", original_id, "--use-global-config", "--", sys.executable, "-c",
              "import os,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(os.environ['AIHUB_API_KEY'])", str(marker))

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert marker.read_text(encoding="utf-8") == "synthetic-entered-later"
    records = json.loads(cli.state_path.read_text(encoding="utf-8"))["records"]["app_test_work"]
    assert len(records) == 1 and records[0]["id"] == original_id
    assert sum(call[:2] == ["base", "+record-batch-create"] for call in calls(cli)) == 1
    assert "synthetic-entered-later" not in ran.stdout + ran.stderr
