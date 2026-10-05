"""Exercise credential persistence through separate CLI processes with synthetic secrets."""
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_cli_bindings import _save_config


@pytest.fixture
def consumer(cli):
    _save_config(cli, name="工作", app_token="app_work", table_id="tbl_work", profile="work-profile")
    requirements = cli.cwd / "requirements.json"
    requirements.write_text(json.dumps({
        "schema_version": 1,
        "consumer": {"kind": "plugin", "name": "example", "skills": ["example-a", "example-b"]},
        "keys": {
            "EXAMPLE_KEY": {"required": True, "sensitive": True, "description": "服务密钥"},
            "EXAMPLE_URL": {"required": False, "sensitive": False, "description": "服务地址", "default": "https://example.invalid"}},
        "groups": [["EXAMPLE_KEY", "EXAMPLE_URL"]]}))
    update_record(cli)
    return requirements


def update_record(cli, **fields):
    state = json.loads(cli.state_path.read_text())
    record = {"_record_id": "rec_work", "id": "sec_example", "name": "测试服务", "service": "example",
              "account": "work", "purpose": "test", "secret": "EXAMPLE_KEY=synthetic-secret-one\nUNRELATED=do-not-copy",
              "expires_at": None, "visible_to": None}
    record.update(fields)
    state["records"] = {"app_work": [record]}
    cli.state_path.write_text(json.dumps(state))


def inspect(cli, *, skill="example-a", env=None, global_enabled=True):
    """A fixture caller with ADR 0003 dotenv precedence; actual AIhub is tested separately."""
    env = env or {}
    root = cli.home / ".config/example"
    paths = [cli.cwd / f".env.{skill}", cli.cwd / ".env.local", cli.cwd / ".env"]
    if global_enabled:
        paths += [root / skill / ".env.local", root / skill / ".env", root / f".env.{skill}",
                  root / ".env.local", root / ".env", cli.home / ".config" / skill / ".env"]
    report = {"schema": "secret-book.config-inspection/v1", "status": "ok",
              "consumer": {"kind": "plugin", "name": "example"}, "cwd": str(cli.cwd.resolve()),
              "skill": skill, "global_enabled": global_enabled, "layers": [], "fields": {}, "environment": {}}
    layers = [("environment", env)]
    for path in paths:
        data = path.read_bytes() if path.exists() else None
        report["layers"].append({"path": str(path), "revision": hashlib.sha256(data).hexdigest() if data is not None else None})
        values = {}
        for line in (data.decode() if data else "").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
        layers.append((str(path), values))
    for key in ["EXAMPLE_KEY", "EXAMPLE_URL"]:
        value = env.get(key, "")
        report["environment"][key] = hashlib.sha256(value.encode()).hexdigest() if value.strip() else None
        source = next((name for name, values in layers if values.get(key, "").strip()), None)
        report["fields"][key] = {"source": source or ("built-in default" if key == "EXAMPLE_URL" else "missing"),
                                  "present": bool(source) or key == "EXAMPLE_URL"}
    target = cli.cwd / "inspection.json"
    target.write_text(json.dumps(report))
    return target


def args(consumer, inspection, *extra):
    return ("configure", "--requirements", str(consumer), "--inspection", str(inspection),
            "--key", "EXAMPLE_KEY", "--id", "sec_example", "--use-global-config", "--agent", "codex", *extra)


