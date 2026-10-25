"""Opt-in integration against the actual packaged Setup native adapter.

Set SETUP_AGENT_MODEL_TEST_PLUGIN to a complete development/test Plugin. The
fake lark-cli supplies synthetic records; no real table or client home is read.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_cli_bindings import _save_config

PLUGIN = os.environ.get("SETUP_AGENT_MODEL_TEST_PLUGIN")
pytestmark = pytest.mark.skipif(not PLUGIN, reason="requires a complete Setup native adapter test package")


def setup_cli(cli, *args, expected=0):
    skill = Path(PLUGIN).resolve() / "skills/setup-agent-model"
    env = cli.build_env({"LOCALAPPDATA": str(cli.home / "AppData/Local")})
    # The test interpreter belongs to Secret Book; use Setup's locked project.
    result = subprocess.run(["uv", "run", "--frozen", "--project", str(skill), "python",
                             str(skill / "scripts/setup_agent_model.py"), *map(str, args)],
                            cwd=cli.cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == expected, result.stdout + result.stderr
    assert "native-synthetic-secret" not in result.stdout + result.stderr
    return json.loads(result.stdout)


def prepare(cli, client, host):
    _save_config(cli, name="工作", app_token="app_work", table_id="tbl_work", profile="work-profile")
    fake = json.loads(cli.state_path.read_bytes())
    fake["records"] = {"app_work": [{"_record_id": "rec_native", "id": "sec_native", "name": "Native test",
                                   "service": "AIhub", "account": "test", "purpose": "test",
                                   "secret": "ACTUAL_TABLE_KEY=native-synthetic-secret"}]}
    cli.state_path.write_text(json.dumps(fake), encoding="utf-8")
    hermes_config = cli.home / ("AppData/Local/hermes/config.yaml" if sys.platform == "win32" else ".hermes/config.yaml")
    desktop_config = cli.home / ("AppData/Local/Claude-3p/configLibrary/native.json" if sys.platform == "win32" else "Library/Application Support/Claude-3p/configLibrary/native.json")
    config = {"workbuddy": cli.home / ".workbuddy/models.json", "hermes": hermes_config,
              "codex": cli.home / ".codex/config.toml", "chatgpt": cli.home / ".codex/config.toml",
              "claude-code": cli.home / ".claude/settings.json",
              "claude-desktop": desktop_config}[client]
    config.parent.mkdir(parents=True, exist_ok=True)
    protocol = {"codex": "openai-responses", "chatgpt": "openai-responses", "claude-code": "anthropic-messages",
                "claude-desktop": "anthropic-messages"}.get(client, "openai-chat-completions")
    model = "claude-sonnet-5-5" if client == "claude-code" else "gpt-6.1-sol"
    request = {"client": client, "config": str(config), "selector": "$settings" if client == "claude-code" else "test",
               "new": True, "name": "Synthetic", "models": [model], "protocol": protocol, "key_mode": "replace"}
    if client in ("codex", "chatgpt"):
        request["default_model"] = model
        request["route_evidence"] = [{"model": model, "protocol": protocol, "source": "https://example.test/synthetic-route"}]
    if client == "claude-desktop":
        config.write_text('{"inferenceProvider":"gateway","inferenceModels":["existing"]}', encoding="utf-8")
        inspected = setup_cli(cli, "inspect", "--client", client)
        request.update(new=False, selector="$configuration", identity=inspected["files"][0]["entries"][0]["identity"],
                       models=["synthetic-claude"], default_model="synthetic-claude",
                       custom_models=[{"id": "synthetic-claude", "name": "Synthetic", "description": "Synthetic test route",
                                       "status": "active", "key_groups": ["Official"], "routes": [{"protocol": protocol,
                                       "capabilities": {"tool_calling": None, "image_input": None, "reasoning_efforts": None}}]}])
    request_path = cli.cwd / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    task = setup_cli(cli, "plan", "--request", request_path, "--source", "secret-book", "--host", host, "--offline")
    report = cli.cwd / "native-inspection.json"
    handoff = setup_cli(cli, "handoff", "--task", task["id"], "--host", host, "--output", report)
    flow = json.loads(cli("workflow", "start", "--role", "consumer", "--goal", "Native synthetic credential setup",
                          "--agent", host).stdout)["task"]["id"]
    command = ["configure", "--requirements", handoff["requirements"], "--inspection", str(report), "--agent", host,
               "--key", "AIHUB_API_KEY", "--id", "sec_native", "--map", "AIHUB_API_KEY=ACTUAL_TABLE_KEY",
               "--use-global-config", "--workflow", flow, "--workflow-agent", host]
    return config, task, report, flow, command


@pytest.mark.parametrize("client,host", [(name, name) for name in
                         ("hermes", "workbuddy", "chatgpt", "codex", "claude-desktop", "claude-code")])
def test_native_public_contract_one_confirmation_and_independent_setup_readback(cli, client, host):
    config, task, report, flow, command = prepare(cli, client, host)
    env = {"LOCALAPPDATA": str(cli.home / "AppData/Local")}
    before = config.read_bytes() if config.exists() else None
    preview = cli(*command, extra_env=env)
    assert preview.returncode == 3, preview.stdout + preview.stderr
    pending = json.loads(preview.stdout)
    assert pending["status"] == "confirmation_required", pending
    assert pending["review"]["native"]["client"] == client
    assert pending["review"]["keys"][0]["source_key"] == "ACTUAL_TABLE_KEY"
    assert (config.read_bytes() if config.exists() else None) == before
    # Public recovery keeps the desktop identities independent from their CLIs.
    resumed = cli("workflow", "status", "--id", flow, "--agent", host,
                  extra_env={**env, "FAKE_LARK_FAIL_ON_CALL": "1"})
    token = json.loads(resumed.stdout)["task"]["pending"]["confirmation_token"]
    assert token == pending["confirmation_token"]
    saved = cli(*command, "--confirm", token, extra_env=env)
    assert saved.returncode == 0, saved.stdout + saved.stderr
    receipt = json.loads(saved.stdout)["native_receipt"]
    assert b"native-synthetic-secret" in config.read_bytes()
    cli.assert_private(config)
    receipt_path = cli.cwd / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    checked = setup_cli(cli, "accept-secret-book", "--task", task["id"], "--host", host,
                        "--receipt", receipt_path, "--reference", flow)
    assert checked["phase"] == "saved", checked
    for text in (preview.stdout, resumed.stdout, saved.stdout, json.dumps(checked)):
        assert "native-synthetic-secret" not in text
    calls = [json.loads(row) for row in cli.log_path.read_text(encoding="utf-8").splitlines()]
    assert all(call[1] in ("+record-list", "+field-list", "+url-resolve") for call in calls if call[0] == "base")
    # Test code may inspect its own synthetic store to enforce the no-secret invariant.
    assert "native-synthetic-secret" not in (cli.home / ".config/secret-book/consumer-configurations.json").read_text(encoding="utf-8")


def test_native_repair_refuses_changed_file_before_accessing_table(cli):
    config, task, report, flow, command = prepare(cli, "workbuddy", "chatgpt")
    config.write_text('[{"name":"changed by user"}]', encoding="utf-8")
    result = cli(*command, extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "native_adapter_rejected"
    assert json.loads(config.read_bytes()) == [{"name": "changed by user"}]


def test_native_scope_cannot_redirect_key_to_dotenv(cli):
    _, _, _, _, command = prepare(cli, "workbuddy", "claude-desktop")
    result = cli(*command, "--scope", "project", extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode != 0
    assert not (cli.cwd / ".env.local").exists()
