"""Task-scoped progress journal. Storage location and CLI routing belong to the caller.

The journal contains metadata only. It never stores command arguments, stdin,
credential payloads, login device codes or raw service responses.
"""
from __future__ import annotations

import copy
import hashlib
import json
import secrets


ROLES = ("administrator", "maintainer", "consumer", "developer")
BASES = ("explicit", "inferred")
CONTEXT_KEYS = {"agent", "cwd", "platform", "installation"}
TARGET_KEYS = {"app_token", "table_id", "profile", "app_id", "open_id", "config_name",
               "config_id", "source", "url", "base_name", "record_name", "record_id"}
IDENTITY_KEYS = {"lark_profile", "app_id", "open_id", "user", "brand"}
STAGES = {"ready", "running", "waiting", "step_complete", "needs_attention", "verification_required"}


class TaskStateError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _metadata(values, allowed):
    if not isinstance(values, dict) or set(values) - allowed:
        raise TaskStateError("invalid_metadata", "任务状态只接受约定的无密钥元数据字段")
    if any(not isinstance(value, str) for value in values.values()):
        raise TaskStateError("invalid_metadata", "任务元数据字段必须是字符串")
    return dict(values)


def _valid_entry(task_id, item):
    if not isinstance(item, dict):
        return False
    current = item.get("context")
    pending = item.get("pending")
    return (item.get("id") == task_id and item.get("role") in ROLES
            and item.get("basis") in BASES and isinstance(item.get("goal"), str)
            and isinstance(item.get("stage"), str) and item["stage"] in STAGES
            and type(item.get("closed")) is bool
            and type(item.get("write_attempted")) is bool
            and isinstance(current, dict) and set(current) == CONTEXT_KEYS
            and all(isinstance(value, str) and value for value in current.values())
            and isinstance(item.get("target"), dict) and not set(item["target"]) - TARGET_KEYS
            and all(isinstance(value, str) for value in item["target"].values())
            and "pending" in item
            and (pending is None or (isinstance(pending, dict)
                 and isinstance(pending.get("operation"), dict)
                 and isinstance(pending["operation"].get("fingerprint"), str)))
            and isinstance(item.get("completed_operations"), list)
            and all(isinstance(value, str) for value in item["completed_operations"]))


