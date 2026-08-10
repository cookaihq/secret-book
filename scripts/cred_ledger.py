#!/usr/bin/env python3
"""cred-ledger — 凭证台账 CLI（飞书多维表格后端）。

明文台账 + agent 取用通道：本脚本负责查表、注入、掩码；不做加密。
安全声明见仓库 README 与 SKILL.md 第一屏。

架构约束（design.md §6）：所有表格 CRUD 必须走 Backend 抽象；业务逻辑不得
直接拼 lark-cli 命令。Notion 后端（v1.1）只需新增一个 Backend 实现。

掩码纪律：凭证值只流向子进程环境（run）或剪贴板（copy），不打印、不进
错误消息；本进程输出中的凭证值一律经 _scrub() 掩码。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import string
import subprocess
import sys
from pathlib import Path

SKILL_NAME = "cred-ledger"
ENV_APP_TOKEN = "CRED_LEDGER_APP_TOKEN"
ENV_TABLE_ID = "CRED_LEDGER_TABLE_ID"
ENV_IDS = "CRED_LEDGER_IDS"

FIELD_SCHEMA = [
    {"name": "id", "type": "text"},
    {"name": "name", "type": "text"},
    {"name": "service", "type": "text"},
    {"name": "account", "type": "text"},
    {"name": "purpose", "type": "text"},
    {"name": "secret", "type": "text"},
    {"name": "expires_at", "type": "datetime"},
    {"name": "notes", "type": "text"},
]
META_FIELDS = ["id", "name", "service", "account", "purpose", "expires_at"]

_SENSITIVE: list[str] = []  # 运行期收集到的凭证值，用于掩码任何将要打印的文本


# ---------- 通用工具 ----------

def die(msg: str, code: int = 1) -> "NoReturn":  # noqa: F821
    print(f"[{SKILL_NAME}] error: {_scrub(msg)}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    # flush：run 动作随后 execvpe 替换进程，不 flush 会丢缓冲中的输出
    print(f"[{SKILL_NAME}] {_scrub(msg)}", flush=True)


def _scrub(text: str) -> str:
    for v in _SENSITIVE:
        # 短值不掩码：低熵且会误伤正常文本中的子串（如值 "1" 命中 "1 个键"）
        if v and len(v) >= 6 and v in text:
            text = text.replace(v, _mask(v))
    return text


def _mask(v: str) -> str:
    return f"{v[:4]}****{v[-4:]}" if len(v) > 12 else "****"


def gen_id() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "sec_" + "".join(secrets.choice(alphabet) for _ in range(10))


# ---------- 配置分层（ADR 0003）----------

_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def _parse_env_file(path: Path) -> dict:
    """ADR 0003 极简解析：KEY=value、可选成对引号、# 注释行、空行、同名取最后。"""
    result: dict = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        result[key] = val
    return result


def load_config(use_global: bool) -> dict:
    """每个变量独立 first-found-wins：进程环境 → $PWD/.env.local → $PWD/.env
    → （仅 --use-global-config 时）~/.config/cred-ledger/.env。"""
    layers = [
        {k: v for k, v in os.environ.items() if k.startswith("CRED_LEDGER_")},
        _parse_env_file(Path.cwd() / ".env.local"),
        _parse_env_file(Path.cwd() / ".env"),
    ]
    if use_global:
        layers.append(_parse_env_file(global_config_path()))
    merged: dict = {}
    for key in (ENV_APP_TOKEN, ENV_TABLE_ID, ENV_IDS):
        for layer in layers:
            if layer.get(key):
                merged[key] = layer[key]
                break
    return merged


def global_config_path() -> Path:
    return Path.home() / ".config" / SKILL_NAME / ".env"


