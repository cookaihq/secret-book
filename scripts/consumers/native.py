"""Public native-config contract. The consumer owns format; Secret Book owns consent.

Only the named, packaged adapter runs, with secrets on stdin. No shell, argv secret,
dotenv emulation or access to the consumer's private task journal is involved.
"""
import json
from pathlib import Path
import secrets
import subprocess
import tomllib

from . import Configuration, digest, revision


class NativeConfiguration(Configuration):
    def __init__(self, api, args):
        super().__init__(api, args)
        native = self.contract.get("native_writer")
        if (native != {"protocol": "secret-book.native-configuration/v1", "adapter": "setup-agent-model",
                       "entrypoint": "scripts/setup_agent_model.py"}
                or self.consumer != {"kind": "plugin", "name": "setup-aihub", "skills": ["setup-agent-model"]}
                or set(self.contract["keys"]) != {"AIHUB_API_KEY"}):
            self.api.die("未登记的原生写入适配器；不能把任意命令作为凭证写入器")
        self.root = Path(args.requirements).resolve().parent.parent
        self.entry = self.root / native["entrypoint"]
        if self.root.name != "setup-agent-model" or not self.entry.is_file():
            self.api.die("请定位完整 Setup AIHub Plugin 内的 setup-agent-model 原生适配器")
        plugin = self.root.parent.parent
        versions = set()
        for host in (".claude-plugin", ".codex-plugin", ".codebuddy-plugin"):
            manifest = self.read_json(plugin / host / "plugin.json")
            if manifest.get("name") != "setup-aihub":
                self.api.die("原生适配器不属于所声明的完整 Plugin")
            versions.add(manifest.get("version"))
        try:
            runtime_version = tomllib.loads((self.root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
            version_tuple = tuple(int(part) for part in runtime_version.split("."))
        except (ValueError, KeyError, OSError):
            self.api.die("无法核对原生适配器版本")
        if versions != {runtime_version} or len(version_tuple) != 3 or version_tuple < (2, 1, 0):
            self.api.die("原生配置契约需要完整且版本一致的 Setup AIHub 2.1.0 或更新版本")

    def adapter_revision(self):
        plugin = self.root.parent.parent
        files = sorted(p for skill in (self.root, self.root.parent / "setup-api-key")
                       for p in (skill / "scripts").rglob("*") if p.suffix in (".py", ".ps1", ".applescript"))
        files += [self.root / name for name in ("pyproject.toml", "uv.lock", ".python-version")]
        files += [plugin / folder / "plugin.json" for folder in (".claude-plugin", ".codex-plugin", ".codebuddy-plugin")]
        if any(not path.resolve().is_relative_to(plugin.resolve()) for path in files):
            self.api.die("原生适配器文件不能指向 Plugin 以外")
        return digest({str(path): revision(path) for path in files})

    def call(self, operation, plan, values=None):
        message = {"operation": operation, "plan": plan}
        if values is not None:
            message["values"] = values
        try:
            process = subprocess.run(["uv", "run", "--frozen", "--no-dev", "--project", str(self.root),
                                      "python", str(self.entry), "native-bridge"],
                                     input=json.dumps(message).encode(), capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            self.guidance("native_adapter_unavailable", message="原生适配器未返回可核验结果；写入阶段须先回读")
        try:
            result = json.loads(process.stdout)
        except (ValueError, UnicodeError):
            self.guidance("native_adapter_invalid_response")
        if not isinstance(result, dict) or process.returncode:
            reason = result.get("reason") if isinstance(result, dict) else None
            self.guidance("native_adapter_rejected", reason=reason if isinstance(reason, str) and
                          len(reason) < 100 and all(c.isalnum() or c == "_" for c in reason) else "unknown")
        expected = {"inspect": "setup-agent-model.native-check/v1", "apply": "setup-agent-model.write/v1"}
        if operation in expected and result.get("schema") != expected[operation]:
            self.guidance("native_adapter_invalid_response")
        return result

    def inspection(self):
        report = self.read_json(Path(self.args.inspection))
        if (set(report) != {"schema", "consumer", "skill", "cwd", "plan"}
                or report.get("schema") != "secret-book.native-inspection/v1"
                or report.get("consumer") != {"kind": "plugin", "name": "setup-aihub"}
                or report.get("skill") != "setup-agent-model" or report.get("cwd") != str(Path.cwd().absolute())):
            self.api.die("原生来源报告与实际调用目录或业务身份不一致")
        plan = report["plan"]
        if not isinstance(plan, dict):
            self.api.die("原生计划不是 JSON 对象")
        result = self.call("inspect", plan)
        if result.get("plan_fingerprint") != plan.get("fingerprint"):
            self.guidance("native_adapter_invalid_response")
        return report, result

    def configure(self):
        from agent_rules import AGENTS, inspect_agent
        if self.args.agent not in AGENTS:
            self.api.die("请使用当前实际执行宿主身份")
        if self.args.scope or self.args.skill_only or self.args.key != ["AIHUB_API_KEY"]:
            self.api.die("原生文件范围与字段来自已选客户端报告，不接受 dotenv scope/skill-only 参数")
        if len(set(self.args.id)) != len(self.args.id):
            self.api.die("--id 不能重复")
        report, inspection = self.inspection()
        plan = report["plan"]
        if plan["request"]["key_mode"] != "replace":
            self.api.die("此原生计划不需要替换 Key；交回调用方保留现有值")
        target = Path(plan["target"]["path"])
        self.check_target(target)
        snapshot = self.api._snapshot_for_args(self.args)
        if snapshot.ids:
            self.api.die("旧 SECRET_BOOK_IDS 不能代替明确选择的记录")
        mapping = self.mapping()
        values, selection = self.records(snapshot, self.args.id, mapping)
        agent_report = inspect_agent(self.args.agent, cwd=Path.cwd(), config_dir=self.args.agent_config_dir,
                                     workspace=self.args.agent_workspace, agent_id=self.args.agent_id)
        adapter = self.adapter_revision()
        review = {"consumer": self.consumer, "skill": report["skill"], "cwd": report["cwd"],
                  "source": {"config_id": snapshot.config_id, "config_name": snapshot.config_name,
                             "layer": snapshot.source, "app_token": snapshot.app_token, "table_id": snapshot.table_id,
                             "lark_profile": snapshot.lark_profile, "feishu_app_id": snapshot.feishu_app_id,
                             "feishu_user_open_id": snapshot.feishu_user_open_id},
                  **selection, "agent_rules": agent_report,
                  "native": {"protocol": "secret-book.native-configuration/v1", "client": plan["request"]["client"],
                             "path": str(target), "fields": plan["secret_fields"], "changes": plan["changes"],
                             "shared_with": plan["shared_with"], "notices": plan["observations"]["notices"],
                             "plan_fingerprint": plan["fingerprint"], "adapter_revision": adapter}}
        request_digest = digest({"contract": self.contract, "inspection": report, "review": review, "values": values})
        with self.api._config_file_lock(self.path):
            store = self.read_store()
            unresolved = [item for item in store["history"] if item.get("native_plan") == plan["fingerprint"] and
                          item.get("status") in ("writing", "write_incomplete")]
            if unresolved:
                self.guidance("native_readback_required", path=str(target),
                              message="此前写入结果未明，先 configure-reconcile 原生报告；不能重放旧保存")
            pending = store["pending"].get(self.args.confirm)
            if not pending or pending["digest"] != request_digest:
                token = secrets.token_hex(24)
                while len(store["pending"]) >= 32:
                    del store["pending"][next(iter(store["pending"]))]
                store["pending"][token] = {"digest": request_digest, "review": review,
                    "arguments": {key: getattr(self.args, key) for key in (
                        "requirements", "inspection", "id", "map", "key", "agent", "agent_config_dir",
                        "agent_workspace", "agent_id", "use_global_config", "config_name")}}
                self.write_store(store)
                self.guidance("confirmation_required", confirmation_token=token, review=review,
                              previous_confirmation_invalid=bool(self.args.confirm),
                              message="一次确认表/身份、记录/映射、原生文件字段、模型/default 和共享影响；Setup 不再重复确认")
            self.inspection()
            if self.adapter_revision() != adapter:
                self.guidance("native_adapter_changed")
            operation = {"consumer": self.consumer, "review": review, "status": "writing", "written": [],
                         "native_plan": plan["fingerprint"]}
            store["pending"].pop(self.args.confirm)
            store["history"] = (store["history"] + [operation])[-32:]
            self.write_store(store)
            self.api._task_before_write("local", str(target))
            try:
                receipt = self.call("apply", plan, values)
                if (receipt.get("plan_fingerprint") != plan["fingerprint"] or receipt.get("path") != str(target)
                        or receipt.get("status") != "written" or receipt.get("key_matches") is not True
                        or receipt.get("revision") != revision(target)):
                    self.guidance("native_readback_failed")
            except (OSError, self.api.ProfileGuidance):
                operation["status"] = "write_incomplete"
                self.write_store(store)
                self.guidance("native_readback_required", path=str(target),
                              message="原生写入结果未明；先回读，禁止自动重放")
            operation.update(status="written", written=[{"path": str(target), "keys": ["AIHUB_API_KEY"]}], receipt=receipt)
            self.write_store(store)
            print(json.dumps({"schema": "secret-book.consumer-write/v2", "status": "written", "consumer": self.consumer,
                              "native_receipt": receipt, "verification": "file_readback_only",
                              "next_step": "把公开 receipt 交回 Setup 独立复查；不再写入、不自动发模型请求"}, ensure_ascii=False))

    def reconcile(self):
        """No table read, credential values or implicit retries during recovery."""
        report = self.read_json(Path(self.args.inspection))
        if (report.get("schema") != "secret-book.native-inspection/v1" or report.get("cwd") != str(Path.cwd().absolute())
                or report.get("consumer") != {"kind": "plugin", "name": "setup-aihub"}
                or report.get("skill") != "setup-agent-model"):
            self.api.die("恢复须使用原调用目录和原生来源报告")
        plan = report["plan"]
        observed = self.call("verify", plan)
        with self.api._config_file_lock(self.path):
            store = self.read_store()
            for item in store["history"]:
                if item.get("native_plan") == plan["fingerprint"] and item.get("status") in ("writing", "write_incomplete"):
                    item.update(status="readback_needs_review", readback=observed)
            self.write_store(store)
        print(json.dumps({"schema": "secret-book.native-recovery/v1", "status": "readback_needs_review", "readback": observed,
                          "next_step": "存在 Key 不证明已替换为预期值；核对现状并生成新计划，旧确认失效"}, ensure_ascii=False))
