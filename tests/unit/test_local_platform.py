"""Exercise native OS behavior with synthetic values and isolated configuration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_cli_bindings import _save_config

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import local_platform
import secret_book as api


def test_atomic_write_sets_private_permissions_before_values_and_preserves_parent(cli, monkeypatch):
    target = cli.cwd / ".env.local"
    target.write_bytes(b"ORIGINAL=keep\n")
    before_parent = cli.cwd.stat().st_mode
    before_acl = bytes(local_platform._read_security(cli.cwd)) if os.name == "nt" else None
    original_permissions = api.private_permissions

    def verify_empty(path, **kwargs):
        assert path.read_bytes() == b""
        original_permissions(path, **kwargs)

    monkeypatch.setattr(api, "private_permissions", verify_empty)
    api._atomic_replace_bytes(target, b"SYNTHETIC_TOKEN=for-tests-only\n")
    cli.assert_private(target)
    assert cli.cwd.stat().st_mode == before_parent
    if before_acl is not None:
        assert bytes(local_platform._read_security(cli.cwd)) == before_acl
    assert target.read_bytes() == b"SYNTHETIC_TOKEN=for-tests-only\n"


def test_permission_failure_keeps_original_and_removes_empty_temporary_file(cli, monkeypatch):
    target = cli.cwd / ".env.local"
    target.write_bytes(b"ORIGINAL=keep\n")

    def deny(*args, **kwargs):
        raise PermissionError("synthetic ACL failure")

    monkeypatch.setattr(api, "private_permissions", deny)
    with pytest.raises(PermissionError):
        api._atomic_replace_bytes(target, b"SYNTHETIC_TOKEN=must-not-be-written\n")
    assert target.read_bytes() == b"ORIGINAL=keep\n"
    assert list(cli.cwd.glob(".env.local.*")) == []


def test_utf8_bom_config_can_be_read_and_replaced(cli):
    _save_config(cli, name="中文名称", app_token="app_test", table_id="tbl_test", profile="work-profile")
    path = cli.home / ".config/secret-book/.env"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    result = cli("config", "rename", "--name", "中文名称", "--new-name", "中文 renamed")
    assert result.returncode == 0, result.stderr
    listed = cli("config", "list")
    assert "中文 renamed" in listed.stdout
    assert path.read_text(encoding="utf-8").count("SECRET_BOOK_CONFIGS_JSON=") == 1


@pytest.mark.parametrize("bind", [False, True])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_run_injects_unicode_and_preserves_exit_code(cli, bind, exit_code):
    _save_config(cli, name="工作", app_token="app_test", table_id="tbl_test", profile="work-profile")
    state = json.loads(cli.state_path.read_text(encoding="utf-8"))
    state["records"] = {"app_test": [{"id": "sec_native", "name": "native", "_record_id": "rec_native",
        "secret": "SYNTHETIC_TOKEN=测试-secret-🧪", "visible_to": None}]}
    cli.state_path.write_text(json.dumps(state), encoding="utf-8")
    child = cli.cwd / "check injected value.py"
    child.write_text("import os,sys; "
                     "sys.exit(int(sys.argv[1]) if os.environ['SYNTHETIC_TOKEN'] == '测试-secret-🧪' else 99)",
                     encoding="utf-8")
    result = cli("run", "--id", "sec_native", "--use-global-config", *(["--bind"] if bind else []),
                 "--", sys.executable, str(child), str(exit_code))
    assert result.returncode == exit_code, result.stderr
    assert "测试-secret-🧪" not in result.stderr
    assert "测试-secret-🧪" not in result.stdout
    bindings = cli.home / ".config/secret-book/bindings.json"
    assert bindings.exists() == (bind and exit_code == 0)


def test_clipboard_value_stays_in_stdin_and_preserves_unicode(monkeypatch, capsys):
    value = "synthetic-中文-🧪"
    monkeypatch.setattr(api, "require_backend", lambda _: object())
    monkeypatch.setattr(api, "resolve_records", lambda *a, **kw: [{"name": "test", "secret": "TOKEN=" + value}])
    calls = []

    def capture(command, **kwargs):
        calls.append((command, kwargs))
        assert value not in " ".join(command)
        assert kwargs["input"].decode("utf-16" if os.name == "nt" else "utf-8") == value
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(api.subprocess, "run", capture)
    api.cmd_copy(SimpleNamespace(id=["sec_test"], name=None, key="TOKEN"))
    assert len(calls) == 1
    assert value not in capsys.readouterr().out


@pytest.mark.skipif(os.name != "nt", reason="native Windows process and locking behavior")
def test_windows_lock_contends_between_processes_then_releases(tmp_path):
    path = tmp_path / "write.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    code = "import os,sys; sys.path.insert(0,sys.argv[1]); from local_platform import exclusive_lock; " \
           "fd=os.open(sys.argv[2],os.O_RDWR);\nwith exclusive_lock(fd,timeout=0.1): pass\n"
    command = [sys.executable, "-c", code, str(REPO / "scripts"), str(path)]
    try:
        with local_platform.exclusive_lock(fd):
            blocked = subprocess.run(command, capture_output=True, timeout=10)
            assert blocked.returncode != 0
            assert b"TimeoutError" in blocked.stderr
        released = subprocess.run(command, capture_output=True, timeout=10)
        assert released.returncode == 0, released.stderr
    finally:
        os.close(fd)


@pytest.mark.skipif(os.name != "nt", reason="native Windows bootstrap and PowerShell quoting")
def test_windows_bootstrap_rebuilds_relative_environment_and_handoff_runs_in_powershell(cli, tmp_path):
    copied = tmp_path / "中文 skill's folder"
    (copied / "scripts").mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock", ".python-version"):
        shutil.copyfile(REPO / name, copied / name)
    for name in ("secret_book.py", "local_platform.py"):
        shutil.copyfile(REPO / "scripts" / name, copied / "scripts" / name)
    script = copied / "scripts/secret_book.py"
    env = cli.build_env({"UV_PROJECT_ENVIRONMENT": "隔离 venv", "UV_OFFLINE": "1"})
    command = [sys._base_executable, str(script), "init-create", "--lark-profile", "work-profile"]
    pending = subprocess.run(command, env=env, cwd=cli.cwd, capture_output=True, encoding="utf-8", timeout=60)
    assert pending.returncode == 3, pending.stderr
    assert (copied / "隔离 venv/Scripts/python.exe").is_file()
    assert not (cli.cwd / "隔离 venv").exists()
    token = json.loads(pending.stdout)["confirmation_token"]
    created = subprocess.run([*command, "--confirm-identity", token], env=env, cwd=cli.cwd,
                             capture_output=True, encoding="utf-8", timeout=30)
    assert created.returncode == 0, created.stderr
    handoff = next(line for line in created.stdout.splitlines() if line.startswith("uv run --project "))
    handoff = handoff.replace("'<名称>'", "'工作; $x & ''quoted''' ")
    saved = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", handoff],
                           env=env, cwd=cli.cwd, capture_output=True, timeout=60)
    assert saved.returncode == 0, saved.stderr
    listed = cli("config", "list")
    assert "工作; $x & 'quoted'" in listed.stdout
