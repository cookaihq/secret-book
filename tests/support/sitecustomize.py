"""Fault injection for process-level atomic-write tests; inactive by default."""

import os
import stat


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
