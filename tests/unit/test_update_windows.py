"""Run the shipped PowerShell adapter against isolated local Git repositories."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell update adapter")
REPO = Path(__file__).resolve().parents[2]


def test_update_check_reports_then_requires_confirmation_and_fast_forwards(tmp_path):
    source = tmp_path / "upstream"
    install = tmp_path / "中文 folder" / "secret-book"
    user_dir = tmp_path / "isolated-user"
    env = {**os.environ, "USERPROFILE": str(user_dir), "GIT_TERMINAL_PROMPT": "0"}

    def git(directory, *args):
        proc = subprocess.run(["git", "-C", str(directory), "-c", "user.name=Secret Book Test",
            "-c", "user.email=secret-book-test@example.invalid", "-c", "core.hooksPath=/dev/null", *args],
            capture_output=True, timeout=20, env=env)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    def commit(version):
        (source / "SKILL.md").write_text("---\nname: secret-book\nversion: " + version +
            "\ndescription: v" + version + "｜test\n---\n", encoding="utf-8")
        git(source, "add", ".")
        git(source, "commit", "-qm", version)
        return git(source, "rev-parse", "HEAD")

    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    (source / "scripts").mkdir()
    shutil.copyfile(REPO / "scripts/check_update.ps1", source / "scripts/check_update.ps1")
    before = commit("2.3.0")
    install.parent.mkdir()
    git(tmp_path, "clone", "-q", str(source), str(install))
    original_bytes = (install / "SKILL.md").read_bytes()
    after = commit("2.4.0")

    def update(*args):
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(install / "scripts/check_update.ps1"), *args], env=env, capture_output=True, timeout=20)

    available = update()
    assert available.returncode == 10, available.stdout + available.stderr
    assert b"v2.3.0" in available.stdout and b"v2.4.0" in available.stdout
    assert git(install, "rev-parse", "HEAD") == before
    throttled = update()
    assert throttled.returncode == 0
    assert git(install, "rev-parse", "HEAD") == before

    (install / "SKILL.md").write_text("local edit", encoding="utf-8")
    refused = update("-Pull")
    assert refused.returncode == 1
    assert (install / "SKILL.md").read_text() == "local edit"
    assert git(install, "rev-parse", "HEAD") == before
    # Restore only the synthetic fixture's text; the adapter never discards it.
    (install / "SKILL.md").write_bytes(original_bytes)
    applied = update("-Pull")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert git(install, "rev-parse", "HEAD") == after


def test_copy_install_skips_git_and_disabled_check_never_creates_stamp(tmp_path):
    install = tmp_path / "secret-book"
    (install / "scripts").mkdir(parents=True)
    script = install / "scripts/check_update.ps1"
    shutil.copyfile(REPO / "scripts/check_update.ps1", script)
    user_dir = tmp_path / "isolated-user"
    config = user_dir / ".config/secret-book/.env"
    config.parent.mkdir(parents=True)
    env = {**os.environ, "USERPROFILE": str(user_dir), "GIT_CEILING_DIRECTORIES": str(tmp_path)}
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    copied = subprocess.run(command, env=env, capture_output=True, timeout=20)
    assert copied.returncode == 0, copied.stderr
    assert not config.with_name(".update-check-stamp").exists()
    config.write_text("AUTO_UPDATE_CHECK=0\n", encoding="utf-8")
    disabled = subprocess.run(command, env=env, capture_output=True, timeout=20)
    assert disabled.returncode == 0, disabled.stderr
    assert not config.with_name(".update-check-stamp").exists()
    assert config.read_text() == "AUTO_UPDATE_CHECK=0\n"