def require_backend(args) -> "FeishuBackend":
    cfg = load_config(args.use_global_config)
    app_token, table_id = cfg.get(ENV_APP_TOKEN), cfg.get(ENV_TABLE_ID)
    if not app_token or not table_id:
        hint = "" if args.use_global_config else "（未启用全局配置；若已 init，请加 --use-global-config）"
        die(f"缺少 {ENV_APP_TOKEN} / {ENV_TABLE_ID} 配置{hint}。先运行 init-create 或 init-adopt。")
    return FeishuBackend(app_token, table_id)


# ---------- payload（secret 列的 dotenv）----------

_PAYLOAD_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_payload(text: str) -> dict:
    """design.md §3.2：值 = 首个 = 之后的原文，不去引号、不转义、单行。
    与配置 .env 解析是两套规则，不得合并。"""
    pairs: dict = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        if "=" not in raw:
            die(f"payload 第 {lineno} 行不是 KEY=VALUE 形式")
        key, val = raw.split("=", 1)
        key = key.strip()
        if not _PAYLOAD_KEY.match(key):
            die(f"payload 第 {lineno} 行键名不合法（需匹配 [A-Za-z_][A-Za-z0-9_]*）")
        if not val:
            die(f"payload 第 {lineno} 行值为空")
        pairs[key] = val
        _SENSITIVE.append(val)
    if not pairs:
        die("payload 为空：stdin 至少要有一行 KEY=VALUE")
    return pairs


# ---------- 后端适配器 ----------

