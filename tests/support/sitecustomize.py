"""Fault injection for process-level atomic-write tests; inactive by default."""

import os
import json
import stat
import subprocess
import sys
from pathlib import Path


if os.name == "nt" and os.environ.get("FAKE_LARK_STATE"):
    # Windows cannot execute the POSIX shebang fixture. Keep lark-cli mocked as
    # a separate Python process; never fall through to a real logged-in client.
    _popen = subprocess.Popen

    class FakeLarkPopen(_popen):
        def __init__(self, args, *positional, **kwargs):
            if isinstance(args, (list, tuple)) and args and args[0] == "lark-cli":
                args = [sys.executable, str(Path(__file__).with_name("fake_lark_cli.py")), *args[1:]]
            super().__init__(args, *positional, **kwargs)

    subprocess.Popen = FakeLarkPopen


if os.environ.get("FAKE_LARK_TIMEOUT_SHORTCUT"):
    _run = subprocess.run

    def timeout_lark(args, *positional, **kwargs):
        if (isinstance(args, (list, tuple)) and args[:2] == ["lark-cli", "base"]
                and args[2] == os.environ["FAKE_LARK_TIMEOUT_SHORTCUT"]):
            with Path(os.environ["FAKE_LARK_LOG"]).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(args[1:]) + "\n")
            assert kwargs["timeout"] in (60, 120)
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return _run(args, *positional, **kwargs)

    subprocess.run = timeout_lark


FAULT = os.environ.get("SECRET_BOOK_TEST_ATOMIC_FAULT")

if FAULT == "replace":
    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    os.replace = fail_replace
elif FAULT == "directory-fsync":
    real_fsync = os.fsync

    def fail_directory_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected directory fsync failure")
        return real_fsync(fd)

    os.fsync = fail_directory_fsync
    if os.name == "nt":
        # Native Windows uses post-rename readback instead of directory fsync.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import local_platform

        def fail_readback(_path, _expected):
            raise OSError("injected post-replace readback failure")

        local_platform.sync_replaced_file = fail_readback
