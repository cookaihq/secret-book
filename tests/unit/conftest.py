import json
import os
import subprocess
import sys
import shlex
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "secret_book.py"
FAKE_LARK = REPO / "tests" / "support" / "fake_lark_cli.py"


@pytest.fixture
def cli(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    cwd.mkdir()
    bin_dir.mkdir()

    if os.name != "nt":
        wrapper = bin_dir / "lark-cli"
        wrapper.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(FAKE_LARK)) + " \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    state_path = tmp_path / "lark-state.json"
    log_path = tmp_path / "lark-calls.jsonl"
    state = {
        "profiles": [{
            "name": "work-profile",
            "tokenStatus": "valid",
            "active": False,
            "user": "Test User",
            "brand": "feishu",
            "appId": "cli_test_work",
        }, {
            "name": "personal-profile",
            "tokenStatus": "valid",
            "active": True,
            "user": "Personal User",
            "brand": "feishu",
            "appId": "cli_test_personal",
        }],
        "auth": {
            "work-profile": {
                "identity": "user",
                "identities": {"user": {
                    "status": "ready",
                    "available": True,
                    "tokenStatus": "valid",
                    "openId": "ou_test_work",
                }},
            },
            "personal-profile": {
                "identity": "user",
                "identities": {"user": {
                    "status": "ready",
                    "available": True,
                    "tokenStatus": "valid",
                    "openId": "ou_test_personal",
                }},
            },
        },
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    def build_env(extra_env=None):
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("SECRET_BOOK_", "CODEX_", "CLAUDE_", "OPENCLAW_", "HERMES_", "WORKBUDDY_", "CODEBUDDY_", "COPILOT_", "OPENCODE_"))
        }
        env.update({
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONUTF8": "1",
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
            "PYTHONPATH": str(REPO / "tests" / "support") + os.pathsep + env.get("PYTHONPATH", ""),
            "FAKE_LARK_STATE": str(state_path),
            "FAKE_LARK_LOG": str(log_path),
            "GIT_CEILING_DIRECTORIES": str(tmp_path),
        })
        env.update(extra_env or {})
        return env

    def run(*args, input_text=None, extra_env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=cwd,
            env=build_env(extra_env),
            input=input_text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=60,
        )

    def success_command(path):
        if os.name == "nt":
            path = path.with_suffix(".cmd")
            path.write_text("@exit /b 0\n", encoding="ascii")
        else:
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(0o755)
        return path

    def assert_private(path):
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o600
            return
        # Independent ACL reader, not the implementation's ctypes verifier.
        checked = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "$acl = [System.IO.File]::GetAccessControl($env:SECRET_BOOK_TEST_ACL_PATH); "
            "$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
            "$rules = @($acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])); "
            "if (-not $acl.AreAccessRulesProtected -or $rules.Count -ne 2) { exit 1 }; "
            "foreach ($rule in $rules) { if ($rule.IsInherited -or "
            "$rule.AccessControlType -ne 'Allow' -or "
            "$rule.FileSystemRights -ne 'FullControl' -or "
            "$rule.IdentityReference.Value -notin @($sid,'S-1-5-18')) { exit 2 } }"],
            env={**{key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"},
                 "SECRET_BOOK_TEST_ACL_PATH": str(path)}, capture_output=True, timeout=20)
        assert checked.returncode == 0, checked.stderr.decode("utf-8", errors="replace")

    run.home = home
    run.cwd = cwd
    run.repo = REPO
    run.script = SCRIPT
    run.state_path = state_path
    run.log_path = log_path
    run.build_env = build_env
    run.success_command = success_command
    run.assert_private = assert_private
    return run