class FeishuBackend:
    """飞书多维表格后端，经 lark-cli。所有方法只吞吐 {字段名: 字符串} 平面记录。"""

    def __init__(self, app_token: str, table_id: str):
        self.app_token = app_token
        self.table_id = table_id

    def _run(self, shortcut: str, extra: list, risk_write: bool = False) -> dict:
        cmd = ["lark-cli", "base", shortcut, "--as", "user", "--format", "json",
               "--base-token", self.app_token, "--table-id", self.table_id, *extra]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            die(f"lark-cli {shortcut} 失败 (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
        try:
            return json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            die(f"lark-cli {shortcut} 返回非 JSON 输出（前 200 字符）: {proc.stdout[:200]}")

    @staticmethod
    def _rows(payload: dict) -> tuple[list, bool]:
        """+record-list 是列式返回：data.data 行数组 + data.fields 列名 +
        record_id_list 对齐 +（实测 lark-cli 1.0.82，2026-08-10）。"""
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        rows = data.get("data") or []
        names = data.get("fields") or []
        rec_ids = data.get("record_id_list") or []
        out = []
        for i, row in enumerate(rows):
            fields = dict(zip(names, row))
            fields["_record_id"] = rec_ids[i] if i < len(rec_ids) else ""
            out.append(fields)
        return out, bool(data.get("has_more"))

    @staticmethod
    def _cell_str(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):  # datetime 毫秒时间戳等
            return str(value)
        if isinstance(value, list):  # 文本段数组 [{"text": ...}]
            return "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in value)
        if isinstance(value, dict):
            return str(value.get("text", value))
        return str(value)

    def _flatten(self, fields: dict) -> dict:
        rec = {name: self._cell_str(fields.get(name)) for name in
               ("id", "name", "service", "account", "purpose", "secret", "notes")}
        rec["expires_at"] = _fmt_expires(fields.get("expires_at"))
        rec["_record_id"] = fields.get("_record_id", "")
        return rec

    def list_records(self, filter_json: str | None = None, with_secret: bool = False) -> list:
        projection = []
        for f in META_FIELDS + (["secret"] if with_secret else []):
            projection += ["--field-id", f]
        records, offset = [], 0
        while True:
            extra = ["--limit", "200", "--offset", str(offset), *projection]
            if filter_json:
                extra += ["--filter-json", filter_json]
            rows, has_more = self._rows(self._run("+record-list", extra))
            records += [self._flatten(r) for r in rows]
            if not has_more:
                return records
            offset += 200

    def find(self, by: str, value: str, with_secret: bool) -> list:
        cond = json.dumps({"logic": "and", "conditions": [[by, "==", value]]}, ensure_ascii=False)
        return self.list_records(filter_json=cond, with_secret=with_secret)

    def create_record(self, fields: dict) -> None:
        body = json.dumps({"create_records": [fields]}, ensure_ascii=False)
        self._run("+record-batch-create", ["--json", body], risk_write=True)

    def update_record(self, record_id: str, fields: dict) -> None:
        body = json.dumps({"update_records": {record_id: fields}}, ensure_ascii=False)
        self._run("+record-batch-update", ["--json", body], risk_write=True)


def _fmt_expires(raw) -> str:
    if raw is None or raw == "":
        return ""
    if isinstance(raw, (int, float)):  # 毫秒时间戳（防御：部分接口返回时间戳）
        import datetime
        return datetime.datetime.fromtimestamp(raw / 1000).date().isoformat()
    # 实测返回 "2026-12-31 00:00:00" 格式化字符串；日期粒度展示去掉零点时间
    return FeishuBackend._cell_str(raw).removesuffix(" 00:00:00")


# ---------- 记录解析与选择 ----------

def backfill_ids(backend: FeishuBackend, records: list) -> None:
    """手工粘贴进表格的行没有 id：遇到即补写，幂等静默（design.md §3.3）。"""
    for rec in records:
        if not rec["id"] and rec["_record_id"]:
            rec["id"] = gen_id()
            backend.update_record(rec["_record_id"], {"id": rec["id"]})


def resolve_records(backend: FeishuBackend, args, need_secret: bool) -> list:
    """按 --id / --name / CRED_LEDGER_IDS 的顺序解析目标记录。"""
    ids: list = list(getattr(args, "id", None) or [])
    name = getattr(args, "name", None)
    if not ids and not name:
        cfg = load_config(args.use_global_config)
        raw = cfg.get(ENV_IDS, "")
        ids = [x for x in re.split(r"[,\s]+", raw) if x]
        if not ids:
            die(f"未指定 --id/--name，且 {ENV_IDS} 未配置（项目绑定见 SKILL.md）")
    out = []
    if name:
        matches = backend.find("name", name, with_secret=need_secret)
        if not matches:
            die(f"找不到 name={name} 的记录")
        if len(matches) > 1:
            die(f"name={name} 命中 {len(matches)} 条记录（别名重复），请改用 --id 精确指定")
        backfill_ids(backend, matches)
        out += matches
    for sid in ids:
        matches = backend.find("id", sid, with_secret=need_secret)
        if not matches:
            die(f"找不到 id={sid} 的记录")
        out += matches
    for rec in out:
        if rec.get("secret"):
            for v in parse_payload(rec["secret"]).values():
                pass  # parse_payload 已把值登记进 _SENSITIVE
    return out


def merged_env_pairs(records: list) -> dict:
    """多记录键值对合并注入；键名冲突报错退出，不静默覆盖（design.md §4.1）。"""
    merged, owner = {}, {}
    for rec in records:
        for key, val in parse_payload(rec["secret"]).items():
            if key in merged:
                die(f"键名冲突：{key} 同时来自记录 {owner[key]} 与 {rec['name'] or rec['id']}")
            merged[key], owner[key] = val, rec["name"] or rec["id"]
    return merged


# ---------- 动作 ----------

def cmd_save(args) -> None:
    payload_text = sys.stdin.read()
    pairs = parse_payload(payload_text)
    backend = require_backend(args)
    if backend.find("name", args.name, with_secret=False):
        die(f"name={args.name} 已存在；换一个别名，或直接在表格里编辑该记录")
    fields = {
        "id": gen_id(),
        "name": args.name,
        "service": args.service,
        "purpose": args.purpose,
        "secret": payload_text.strip(),
    }
    if args.account:
        fields["account"] = args.account
    if args.expires_at:
        exp = args.expires_at
        fields["expires_at"] = f"{exp} 00:00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", exp) else exp
    if args.notes:
        fields["notes"] = args.notes
    backend.create_record(fields)
    info(f"已保存 {args.name} (id={fields['id']}，{len(pairs)} 个键：{', '.join(pairs)})")


