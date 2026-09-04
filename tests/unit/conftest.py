import json
import os
import subprocess
import sys
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

    wrapper = bin_dir / "lark-cli"
    wrapper.write_text(
        "#!/bin/sh\nexec " + repr(sys.executable) + " " + repr(str(FAKE_LARK)) + " \"$@\"\n",
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
            if not key.startswith("SECRET_BOOK_")
        }
        env.update({
            "HOME": str(home),
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
            "PYTHONPATH": str(REPO / "tests" / "support") + os.pathsep + env.get("PYTHONPATH", ""),
            "FAKE_LARK_STATE": str(state_path),
            "FAKE_LARK_LOG": str(log_path),
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
            capture_output=True,
        )

    run.home = home
    run.cwd = cwd
    run.repo = REPO
    run.script = SCRIPT
    run.state_path = state_path
    run.log_path = log_path
    run.build_env = build_env
    return run
