"""Preview and persist selected credentials to the caller's effective dotenv files.

This module never launches the consumer. Inspection comes from its own loader; all
values travel from the backend to the confirmed files inside this process only.
"""
import hashlib
import json
import os
import re
import secrets
import subprocess
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def revision(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


class Configuration:
    def __init__(self, api, args):
        self.api, self.args = api, args
        self.path = Path.home() / ".config" / "secret-book" / "consumer-configurations.json"
        self.contract = self.read_json(Path(args.requirements))
        self.validate_contract()
        self.consumer = self.contract["consumer"]

    def read_json(self, path):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (OSError, ValueError):
            self.api.die(f"无法读取有效的 JSON 对象：{path}")

    def validate_contract(self):
        c = self.contract
        slug = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
        consumer, keys = c.get("consumer", {}), c.get("keys", {})
        if (type(c.get("schema_version")) is not int or c["schema_version"] != 1 or not isinstance(consumer, dict)
                or consumer.get("kind") not in ("plugin", "skill")
                or not isinstance(consumer.get("name"), str)
                or not slug.fullmatch(consumer["name"]) or not isinstance(keys, dict) or not keys):
            self.api.die("业务配置声明不符合 schema_version=1；请核对 consumer 和 keys")
        skills = consumer.get("skills", [])
        if (not isinstance(skills, list) or any(not isinstance(s, str) or not slug.fullmatch(s) for s in skills)
                or len(skills) != len(set(skills)) or (consumer["kind"] == "plugin" and not skills)):
            self.api.die("Plugin 必须声明不同的真实 Skill 名称")
        for key, spec in keys.items():
            if (not self.api._PAYLOAD_KEY.fullmatch(key) or not isinstance(spec, dict)
                    or type(spec.get("required")) is not bool or type(spec.get("sensitive")) is not bool
                    or not isinstance(spec.get("description"), str)):
                self.api.die("配置项必须声明 required、sensitive 和 description")
            if "default" in spec and (spec["sensitive"] or not isinstance(spec["default"], str)
                                      or any(ch in spec["default"] for ch in "\x00\r\n")):
                self.api.die("default 只能是非敏感配置的单行字符串")
        groups = c.get("groups", [])
        if not isinstance(groups, list) or any(not isinstance(g, list) or len(g) < 2
                or any(not isinstance(k, str) or k not in keys for k in g) for g in groups):
            self.api.die("groups 必须由有关联的配置字段列表组成")

    def guidance(self, status, **fields):
        raise self.api.ProfileGuidance({"schema": "secret-book.consumer-guidance/v2",
                                       "status": status, "consumer": self.consumer, **fields})

    def read_store(self):
        if not self.path.exists():
            return {"schema_version": 1, "pending": {}, "history": []}
        store = self.read_json(self.path)
        if (store.get("schema_version") != 1 or not isinstance(store.get("pending"), dict)
                or not isinstance(store.get("history"), list)):
            self.api.die("consumer-configurations.json 无效；未覆盖原文件")
        if (any(not isinstance(p, dict) or not isinstance(p.get("review"), dict)
                or not isinstance(p["review"].get("consumer"), dict) or not isinstance(p.get("digest"), str)
                for p in store["pending"].values())
                or any(not isinstance(p, dict) or not isinstance(p.get("consumer"), dict)
                       for p in store["history"])):
            self.api.die("consumer-configurations.json 记录无效；未覆盖原文件")
        return store

    def write_store(self, store):
        self.api._atomic_replace_bytes(self.path, (json.dumps(store, ensure_ascii=False, indent=2) + "\n").encode())

    def show(self):
        store = self.read_store()
        print(json.dumps({"schema": "secret-book.consumer-status/v2", "consumer": self.consumer,
            "pending": {t: p for t, p in store["pending"].items() if p["review"]["consumer"] == self.consumer},
            "history": [p for p in store["history"] if p["consumer"] == self.consumer],
            "note": "仅为确认恢复与写入记录；业务程序不读取本文件，也不自动同步令牌表"}, ensure_ascii=False))

    def inspection(self):
        report = self.read_json(Path(self.args.inspection))
        expected = {"kind": self.consumer["kind"], "name": self.consumer["name"]}
        skill = report.get("skill")
        if (report.get("schema") != "secret-book.config-inspection/v1" or report.get("consumer") != expected
                or "skill" not in report
                or (skill is not None and not isinstance(skill, str))
                or (self.consumer["kind"] == "plugin" and skill is not None and skill not in self.consumer["skills"])
                or (self.consumer["kind"] == "skill" and skill != self.consumer["name"])
                or report.get("cwd") != str(Path.cwd().resolve())
                or type(report.get("global_enabled")) is not bool):
            self.api.die("配置来源报告与调用者/当前目录不一致；从实际调用目录重新运行业务配置检查")
        keys = self.contract["keys"]
        if (not isinstance(report.get("layers"), list) or not isinstance(report.get("fields"), dict)
                or not isinstance(report.get("environment"), dict)
                or set(report["fields"]) != set(keys) or set(report["environment"]) != set(keys)):
            self.api.die("配置来源报告必须含按读取顺序排列的 layers、environment 和完整 fields")
        root = Path.home() / ".config" / self.consumer["name"]
        allowed = {Path.cwd() / ".env.local", Path.cwd() / ".env"}
        if skill is not None:
            allowed.add(Path.cwd() / f".env.{skill}")
        if report["global_enabled"]:
            allowed.add(root / ".env")
            if self.consumer["kind"] == "plugin":
                allowed.add(root / ".env.local")
                if skill is not None:
                    allowed.update([root / skill / ".env.local", root / skill / ".env", root / f".env.{skill}",
                                    Path.home() / ".config" / skill / ".env"])
        layers = []
        paths = []
        for layer in report["layers"]:
            if not isinstance(layer, dict) or not isinstance(layer.get("path"), str):
                self.api.die("无效的配置层")
            path = Path(layer["path"])
            if path not in allowed or path in paths or "revision" not in layer:
                self.api.die("配置层重复或超出当前调用者的标准 dotenv 目录；需要业务专用配置写入实现")
            if layer.get("error"):
                self.guidance("source_unreadable", path=str(path), message="先修复读取错误；未改变保存位置")
            if revision(path) != layer["revision"]:
                self.guidance("inspection_stale", path=str(path), message="本机配置已变化；重新运行业务配置检查")
            paths.append(path)
            layers.append((str(path), self.api._parse_env_file(path)))
        actual, sources = {}, {}
        for key, spec in keys.items():
            value = os.environ.get(key, "")
            env_revision = hashlib.sha256(value.encode()).hexdigest() if value.strip() else None
            if env_revision != report["environment"][key]:
                self.guidance("inspection_stale", key=key, message="进程环境已变化；在相同启动环境重新检查")
            if value.strip():
                actual[key], sources[key] = value, "environment"
            else:
                for path, values in layers:
                    if values.get(key, "").strip():
                        actual[key], sources[key] = values[key], path
                        break
                else:
                    if "default" in spec:
                        actual[key], sources[key] = spec["default"], "built-in default"
                    else:
                        sources[key] = "missing"
            field = report["fields"][key]
            if (not isinstance(field, dict) or field.get("source") != sources[key]
                    or field.get("present") != bool(actual.get(key))):
                self.guidance("inspection_stale", key=key, message="业务报告与实际配置来源不一致；未写文件")
        return report, actual, sources

    def mapping(self):
        mapping = {key: key for key in self.contract["keys"]}
        seen = set()
        for raw in self.args.map or []:
            parts = raw.split("=", 1)
            if (len(parts) != 2 or parts[0] not in mapping or parts[0] in seen
                    or not self.api._PAYLOAD_KEY.fullmatch(parts[1])):
                self.api.die("--map 必须是声明中的 TARGET=SOURCE，同一目标只能指定一次")
            mapping[parts[0]] = parts[1]
            seen.add(parts[0])
        return mapping

    def records(self, snapshot, ids, mapping):
        self.args._config_snapshot = snapshot
        backend = self.api.require_backend(self.args)
        records, pairs = [], {}
        for sid in ids:
            matches = backend.find("id", sid, with_secret=True)
            if len(matches) != 1:
                self.guidance("record_unavailable", record_id=sid, message="记录不存在、不可见或 ID 不唯一；请重新选择")
            record = matches[0]
            records.append({key: record.get(key, "") for key in ("id", "name", "service", "account")})
            for key, value in self.api.parse_payload(record["secret"]).items():
                if key not in mapping.values():
                    continue
                if key in pairs:
                    self.guidance("duplicate_key", key=key, message="多条记录含同名 key；请明确来源")
                pairs[key] = value
        values, rows, missing = {}, [], []
        for key, spec in self.contract["keys"].items():
            source = mapping[key]
            present = bool(pairs.get(source, "").strip())
            default = not present and "default" in spec
            if present:
                values[key] = pairs[source]
            elif default:
                values[key] = spec["default"]
            elif spec["required"] or key in self.args.key:
                missing.append(key)
            rows.append({"key": key, "source_key": source, "required": spec["required"],
                         "present": present, "uses_default": default, "description": spec["description"]})
        if missing:
            self.guidance("missing_keys", missing_keys=missing, records=records, keys=rows,
                          message="请在自己的令牌表中填写，或选择自行修改本机文件；不要把值发到聊天")
        for value in values.values():
            if any(ch in value for ch in "\x00\r\n\u0085\u2028\u2029"):
                self.api.die("目标 dotenv 只支持单行配置；未写入任何文件")
        return values, {"records": records, "keys": rows}

    def check_target(self, path):
        """Validate both a symlink's spelling and its destination; never change repo ignore rules."""
        for candidate in dict.fromkeys([path, path.resolve()]):
            parent = candidate.parent
            while not parent.exists():
                parent = parent.parent
            if not parent.is_dir() or not os.access(parent, os.W_OK):
                self.guidance("target_not_writable", path=str(path))
            if candidate.exists() and (not candidate.is_file() or not os.access(candidate, os.W_OK)):
                self.guidance("target_not_writable", path=str(path))
            def git(*args, directory=parent):
                try:
                    return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    self.guidance("git_check_failed", path=str(path))
            root = git("rev-parse", "--show-toplevel")
            if root.returncode:
                if b"not a git repository" not in root.stderr:
                    self.guidance("git_check_failed", path=str(path))
                continue
            repo = Path(os.fsdecode(root.stdout).strip())
            relative = str((candidate.parent.resolve() / candidate.name).relative_to(repo.resolve()))
            tracked = git("ls-files", "--error-unmatch", "--", relative, directory=repo)
            ignored = git("check-ignore", "--quiet", "--", relative, directory=repo)
            if tracked.returncode != 1 or ignored.returncode != 0:
                self.guidance("unsafe_target", path=str(path), tracked=tracked.returncode == 0,
                              ignored=ignored.returncode == 0,
                              message="目标必须未被 Git 跟踪且已被忽略；未改索引、忽略规则或保存位置")

    def target(self, key, report, sources):
        if self.args.skill_only and (self.consumer["kind"] != "plugin" or report["skill"] is None):
            self.api.die("--skill-only 只适用于有真实调用 Skill 的 Plugin 报告；共享检查不能借用 Skill 身份")
        source = sources[key]
        if source == "environment":
            self.guidance("environment_source", key=key, message="先找到启动/注入环境变量的实际来源；写全局文件无法替换它")
        if source not in ("missing", "built-in default"):
            return Path(source)
        scope = self.args.scope or "global"
        if scope == "project":
            return Path.cwd() / ".env.local"
        if not report["global_enabled"]:
            self.guidance("global_disabled", key=key, message="调用者未启用全局读取；明确启用或选择项目范围后重新检查")
        root = Path.home() / ".config" / self.consumer["name"]
        if self.args.skill_only:
            return root / f".env.{report['skill']}"
        return root / ".env"

    def configure(self):
        from agent_rules import AGENTS, inspect_agent
        if self.args.agent not in AGENTS:
            self.api.die("未知 Agent；请按实际调用上下文选择 agent-rule 支持的宿主")
        agent_report = inspect_agent(self.args.agent, cwd=Path.cwd(), config_dir=self.args.agent_config_dir,
                                     workspace=self.args.agent_workspace, agent_id=self.args.agent_id)
        report, actual, sources = self.inspection()
        selected = self.args.key
        if len(selected) != len(set(selected)) or any(key not in self.contract["keys"] for key in selected):
            self.api.die("--key 必须是声明中要新增或修复的字段，且不能重复")
        targets = {key: self.target(key, report, sources) for key in selected}
        for key, target in targets.items():
            if str(target) not in {layer["path"] for layer in report["layers"]}:
                self.guidance("target_not_read", key=key, path=str(target),
                              message="调用者未声明读取目标文件；未写入配置")
        for target in targets.values():
            self.check_target(target)
        ids = self.args.id
        if len(ids) != len(set(ids)):
            self.api.die("--id 不能重复")
        mapping = self.mapping()
        snapshot = self.api._snapshot_for_args(self.args)
        if snapshot.ids:
            self.api.die("旧 SECRET_BOOK_IDS 不能代替本次明确选择的记录")
        values, selection = self.records(snapshot, ids, mapping)
        required_missing = [k for k, spec in self.contract["keys"].items()
                            if spec["required"] and not actual.get(k) and k not in selected]
        if required_missing:
            self.guidance("required_fields_not_selected", keys=required_missing)
        for group in self.contract.get("groups", []):
            if set(group).intersection(selected):
                mismatches = [k for k in group if k not in selected and values.get(k) != actual.get(k)]
                if mismatches:
                    self.guidance("related_fields_differ", keys=mismatches,
                                  sources={k: sources[k] for k in mismatches},
                                  message="同组字段与表中所选配置不同；核对后将需替换字段一并加入 --key，不能静默覆盖")
        updates = {}
        changes = []
        for key, target in targets.items():
            resolved = target.resolve()
            updates.setdefault(resolved, {})[key] = values[key]
            changes.append({"key": key, "source": sources[key], "path": str(target), "real_path": str(resolved),
                            "operation": "unchanged" if actual.get(key) == values[key] else
                                ("add" if sources[key] in ("missing", "built-in default") else "replace")})
        review = {"consumer": self.consumer, "skill": report["skill"], "cwd": report["cwd"],
            "source": {"config_id": snapshot.config_id, "config_name": snapshot.config_name,
                       "layer": snapshot.source, "app_token": snapshot.app_token, "table_id": snapshot.table_id,
                       "lark_profile": snapshot.lark_profile, "feishu_app_id": snapshot.feishu_app_id,
                       "feishu_user_open_id": snapshot.feishu_user_open_id},
            **selection, "changes": changes, "agent_rules": agent_report}
        revisions = {str(p): revision(p) for p in updates}
        request_digest = digest({"contract": self.contract, "inspection": report, "review": review,
                                 "values": values, "files": revisions})
        with self.api._config_file_lock(self.path):
            store = self.read_store()
            pending = store["pending"].get(self.args.confirm)
            if not pending or pending["digest"] != request_digest:
                token = secrets.token_hex(24)
                while len(store["pending"]) >= 32:
                    del store["pending"][next(iter(store["pending"]))]
                store["pending"][token] = {"digest": request_digest, "review": review,
                    "arguments": {key: getattr(self.args, key) for key in (
                        "requirements", "inspection", "scope", "skill_only", "id", "map", "key",
                        "agent", "agent_config_dir", "agent_workspace", "agent_id", "use_global_config", "config_name")}}
                self.write_store(store)
                self.guidance("confirmation_required", confirmation_token=token,
                              previous_confirmation_invalid=bool(self.args.confirm), review=review,
                              message="展示记录/账号、表中 key→业务字段、目标文件与替换项；用户确认后追加 --confirm")
            # Re-read caller inputs under the writer lock. User edits and changed overrides invalidate confirmation.
            self.inspection()
            if any(str(targets[row["key"]].resolve()) != row["real_path"] for row in changes):
                self.guidance("inspection_stale", message="配置文件链接目标已改变；请重新检查")
            store["pending"].pop(self.args.confirm)
            operation = {"consumer": self.consumer, "review": review, "status": "writing", "written": []}
            store["history"] = (store["history"] + [operation])[-32:]
            self.write_store(store)
            written = []
            try:
                for path, fields in updates.items():
                    self.check_target(path)
                    if revision(path) != revisions[str(path)]:
                        self.guidance("inspection_stale", path=str(path), message="写入前文件发生变化")
                    # No chmod on an existing project or home directory.
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    self.api._write_env_updates(path, fields)
                    if any(self.api._parse_env_file(path).get(key) != value for key, value in fields.items()):
                        raise OSError("写入后字段回读不一致")
                    written.append({"path": str(path), "keys": list(fields)})
            except (OSError, self.api.LocalWriteResultUnknown, self.api.ProfileGuidance) as exc:
                operation.update(status="write_incomplete", written=written)
                try:
                    self.write_store(store)
                except OSError:
                    pass  # The durable 'writing' record still requires readback after interruption.
                self.guidance("write_incomplete", written=written, needs_readback=[str(p) for p in updates],
                              failed_path=str(path), cause=exc.payload if isinstance(exc, self.api.ProfileGuidance) else None,
                              message="写入未全部验证；先回读各目标文件，不能换位置或盲目重放", error_type=type(exc).__name__)
            result = {"schema": "secret-book.consumer-write/v1", "status": "written", "consumer": self.consumer,
                      "written": written, "verification": "file_readback_only", "agent_rules": agent_report,
                      "next_step": "直接运行调用者的配置检查，报告实际生效来源；鉴权需单独验证，不能自动重发业务请求",
                      "project_option": "若要改为项目范围，让 Agent 检查项目 .env.local 的忽略规则和高优先级覆盖，再确认迁移；不会自动复制"}
            operation.update(status="written", written=written)
            self.write_store(store)
            print(json.dumps(result, ensure_ascii=False))


def dispatch(api, args):
    config = Configuration(api, args)
    if args.action == "configure-status":
        config.show()
    else:
        config.configure()