def cmd_list(args) -> None:
    backend = require_backend(args)
    records = backend.list_records()
    backfill_ids(backend, records)
    if not records:
        info("台账为空")
        return
    header = META_FIELDS
    widths = [max(len(h), *(len(r[h]) for r in records)) for h in header]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for r in records:
        print("  ".join(r[h].ljust(w) for h, w in zip(header, widths)))


def cmd_get(args) -> None:
    backend = require_backend(args)
    rec = resolve_records(backend, args, need_secret=True)[0]
    for key in META_FIELDS + ["notes"]:
        print(f"{key}: {rec.get(key, '')}")
    print(f"keys: {', '.join(parse_payload(rec['secret']))}")


def cmd_run(args) -> None:
    if not args.command:
        die("run 需要 '-- <命令>'")
    backend = require_backend(args)
    records = resolve_records(backend, args, need_secret=True)
    pairs = merged_env_pairs(records)
    env = dict(os.environ)
    env.update(pairs)
    info(f"注入 {len(pairs)} 个变量（{', '.join(pairs)}）后执行：{' '.join(args.command)}")
    try:
        os.execvpe(args.command[0], args.command, env)
    except FileNotFoundError:
        die(f"命令不存在：{args.command[0]}")


def cmd_copy(args) -> None:
    backend = require_backend(args)
    rec = resolve_records(backend, args, need_secret=True)[0]
    pairs = parse_payload(rec["secret"])
    if args.key:
        if args.key not in pairs:
            die(f"记录 {rec['name']} 没有键 {args.key}（可用：{', '.join(pairs)}）")
        value = pairs[args.key]
    elif len(pairs) == 1:
        value = next(iter(pairs.values()))
    else:
        die(f"记录 {rec['name']} 有多个键（{', '.join(pairs)}），必须用 --key 指定")
    for tool in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        try:
            subprocess.run(tool, input=value, text=True, check=True)
            info(f"已复制到剪贴板：{args.key or next(iter(pairs))} = {_mask(value)}")
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    die("无可用剪贴板工具（pbcopy/wl-copy/xclip）。请在你自己的终端运行本命令，禁止让 agent 明文打印")