class Journal:
    def __init__(self, core, path):
        self.core, self.path = core, path

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": 1, "tasks": {}}
        except (ValueError, UnicodeError) as exc:
            raise TaskStateError("state_invalid", "任务状态无法解析；保留原文件并人工核对") from exc
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or not isinstance(data.get("tasks"), dict)
                or any(not _valid_entry(task_id, item) for task_id, item in data["tasks"].items())):
            raise TaskStateError("state_invalid", "任务状态格式不受支持；不能覆盖重建")
        return data

    def _write(self, data):
        self.core._atomic_replace_bytes(
            self.path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    @staticmethod
    def _entry(data, task_id, context):
        entry = data["tasks"].get(task_id)
        if entry is None:
            raise TaskStateError("task_missing", "找不到该任务；请核对任务编号")
        if entry["context"] != context:
            raise TaskStateError("context_changed", "宿主、工作目录或安装已变化；重新确认任务，不复用旧确认")
        return entry

    def start(self, role, basis, goal, context):
        if role not in ROLES or basis not in BASES or not goal.strip() or len(goal) > 500:
            raise TaskStateError("invalid_task", "需要有效的角色、识别依据和简短任务目标（勿含密钥）")
        context = _metadata(context, CONTEXT_KEYS)
        if set(context) != CONTEXT_KEYS or not all(context.values()):
            raise TaskStateError("invalid_context", "需要实际宿主、工作目录、运行环境和安装位置")
        with self.core._config_file_lock(self.path):
            data = self._load()
            task_id = "task_" + secrets.token_hex(8)
            entry = {"id": task_id, "role": role, "basis": basis, "goal": goal,
                     "context": context, "stage": "ready", "closed": False,
                     "created_at": self.core._now(), "updated_at": self.core._now(),
                     "target": {}, "pending": None, "operation": None,
                     "write_attempted": False, "completed_operations": []}
            data["tasks"][task_id] = entry
            self._write(data)
            return copy.deepcopy(entry)

    def status(self, context, task_id=None):
        with self.core._config_file_lock(self.path):
            data = self._load()
            if task_id:
                return copy.deepcopy(self._entry(data, task_id, context))
            return [copy.deepcopy(item) for item in data["tasks"].values()
                    if item["context"] == context and not item["closed"]]

    def close(self, task_id, context, outcome):
        if outcome not in ("completed", "cancelled"):
            raise TaskStateError("invalid_outcome", "任务只能按完成或取消关闭")
        with self.core._config_file_lock(self.path):
            data = self._load()
            entry = self._entry(data, task_id, context)
            if outcome == "completed" and (entry["pending"] or entry["write_attempted"]):
                raise TaskStateError("verification_required", "仍有待确认或待核对操作；先核对结果，不能报告任务完成")
            entry.update(closed=True, outcome=outcome, updated_at=self.core._now())
            # Cancellation keeps the last operation/target for reconciliation.
            entry["pending"] = None
            self._write(data)
            return copy.deepcopy(entry)

    def begin(self, task_id, context, action, parameters, target=None):
        """Only a digest of parameters is stored; callers supply safe target metadata."""
        operation = {"action": action, "fingerprint": fingerprint({"action": action, "parameters": parameters})}
        with self.core._config_file_lock(self.path):
            data = self._load()
            entry = self._entry(data, task_id, context)
            if entry["closed"]:
                raise TaskStateError("task_closed", "任务已经关闭；新任务重新确定角色")
            if entry["write_attempted"]:
                raise TaskStateError("verification_required", "上次写入可能已执行；先核对实际状态，禁止重放。可用独立只读命令核对")
            previous_pending = copy.deepcopy(entry["pending"])
            lease = secrets.token_hex(8)
            entry.update(operation=operation, lease=lease, stage="running", pending=None,
                         updated_at=self.core._now())
            if target:
                entry["target"].update(_metadata(target, TARGET_KEYS))
            self._write(data)
        return Session(self, task_id, context, lease, operation, previous_pending)


class Session:
    def __init__(self, journal, task_id, context, lease, operation, previous_pending):
        self.journal, self.task_id, self.context = journal, task_id, context
        self.lease, self.operation, self.previous_pending = lease, operation, previous_pending

    def _update(self, change):
        journal = self.journal
        with journal.core._config_file_lock(journal.path):
            data = journal._load()
            entry = journal._entry(data, self.task_id, self.context)
            if entry.get("lease") != self.lease or entry["closed"]:
                raise TaskStateError("task_changed", "任务已由另一调用推进或关闭；重新读取状态")
            change(entry)
            entry["updated_at"] = journal.core._now()
            journal._write(data)

    def set_target(self, values):
        values = _metadata(values, TARGET_KEYS)
        self._update(lambda entry: entry["target"].update(values))

    def before_write(self, kind, destination):
        def change(entry):
            if self.operation["fingerprint"] in entry["completed_operations"]:
                raise TaskStateError("operation_completed", "本任务已经完成过该写入；读取已保存结果，不重复执行")
            entry.update(write_attempted=True, stage="verification_required",
                         last_write={"kind": kind, "destination": destination})
        self._update(change)

    def waiting(self, guidance):
        # Auth guidance can contain templates and other data; persist only this allowlist.
        pending = {"status": guidance.get("status") or guidance.get("error_kind") or "guidance_required",
                   "operation": self.operation,
                   "schema": guidance.get("schema_version") or guidance.get("schema", "")}
        token = guidance.get("confirmation_token")
        if isinstance(token, str):
            pending["confirmation_token"] = token
        source_token = guidance.get("source_confirmation_token")
        if isinstance(source_token, str):
            pending["source_confirmation_token"] = source_token
        identity = guidance.get("observed_identity")
        if isinstance(identity, dict):
            pending["observed_identity"] = {key: value for key, value in identity.items()
                                            if key in IDENTITY_KEYS and isinstance(value, str)}
        if guidance.get("schema") == "secret-book.record-maintenance/v1":
            pending["review"] = _metadata(guidance["review"],
                {"resource_namespace", "record_id", "name", "service", "account", "operation"})
        if guidance.get("status") == "table_fields_missing":
            pending["missing_fields"] = guidance["missing_fields"]
        def change(entry):
            entry["pending"] = pending
            if not entry["write_attempted"]:
                entry["stage"] = "waiting"
        self._update(change)

    def succeeded(self):
        def change(entry):
            if entry["write_attempted"]:
                entry["completed_operations"].append(self.operation["fingerprint"])
            entry.update(stage="step_complete", pending=None, write_attempted=False)
        self._update(change)

    def failed(self):
        def change(entry):
            entry["stage"] = "verification_required" if entry["write_attempted"] else "needs_attention"
        self._update(change)