def configure(cli, consumer, report, *extra):
    command = args(consumer, report, *extra)
    preview = cli(*command)
    assert preview.returncode == 3, preview.stderr
    assert json.loads(preview.stdout)["status"] == "confirmation_required", preview.stdout
    token = json.loads(preview.stdout)["confirmation_token"]
    confirmed = cli(*command, "--confirm", token)
    assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
    return json.loads(preview.stdout), json.loads(confirmed.stdout)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_first_save_confirms_keys_paths_and_survives_separate_processes(cli, consumer):
    report = inspect(cli)
    command = args(consumer, report)
    preview = cli(*command)
    output = json.loads(preview.stdout)
    target = cli.home / ".config/example/.env"
    assert not target.exists()
    assert output["review"]["records"][0]["account"] == "work"
    assert output["review"]["keys"][0]["source_key"] == "EXAMPLE_KEY"
    assert output["review"]["changes"] == [{"key": "EXAMPLE_KEY", "source": "missing", "path": str(target), "real_path": str(target), "operation": "add"}]
    pending = cli("configure-status", "--requirements", str(consumer), extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert output["confirmation_token"] in json.loads(pending.stdout)["pending"]
    confirmed = cli(*command, "--confirm", output["confirmation_token"])
    assert confirmed.returncode == 0, confirmed.stderr
    assert target.read_text() == "EXAMPLE_KEY='synthetic-secret-one'\n"
    cli.assert_private(target)
    persisted = cli.home / ".config/secret-book/consumer-configurations.json"
    for text in [preview.stdout, pending.stdout, confirmed.stdout, persisted.read_text()]:
        assert "synthetic-secret-one" not in text
        assert "do-not-copy" not in text
    # A later table rotation does not mutate the local file.
    update_record(cli, secret="EXAMPLE_KEY=synthetic-rotated")
    assert target.read_text() == "EXAMPLE_KEY='synthetic-secret-one'\n"


@pytest.mark.parametrize("location", ["project", "project_skill", "plugin", "plugin_skill", "subdir", "ordinary"])
def test_repair_replaces_actual_source_even_when_scope_says_global(cli, consumer, location):
    root = cli.home / ".config/example"
    paths = {"project": cli.cwd / ".env.local", "project_skill": cli.cwd / ".env.example-a",
             "plugin": root / ".env.local", "plugin_skill": root / ".env.example-a",
             "subdir": root / "example-a/.env", "ordinary": cli.home / ".config/example-a/.env"}
    path = write(paths[location], "# keep this\nUNRELATED=keep\nEXAMPLE_KEY=wrong\nEXAMPLE_KEY=also-wrong\n")
    mode = cli.cwd.stat().st_mode
    preview, saved = configure(cli, consumer, inspect(cli), "--scope", "global")
    assert preview["review"]["changes"][0]["path"] == str(path)
    assert saved["written"] == [{"path": str(path), "keys": ["EXAMPLE_KEY"]}]
    assert path.read_text() == "# keep this\nUNRELATED=keep\nEXAMPLE_KEY='synthetic-secret-one'\n"
    assert not (root / ".env").exists()
    assert cli.cwd.stat().st_mode == mode


@pytest.mark.parametrize("scope,skill_only,relative", [("project", False, ".env.local"), ("global", True, ".config/example/.env.example-a")])
def test_new_fields_respect_explicit_project_or_skill_choice(cli, consumer, scope, skill_only, relative):
    extra = ["--scope", scope] + (["--skill-only"] if skill_only else [])
    configure(cli, consumer, inspect(cli), *extra)
    target = (cli.cwd if scope == "project" else cli.home) / relative
    assert "synthetic-secret-one" in target.read_text()


def test_environment_failure_is_not_hidden_by_new_global_file(cli, consumer):
    env = {"EXAMPLE_KEY": "wrong-environment-key", "FAKE_LARK_FAIL_ON_CALL": "1"}
    result = cli(*args(consumer, inspect(cli, env=env)), extra_env=env)
    assert json.loads(result.stdout)["status"] == "environment_source"
    assert not (cli.home / ".config/example").exists()


def test_global_disabled_requires_real_readable_target(cli, consumer):
    report = inspect(cli, global_enabled=False)
    result = cli(*args(consumer, report), extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert json.loads(result.stdout)["status"] == "global_disabled"
    configure(cli, consumer, report, "--scope", "project")


@pytest.mark.parametrize("change", ["local_file", "higher_override", "environment", "secret", "account", "contract"])
def test_changed_inputs_invalidate_confirmation(cli, consumer, change):
    target = write(cli.home / ".config/example/.env", "EXAMPLE_KEY=original")
    report = inspect(cli)
    command = args(consumer, report)
    preview = json.loads(cli(*command).stdout)
    env = {}
    if change == "local_file":
        target.write_text("EXAMPLE_KEY=user-edited")
    elif change == "higher_override":
        write(cli.cwd / ".env.local", "EXAMPLE_KEY=override")
    elif change == "environment":
        env["EXAMPLE_KEY"] = "new-env"
    elif change == "contract":
        contract = json.loads(consumer.read_text()); contract["keys"]["EXAMPLE_KEY"]["description"] = "changed"
        consumer.write_text(json.dumps(contract))
    else:
        update_record(cli, **({"secret": "EXAMPLE_KEY=rotated"} if change == "secret" else {"account": "other"}))
    before = target.read_bytes()
    result = cli(*command, "--confirm", preview["confirmation_token"], extra_env=env)
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] in ("inspection_stale", "confirmation_required")
    assert target.read_bytes() == before


def test_related_endpoint_change_requires_explicit_field_selection(cli, consumer):
    url = write(cli.cwd / ".env.local", "EXAMPLE_URL=https://wrong.invalid\nOTHER=value\n")
    report = inspect(cli)
    rejected = cli(*args(consumer, report))
    assert json.loads(rejected.stdout)["status"] == "related_fields_differ"
    preview, saved = configure(cli, consumer, report, "--key", "EXAMPLE_URL")
    assert len(saved["written"]) == 2
    assert "https://example.invalid" in url.read_text()
    assert "OTHER=value" in url.read_text()
    assert {row["key"] for row in preview["review"]["changes"]} == {"EXAMPLE_KEY", "EXAMPLE_URL"}


def test_mapping_shows_actual_key_and_missing_record_cannot_write(cli, consumer):
    update_record(cli, secret="API_KEY=synthetic-mapped")
    report = inspect(cli)
    missing = cli(*args(consumer, report))
    assert json.loads(missing.stdout)["status"] == "missing_keys"
    preview, _ = configure(cli, consumer, report, "--map", "EXAMPLE_KEY=API_KEY")
    assert preview["review"]["keys"][0]["source_key"] == "API_KEY"
    assert "synthetic-mapped" in (cli.home / ".config/example/.env").read_text()


@pytest.mark.parametrize("tracked,ignored", [(True, True), (False, False), (False, True)])
def test_git_protection_does_not_modify_index_or_ignore_rules(cli, consumer, tracked, ignored):
    subprocess.run(["git", "init", "-q", str(cli.cwd)], check=True)
    if ignored:
        write(cli.cwd / ".gitignore", ".env.local\n")
    path = write(cli.cwd / ".env.local", "EXAMPLE_KEY=synthetic-old\n")
    if tracked:
        subprocess.run(["git", "-C", str(cli.cwd), "add", "-f", ".env.local"], check=True)
    report = inspect(cli)
    if not tracked and ignored:
        configure(cli, consumer, report)
        assert "synthetic-secret-one" in path.read_text()
    else:
        result = cli(*args(consumer, report), extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
        assert json.loads(result.stdout)["status"] == "unsafe_target"
        assert path.read_text() == "EXAMPLE_KEY=synthetic-old\n"


def test_concurrent_confirmation_writes_once(cli, consumer):
    report = inspect(cli)
    command = args(consumer, report)
    token = json.loads(cli(*command).stdout)["confirmation_token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: cli(*command, "--confirm", token), range(2)))
    assert sorted(r.returncode for r in results) == [0, 3]


def test_legacy_consumer_runner_does_not_execute_child(cli, consumer):
    target = cli.cwd / "never-created"
    result = cli("run", "--requirements", str(consumer), "--", "touch", str(target), extra_env={"FAKE_LARK_FAIL_ON_CALL": "1"})
    assert result.returncode == 1
    assert "configure" in result.stderr
    assert not target.exists()


def test_symlink_repair_preserves_link_and_writes_confirmed_entity(cli, consumer):
    entity = write(cli.home / 'private-config/env', 'EXAMPLE_KEY=old\nOTHER=keep\n')
    target = cli.cwd / '.env.local'
    target.symlink_to(entity)
    preview, result = configure(cli, consumer, inspect(cli))
    assert target.is_symlink()
    assert preview['review']['changes'][0]['real_path'] == str(entity)
    assert result['written'][0]['path'] == str(entity)
    assert 'synthetic-secret-one' in entity.read_text()
    assert 'OTHER=keep' in entity.read_text()


def test_corrupt_pending_metadata_is_reported_without_overwrite(cli, consumer):
    path = write(cli.home / '.config/secret-book/consumer-configurations.json',
                 '{"schema_version":1,"pending":{"bad":{}},"history":[]}')
    before = path.read_bytes()
    result = cli('configure-status', '--requirements', str(consumer), extra_env={'FAKE_LARK_FAIL_ON_CALL': '1'})
    assert result.returncode == 1
    assert '未覆盖' in result.stderr
    assert path.read_bytes() == before


def test_multiple_file_failure_records_completed_writes_and_requires_readback(cli, consumer, monkeypatch):
    import sys
    from types import SimpleNamespace
    sys.path.insert(0, str(cli.repo / 'scripts'))
    import secret_book as api
    monkeypatch.setenv('HOME', str(cli.home))
    monkeypatch.setenv('USERPROFILE', str(cli.home))
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(cli.cwd.parent))
    monkeypatch.chdir(cli.cwd)
    for key in ('EXAMPLE_KEY', 'EXAMPLE_URL', 'CODEX_HOME'):
        monkeypatch.delenv(key, raising=False)
    project = write(cli.cwd / '.env.local', 'EXAMPLE_URL=https://old.invalid\n')
    report = inspect(cli)
    snapshot = SimpleNamespace(ids=(), config_id='cfg_test', config_name='test', source='global_current',
        app_token='app_work', table_id='tbl_work', lark_profile='work-profile', feishu_app_id='cli_test', feishu_user_open_id='ou_test')
    monkeypatch.setattr(api, '_snapshot_for_args', lambda _: snapshot)
    backend = SimpleNamespace(find=lambda *a, **kw: [{'id': 'sec_example', 'name': 'test', 'service': 'example', 'account': 'work',
                                                    'secret': 'EXAMPLE_KEY=synthetic-secret-one'}])
    monkeypatch.setattr(api, 'require_backend', lambda _: backend)
    command = args(consumer, report, '--key', 'EXAMPLE_URL')
    parsed = api.build_parser().parse_args(command)
    with pytest.raises(api.ProfileGuidance) as preview:
        api.cmd_consumer(parsed)
    token = preview.value.payload['confirmation_token']
    original_write = api._write_env_updates
    def fail_second(path, values):
        if path == project:
            raise OSError('synthetic write failure')
        original_write(path, values)
    monkeypatch.setattr(api, '_write_env_updates', fail_second)
    parsed.confirm = token
    with pytest.raises(api.ProfileGuidance) as failed:
        api.cmd_consumer(parsed)
    output = failed.value.payload
    assert output['status'] == 'write_incomplete'
    assert output['failed_path'] == str(project)
    assert output['written'] == [{'path': str(cli.home / '.config/example/.env'), 'keys': ['EXAMPLE_KEY']}]
    assert project.read_text() == 'EXAMPLE_URL=https://old.invalid\n'
    store = json.loads((cli.home / '.config/secret-book/consumer-configurations.json').read_text())
    assert store['history'][-1]['status'] == 'write_incomplete'
    assert token not in store['pending']