def cmd_init_create(args) -> None:
    cmd = ["lark-cli", "base", "+base-create", "--as", "user", "--format", "json",
           "--name", args.base_name, "--table-name", "credentials",
           "--fields", json.dumps(FIELD_SCHEMA, ensure_ascii=False)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"+base-create 失败 (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    print(proc.stdout)
    info("请从上方返回中提取 base token（app_token）与 credentials 表的 table_id，"
         "与用户确认后运行 config-write 写入全局配置")


def cmd_init_adopt(args) -> None:
    proc = subprocess.run(["lark-cli", "base", "+url-resolve", "--as", "user",
                           "--format", "json", "--url", args.url],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"+url-resolve 失败 (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    data = json.loads(proc.stdout)
    inner = data.get("data", data)
    app_token = inner.get("base_token") or inner.get("app_token")
    table_id = inner.get("table_id")
    if not app_token or not table_id:
        die(f"无法从 URL 解析 base_token/table_id，+url-resolve 返回：{json.dumps(inner, ensure_ascii=False)[:300]}")
    backend = FeishuBackend(app_token, table_id)
    listed = backend._run("+field-list", ["--limit", "200"])
    # +field-list 返回 data.fields = [{name, type, ...}]（实测 lark-cli 1.0.82）
    existing = {str(it.get("name", "")): str(it.get("type", ""))
                for it in listed.get("data", {}).get("fields", [])}
    for spec in FIELD_SCHEMA:
        name, want = spec["name"], spec["type"]
        if name not in existing:
            backend._run("+field-create", ["--json", json.dumps(spec, ensure_ascii=False)], risk_write=True)
            info(f"已补建缺失字段 {name} ({want})")
        elif str(existing[name]) not in (want, ""):
            die(f"字段 {name} 类型不符：表内为 {existing[name]}，需要 {want}。拒绝接管脏表（design.md §5），请修正后重试")
    info(f"字段校验通过。与用户确认后运行 config-write --app-token {app_token} --table-id {table_id}")


def cmd_config_write(args) -> None:
    content = f"{ENV_APP_TOKEN}={args.app_token}\n{ENV_TABLE_ID}={args.table_id}\n"
    if args.project:
        target = Path.cwd() / ".env.local"
        _assert_untracked_ignored(target)
    else:
        target = global_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.parent.chmod(0o700)
    existing = _parse_env_file(target)
    existing[ENV_APP_TOKEN] = args.app_token
    existing[ENV_TABLE_ID] = args.table_id
    kept = [f"{k}={v}" for k, v in existing.items()]
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
    target.chmod(0o600)
    info(f"配置已写入 {target}")


def _assert_untracked_ignored(target: Path) -> None:
    """CLAUDE.md 凭证写入约定：git 工作树内写入前必须证明目标未被跟踪且实际被忽略。"""
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", target.name],
                             capture_output=True, text=True)
    if tracked.returncode == 0:
        die(f"{target.name} 已被 git 跟踪，拒绝写入配置")
    ignored = subprocess.run(["git", "check-ignore", "-q", target.name],
                             capture_output=True, text=True)
    if ignored.returncode != 0:
        die(f"{target.name} 未被 .gitignore 忽略，拒绝写入配置。先把它加入 .gitignore")


# ---------- CLI ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=SKILL_NAME, description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="action", required=True)

    def common(sp, with_lookup: bool = False):
        sp.add_argument("--use-global-config", action="store_true",
                        help=f"启用第 4 层配置 {global_config_path()}（ADR 0003 要求显式 flag）")
        if with_lookup:
            sp.add_argument("--name", help="按人类别名定位记录")
            sp.add_argument("--id", action="append", help="按机器键 sec_xxx 定位记录，可重复")

    sp = sub.add_parser("save", help="从 stdin 读 dotenv payload 保存新凭证组")
    sp.add_argument("--name", required=True)
    sp.add_argument("--service", required=True)
    sp.add_argument("--purpose", required=True)
    sp.add_argument("--account")
    sp.add_argument("--expires-at", dest="expires_at", help="YYYY-MM-DD")
    sp.add_argument("--notes")
    common(sp)
    sp.set_defaults(func=cmd_save)

    sp = sub.add_parser("list", help="列台账（仅元数据，不含 secret/notes）")
    common(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("get", help="查单条元数据与键名（不回显值）")
    common(sp, with_lookup=True)
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("run", help="键值对注入子进程环境后执行命令")
    common(sp, with_lookup=True)
    sp.add_argument("command", nargs=argparse.REMAINDER,
                    help="'-- <命令及参数>'")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("copy", help="单个值写入剪贴板（多键记录需 --key）")
    common(sp, with_lookup=True)
    sp.add_argument("--key")
    sp.set_defaults(func=cmd_copy)

    sp = sub.add_parser("init-create", help="新建台账 Base（credentials 表 + 8 字段）")
    sp.add_argument("--base-name", default="凭证台账")
    sp.set_defaults(func=cmd_init_create)

    sp = sub.add_parser("init-adopt", help="接管用户自备表：校验字段，缺列补建，类型不符报错")
    sp.add_argument("--url", required=True)
    sp.set_defaults(func=cmd_init_adopt)

    sp = sub.add_parser("config-write", help="写入 app_token/table_id 配置（默认全局，--project 写 $PWD/.env.local）")
    sp.add_argument("--app-token", required=True)
    sp.add_argument("--table-id", required=True)
    sp.add_argument("--project", action="store_true")
    sp.set_defaults(func=cmd_config_write)

    return p


def main() -> None:
    args = build_parser().parse_args()
    if getattr(args, "command", None) and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    args.func(args)


if __name__ == "__main__":
    main()
