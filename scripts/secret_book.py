#!/usr/bin/env python3
"""secret-book — 令牌管理 CLI（飞书多维表格后端）。

令牌表 + agent 取用通道：本脚本负责查表、注入、掩码；不做加密。
安全声明见仓库 README 与 SKILL.md 第一屏。

架构约束：所有表格 CRUD 必须走 Backend 抽象；每条业务命令只解析一次完整的
令牌配置快照，身份校验通过后才能访问对应的飞书 Base。

掩码纪律：令牌值只流向子进程环境（run）或剪贴板（copy），不打印、不进
错误消息；本进程输出中的令牌值一律经 _scrub() 掩码。

自动绑定与多 agent 兜底规则的设计记录：外层仓
docs/deliverables/secret-book/agent-rule-and-bindings.md。
"""

from __future__ import annotations

# ---------- 运行时环境 bootstrap（ADR 0007 §1.4）----------
# 作用：把「用哪个解释器跑」从调用方每轮的记忆变成结构性事实——不在
# <仓根>/.venv 里就 os.execv 拉回去，venv 缺失就按 uv.lock 自动重建。
# **只用 Python 3.9 兼容语法与标准库**：本段会先被系统 python3（本机 3.9.6）
# 执行，用了新语法会在 SyntaxError 阶段就死掉，兜底反而成了故障点。

import os
import shlex
import subprocess
import sys

# 一次性再入护栏：exec 之后仍不在目标 venv，说明 venv 目录本身坏了。没有这个
# 标记会无限 execv 且零输出。标记值存的是**本轮目标 venv 的 realpath**，不是
# 布尔——变量被外部环境 export 时值不匹配就不算本轮再入，仍照常自动重建。
_BOOTSTRAP_REEXEC_ENV = "SECRET_BOOK_BOOTSTRAP_REEXEC"

# 本脚本在 <仓根>/scripts/ 下，项目根（pyproject.toml 所在）是上一级。
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 重定位只认 uv 原生的 UV_PROJECT_ENVIRONMENT，且基准必须是**项目根**——uv 0.8
# 就是这么解析相对值的；按 CWD 解析会在用户项目目录里设了相对值时把进程 exec
# 进用户项目的 venv。绝对值不受影响：os.path.join 遇到绝对路径直接返回它。
_VENV_DIR = os.path.join(_SKILL_DIR, os.environ.get("UV_PROJECT_ENVIRONMENT") or ".venv")
_VENV_PY = os.path.join(_VENV_DIR, "bin", "python")


def _bootstrap_fail(msg):
    sys.stderr.write(msg + "\n")
    raise SystemExit(1)


def _bootstrap_manual_hint():
    # 必须 shell 引用：路径含空格时，未引用的 `rm -rf /tmp/sp ace/.venv` 被照抄
    # 执行会删掉两个无关路径。
    # --no-dev：重建的是**运行**环境，不该拉进测试依赖（ADR 0007 §1.2 补充 2）。
    # 本 skill 的 pyproject 有 dev 组；--no-dev 保证自动重建不安装 pytest。
    return "rm -rf %s && uv sync --project %s --no-dev" % (
        shlex.quote(_VENV_DIR), shlex.quote(_SKILL_DIR)
    )


def _bootstrap_venv_is_valid():
    # 只判 bin/python 存在是不够的：sync 中断、手工同名目录、残留软链都会让
    # 解释器存在而目录不是 venv，此时跳过修复直接 execv 就会无限重启。
    return (os.path.exists(_VENV_PY)
            and os.path.exists(os.path.join(_VENV_DIR, "pyvenv.cfg")))


def _bootstrap_require_uv():
    """uv 是系统级程序，缺失/版本过低只报错给命令，不擅自安装（ADR 0007 §4.2）。"""
    try:
        probe = subprocess.run(["uv", "--version"], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=30)
    except (OSError, subprocess.SubprocessError):
        _bootstrap_fail("uv 未安装。请执行：curl -LsSf https://astral.sh/uv/install.sh | sh")
    parts = probe.stdout.decode("utf-8", "replace").split()  # 形如 "uv 0.8.11 (...)"
    found = parts[1] if len(parts) > 1 else "0"
    try:
        numeric = tuple(int(x) for x in (found.split(".") + ["0", "0"])[:2])
    except ValueError:
        numeric = (0, 0)  # uv 报错时 stdout 是 "error: ..."，硬 int() 会抛未捕获异常
    if numeric < (0, 8):
        _bootstrap_fail("uv 版本过低（需 >= 0.8，当前 %s）。请执行：uv self update" % found)


def _bootstrap_ensure():
    target = os.path.realpath(_VENV_DIR)
    if os.path.realpath(sys.prefix) == target:
        # 已到位：清掉本轮标记，别让 run 派生的子进程继承后误判。
        if os.environ.get(_BOOTSTRAP_REEXEC_ENV) == target:
            os.environ.pop(_BOOTSTRAP_REEXEC_ENV, None)
        return
    if os.environ.get(_BOOTSTRAP_REEXEC_ENV) == target:  # 只认「值等于本轮目标」
        _bootstrap_fail("运行环境异常：已重启到 %s 但解释器仍不在该 venv 内，目录疑似损坏。\n"
                        "请手工重建：%s" % (_VENV_DIR, _bootstrap_manual_hint()))
    if not _bootstrap_venv_is_valid():
        _bootstrap_require_uv()
        sys.stderr.write("[bootstrap] 运行环境缺失，正在按 uv.lock 重建 %s ...\n" % _VENV_DIR)
        try:
            sync = subprocess.run(["uv", "sync", "--project", _SKILL_DIR, "--no-dev"],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=600)  # 网络调用必设总预算（ADR 0006）
        except subprocess.TimeoutExpired:
            _bootstrap_fail("uv sync 超过 600 秒未完成，疑似网络异常。请手工执行："
                            + _bootstrap_manual_hint())
        if sync.returncode != 0 or not _bootstrap_venv_is_valid():
            _bootstrap_fail("uv sync 失败，无法重建运行环境（请手工执行：%s）：\n%s"
                            % (_bootstrap_manual_hint(),
                               sync.stdout.decode("utf-8", "replace")))
    os.environ[_BOOTSTRAP_REEXEC_ENV] = target  # putenv，execv 后的进程读得到
    os.execv(_VENV_PY, [_VENV_PY] + sys.argv)   # 拉回目标解释器重启自身


_bootstrap_ensure()

# ---------- bootstrap 结束，以下为业务代码 ----------

import argparse
import contextlib
import datetime
import dataclasses
import fcntl
import hashlib
import json
import re
import secrets
import string
import tempfile
import time
from pathlib import Path

SKILL_NAME = "secret-book"
ENV_APP_TOKEN = "SECRET_BOOK_APP_TOKEN"
ENV_TABLE_ID = "SECRET_BOOK_TABLE_ID"
ENV_IDS = "SECRET_BOOK_IDS"
ENV_LARK_PROFILE = "SECRET_BOOK_LARK_PROFILE"
ENV_FEISHU_APP_ID = "SECRET_BOOK_FEISHU_APP_ID"
ENV_FEISHU_USER_OPEN_ID = "SECRET_BOOK_FEISHU_USER_OPEN_ID"
ENV_CONFIGS_JSON = "SECRET_BOOK_CONFIGS_JSON"

GLOBAL_CONFIG_SCHEMA = 1
CONFIG_IDENTITY_CONFIRMATION_SCHEMA = "secret-book.config-identity-confirmation/v1"
PROFILE_GUIDANCE_SCHEMA = "secret-book.profile-guidance/v1"
EXIT_GUIDANCE = 3
RESOURCE_ENV_KEYS = (
    ENV_APP_TOKEN,
    ENV_TABLE_ID,
    ENV_LARK_PROFILE,
    ENV_FEISHU_APP_ID,
    ENV_FEISHU_USER_OPEN_ID,
)

FIELD_SCHEMA = [
    {"name": "id", "type": "text"},
    {"name": "name", "type": "text"},
    {"name": "service", "type": "text"},
    {"name": "account", "type": "text"},
    {"name": "purpose", "type": "text"},
    {"name": "secret", "type": "text"},
    {"name": "expires_at", "type": "datetime"},
    {"name": "notes", "type": "text"},
    {"name": "visible_to", "type": "user", "multiple": True},
]
META_FIELDS = ["id", "name", "service", "account", "purpose", "expires_at"]

_SENSITIVE: list[str] = []  # 运行期收集到的令牌值，用于掩码任何将要打印的文本


# ---------- 通用工具 ----------

class ProfileGuidance(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("reason", "profile guidance required"))
        self.payload = payload


class BindingsFileError(Exception):
    pass


class LocalWriteResultUnknown(OSError):
    """The destination was replaced, but directory durability was not confirmed."""


def _profile_fix_actions(error_kind: str, profile: str, snapshot=None) -> list:
    if error_kind == "feishu_profile_list_unavailable":
        return [{
            "kind": "inspect_lark_profiles",
            "description": "修复 lark-cli 本地配置后重新列出 profile，再重试原命令",
            "argv": ["lark-cli", "profile", "list"],
        }]
    if error_kind == "feishu_identity_values_missing":
        source = getattr(snapshot, "source", None)
        if source == "process_env":
            description = (
                "向用户展示 observed_identity；确认后在下次命令的同一进程环境中同时设置 "
                "SECRET_BOOK_FEISHU_APP_ID 和 SECRET_BOOK_FEISHU_USER_OPEN_ID"
            )
        else:
            description = (
                "向用户展示 observed_identity；确认后把 app_id/open_id 写入 "
                "config_write_target 指定文件中的两个 keys，不得写到其它配置层"
            )
        return [{
            "kind": "confirm_and_write_identity_values",
            "description": description,
        }]
    rebind = []
    if getattr(snapshot, "source", None) == "global_current":
        rebind = [{
            "kind": "rebind_named_config",
            "description": "经用户确认后更新这套令牌配置绑定的 lark-cli profile 和身份固定值",
            "argv_template": [
                "config", "rebind", "--name", snapshot.config_name,
                "--lark-profile", "<profile-name>",
            ],
        }]
    if error_kind == "feishu_profile_not_found":
        return rebind + [
            {
                "kind": "bind_existing_profile",
                "description": "从 candidates 中选择正确的已登录 profile；命名配置使用上面的 config rebind",
            },
            {
                "kind": "login_new_profile",
                "description": "候选都不正确时，为新 profile 完成 device flow 登录后重新保存令牌配置",
                "argv_template": ["lark-cli", "auth", "login", "--profile", "<profile-name>"],
            },
        ]
    if error_kind == "feishu_profile_not_authenticated":
        return [
            {
                "kind": "login_profile",
                "description": "为当前 profile 重新完成 device flow 登录后重试",
                "argv": ["lark-cli", "auth", "login", "--profile", profile],
            },
            {
                "kind": "bind_existing_profile",
                "description": "从 candidates 中选择其它已登录 profile；命名配置使用 config rebind",
            },
        ] + rebind
    actions = rebind + [
        {
            "kind": "login_expected_app_profile",
            "description": "在当前环境为配置固定的应用和用户重新登录这个 profile，然后重试",
            "argv": ["lark-cli", "auth", "login", "--profile", profile],
        }
    ]
    if not rebind:
        location = "同一进程环境" if getattr(snapshot, "source", None) == "process_env" else "同一配置文件"
        actions.append({
            "kind": "rebind_profile_and_identity",
            "description": f"覆盖配置需要经用户确认后，把 profile 和身份固定值更新到{location}",
        })
    return actions


def _guidance_write_target(snapshot, keys=None) -> dict:
    if snapshot is None:
        target = {"source": None, "path": str(global_config_path()), "config_id": None,
                  "config_name": None}
        if keys is not None:
            target["keys"] = keys
        return target
    source_paths = {
        ".env.local": Path.cwd() / ".env.local",
        ".env": Path.cwd() / ".env",
        "global_current": global_config_path(),
    }
    path = source_paths.get(snapshot.source)
    target = {
        "source": snapshot.source,
        "path": str(path) if path else None,
        "config_id": snapshot.config_id or None,
        "config_name": snapshot.config_name or None,
    }
    if keys is not None:
        target["keys"] = keys
    return target


def _raise_profile_guidance(error_kind: str, reason: str, profile: str,
                            candidates: list, *, expected=None, observed=None,
                            snapshot=None, confirmation_token=None,
                            write_keys=None) -> None:
    payload = {
        "schema_version": PROFILE_GUIDANCE_SCHEMA,
        "error_kind": error_kind,
        "reason": reason,
        "configured_profile": profile,
        "config_write_target": _guidance_write_target(snapshot, write_keys),
        "candidates": candidates,
        "fix_actions": _profile_fix_actions(error_kind, profile, snapshot),
        "expected_identity": expected,
        "observed_identity": observed,
    }
    if confirmation_token is not None:
        payload["confirmation_token"] = confirmation_token
    raise ProfileGuidance(payload)

def die(msg: str, code: int = 1) -> "NoReturn":  # noqa: F821
    print(f"[{SKILL_NAME}] error: {_scrub(msg)}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    # flush：run 动作随后 execvpe 替换进程，不 flush 会丢缓冲中的输出
    print(f"[{SKILL_NAME}] {_scrub(msg)}", flush=True)


def warn(msg: str) -> None:
    """诊断信息走 stderr，不污染 list/get 的 stdout 数据流。"""
    print(f"[{SKILL_NAME}] {_scrub(msg)}", file=sys.stderr, flush=True)


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


def global_config_path() -> Path:
    return Path.home() / ".config" / SKILL_NAME / ".env"


def _empty_named_config_store() -> dict:
    return {"schema_version": GLOBAL_CONFIG_SCHEMA, "active_id": None, "configs": {}}


def _load_named_config_store(values: dict, *, allow_missing: bool = True) -> dict:
    raw = values.get(ENV_CONFIGS_JSON, "")
    if not raw:
        if allow_missing:
            return _empty_named_config_store()
        die(f"{ENV_CONFIGS_JSON} 未配置")
    try:
        store = json.loads(raw)
    except json.JSONDecodeError:
        die(f"{ENV_CONFIGS_JSON} 不是合法 JSON；未读取或修改任何令牌配置")
    if (not isinstance(store, dict)
            or type(store.get("schema_version")) is not int
            or store.get("schema_version") != GLOBAL_CONFIG_SCHEMA):
        die(f"{ENV_CONFIGS_JSON} schema_version 不受支持；未读取或修改任何令牌配置")
    if "active_id" not in store:
        die(f"{ENV_CONFIGS_JSON}.active_id 必须存在")
    if "configs" not in store:
        die(f"{ENV_CONFIGS_JSON}.configs 必须存在")
    configs = store.get("configs")
    active_id = store.get("active_id")
    if not isinstance(configs, dict):
        die(f"{ENV_CONFIGS_JSON}.configs 必须是对象")
    seen_names = set()
    for config_id, record in configs.items():
        if not re.fullmatch(r"cfg_[a-z0-9]{10}", config_id) or not isinstance(record, dict):
            die(f"{ENV_CONFIGS_JSON} 含无效的令牌配置 ID 或记录")
        missing = [field for field in (
            "name", "app_token", "table_id", "lark_profile", "feishu_app_id",
            "feishu_user_open_id",
        ) if not isinstance(record.get(field), str) or not record[field]]
        if missing:
            die(f"令牌配置 {config_id} 缺少必填字段：{', '.join(missing)}")
        if (record["name"] != record["name"].strip()
                or any(not ch.isprintable() for ch in record["name"])):
            die(f"令牌配置 {config_id} 的名称含首尾空白或不可打印字符")
        if record["name"] in seen_names:
            die(f"{ENV_CONFIGS_JSON} 中令牌配置名称重复：{record['name']}")
        seen_names.add(record["name"])
    if configs:
        if not isinstance(active_id, str) or active_id not in configs:
            die("令牌配置非空时，active_id 必须且只能指向一个当前配置")
    elif active_id is not None:
        die("令牌配置为空时，active_id 必须是 null")
    return store


def _named_config_json(store: dict) -> str:
    # .env 解析按 splitlines() 逐行读取。ASCII 转义确保任何 Unicode 行分隔符都不会
    # 以真实换行写进单行 JSON，避免下一次加载时把结构化配置拆成多行。
    raw = json.dumps(store, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    _load_named_config_store({ENV_CONFIGS_JSON: raw}, allow_missing=False)
    return raw


def _env_assignment(key: str, value: str) -> str:
    return f"{key}='{value}'"


@contextlib.contextmanager
def _config_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_replace_bytes(path: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    replaced = False
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        replaced = True
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        if replaced and isinstance(exc, OSError):
            raise LocalWriteResultUnknown(
                f"本地写入结果不明：{path} 已替换，但无法确认目录同步完成（{exc}）。"
                "当前文件可能已经包含新内容；请先读取核对，不要直接重放写操作"
            ) from exc
        raise


def _write_env_updates(path: Path, updates: dict) -> None:
    """Preserve comments and unknown keys while replacing selected assignments atomically."""
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    output = []
    emitted = set()
    for raw in original.splitlines():
        match = _ENV_LINE.match(raw.strip())
        key = match.group(1) if match else None
        if key not in updates:
            output.append(raw)
            continue
        if key not in emitted and updates[key] is not None:
            output.append(_env_assignment(key, updates[key]))
        emitted.add(key)
    for key, value in updates.items():
        if key not in emitted and value is not None:
            output.append(_env_assignment(key, value))

    data = ("\n".join(output) + "\n").encode("utf-8") if output else b""
    _atomic_replace_bytes(path, data)


def _new_config_id(configs: dict) -> str:
    while True:
        config_id = "cfg_" + "".join(
            secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10)
        )
        if config_id not in configs:
            return config_id


def _validate_config_name(name: str) -> str:
    if not name or name != name.strip() or any(not ch.isprintable() for ch in name):
        die("令牌配置名称不能为空、不能带首尾空白或不可打印字符")
    return name


def _validate_resource_id(value: str, flag: str) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(ch) < 32 for ch in value)):
        die(f"{flag} 不能为空、不能带首尾空白或控制字符")
    return value


@dataclasses.dataclass(frozen=True)
class ConfigLocation:
    source: str
    config_id: str = ""
    config_name: str = ""


@dataclasses.dataclass(frozen=True)
class ConfigSnapshot:
    app_token: str
    table_id: str
    lark_profile: str
    feishu_app_id: str
    feishu_user_open_id: str
    ids: tuple
    source: str
    config_id: str = ""
    config_name: str = ""

    @property
    def resource_namespace(self) -> str:
        return _resource_namespace(
            self.app_token,
            self.table_id,
            self.feishu_app_id,
            self.feishu_user_open_id,
        )


def _resource_namespace(app_token: str, table_id: str,
                        feishu_app_id: str, feishu_user_open_id: str) -> str:
    identity = {
        "app_token": app_token,
        "table_id": table_id,
        "feishu_app_id": feishu_app_id,
        "feishu_user_open_id": feishu_user_open_id,
    }
    canonical = json.dumps(identity, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _config_layers(use_global: bool) -> tuple[list, dict]:
    env = {key: os.environ.get(key, "") for key in (*RESOURCE_ENV_KEYS, ENV_IDS)}
    layers = [
        ("process_env", env),
        (".env.local", _parse_env_file(Path.cwd() / ".env.local")),
        (".env", _parse_env_file(Path.cwd() / ".env")),
    ]
    global_values = _parse_env_file(global_config_path()) if use_global else {}
    return layers, global_values


def resolve_config_snapshot(use_global: bool) -> ConfigSnapshot:
    """Resolve one complete resource record once; never fill its fields across layers."""
    layers, global_values = _config_layers(use_global)
    id_layers = [values for _, values in layers]
    if use_global:
        id_layers.append(global_values)
    raw_ids = next((values.get(ENV_IDS, "") for values in id_layers
                    if values.get(ENV_IDS)), "")
    ids = tuple(item for item in re.split(r"[,\s]+", raw_ids) if item)

    for source, values in layers:
        present = [key for key in RESOURCE_ENV_KEYS if values.get(key)]
        if not present:
            continue
        missing = [key for key in RESOURCE_ENV_KEYS if not values.get(key)]
        if missing:
            identity_keys = [ENV_FEISHU_APP_ID, ENV_FEISHU_USER_OPEN_ID]
            if missing and all(key in identity_keys for key in missing):
                location = ConfigLocation(source)
                observed = _capture_profile_identity(
                    values[ENV_LARK_PROFILE], snapshot=location
                )
                candidates = observed.pop("_candidates")
                expected = dict(observed)
                expected["app_id"] = None
                expected["open_id"] = None
                _raise_profile_guidance(
                    "feishu_identity_values_missing",
                    f"{source} 中的令牌配置缺少飞书身份固定值；确认实际身份后写回同一层",
                    values[ENV_LARK_PROFILE],
                    candidates,
                    expected=expected,
                    observed=observed,
                    snapshot=location,
                    confirmation_token=_identity_confirmation_token(observed),
                    write_keys=identity_keys,
                )
            die(f"{source} 中的令牌配置不完整，缺少：{', '.join(missing)}。"
                "五个字段必须来自同一层，禁止从其它配置层补齐")
        return ConfigSnapshot(
            values[ENV_APP_TOKEN], values[ENV_TABLE_ID], values[ENV_LARK_PROFILE],
            values[ENV_FEISHU_APP_ID], values[ENV_FEISHU_USER_OPEN_ID], ids, source,
        )

    if use_global:
        if global_values.get(ENV_CONFIGS_JSON):
            store = _load_named_config_store(global_values, allow_missing=False)
            if store["configs"]:
                config_id = store["active_id"]
                record = store["configs"][config_id]
                return ConfigSnapshot(
                    record["app_token"], record["table_id"], record["lark_profile"],
                    record["feishu_app_id"], record["feishu_user_open_id"], ids,
                    "global_current", config_id, record["name"],
                )
        if any(global_values.get(key) for key in RESOURCE_ENV_KEYS):
            die("检测到旧版平面令牌配置。请先运行 config migrate --name <名称>")

    hint = "" if use_global else "（未启用全局配置；如需使用当前配置，请加 --use-global-config）"
    die(f"没有可用的完整令牌配置{hint}。先运行 init-create/init-adopt 和 config save")


def _snapshot_for_args(args) -> ConfigSnapshot:
    snapshot = getattr(args, "_config_snapshot", None)
    if snapshot is None:
        snapshot = resolve_config_snapshot(args.use_global_config)
        args._config_snapshot = snapshot
    return snapshot


def _higher_priority_resource_source() -> str:
    layers = [
        ("进程环境变量", {key: os.environ.get(key, "") for key in RESOURCE_ENV_KEYS}),
        ("当前目录的 .env.local", _parse_env_file(Path.cwd() / ".env.local")),
        ("当前目录的 .env", _parse_env_file(Path.cwd() / ".env")),
    ]
    for label, values in layers:
        if any(values.get(key) for key in RESOURCE_ENV_KEYS):
            return label
    return ""


def require_backend(args) -> "FeishuBackend":
    snapshot = _snapshot_for_args(args)
    observed = _capture_profile_identity(snapshot.lark_profile, snapshot=snapshot)
    if (observed["app_id"] != snapshot.feishu_app_id
            or observed["open_id"] != snapshot.feishu_user_open_id):
        expected = {
            "lark_profile": snapshot.lark_profile,
            "app_id": snapshot.feishu_app_id,
            "user": None,
            "open_id": snapshot.feishu_user_open_id,
        }
        _raise_profile_guidance(
            "feishu_identity_mismatch",
            "当前 profile 实际身份与令牌配置固定的 appId/openId 不一致；拒绝访问令牌表",
            snapshot.lark_profile,
            observed.pop("_candidates"),
            expected=expected,
            observed=observed,
            snapshot=snapshot,
        )
    return FeishuBackend(
        snapshot.app_token,
        snapshot.table_id,
        snapshot.lark_profile,
        snapshot.feishu_user_open_id,
    )


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


# ---------- lark-cli 调用与网络抖动处理（ADR 0006）----------
# 本 skill 的每一条业务命令都经 lark-cli 打飞书开放平台，属网络请求路径。
#
# lark-cli 1.0.82 实测（2026-08-18）：`--format json` 下**失败信封写 stderr**、
# 成功输出写 stdout；网络类失败信封形如
#   {"ok": false, "error": {"type": "network", "subtype": "transport",
#    "message": "API call failed: Get \"https://open.feishu.cn/...\": ...
#                dial tcp ...: connect: connection refused"}}
# 退出码 4；profile 不存在这类配置错误 type=config、退出码 3；参数错误退出码 2。
# 因此分类优先读信封的 error.type（结构化），无信封时才退到关键词匹配。

LARK_READ_TIMEOUT = 60    # 查询类：+record-list / +field-list / auth status / +url-resolve
LARK_WRITE_TIMEOUT = 120  # 写入类：+record-batch-* / +field-create / +base-create（建 Base 连带建表，最慢）
LARK_MAX_ATTEMPTS = 3     # ADR 0006 规则 3：总尝试 3 次（首次 + 2 次重试），退避 2^n 秒

# 写操作结果不明（ADR 0006 规则 4）：调用方须先核实再决定是否重试。
#
# 取值必须避开**被包装命令**的常用退出码。本 skill 的 `run` 会原样透传子命令的
# 退出码（`sys.exit(proc.returncode)` / `execvpe`），而 `run --name <别名>` 在
# 启动子命令**之前**还会经 resolve_records → backfill_ids 打一次写请求（给手工
# 粘贴进表格的行补 id），那次写超时就以本码退出。也就是说同一个数字有两种来源，
# 值一旦落在子命令的常用区间就不可区分——原值 4 正好撞上 curl 的 4（功能未编入
# libcurl），调用方无从判断是"令牌表写入结果不明"还是"curl 缺特性"。
#
# 选 121 的依据（不是随手挑一个大数）：
# - 121–125 是"包装器自身错误"的既有惯例带，紧邻 shell 保留的 126（找到但不可
#   执行）/ 127（找不到）/ 128+N（被信号杀死）。GNU timeout 用 124/125、
#   xargs 用 123–125，都落在这一带。
# - 121 高于常见被包装命令的取值上限：curl 文档化的最大退出码约 102，git、
#   rsync、ffmpeg 等更低；同时避开 timeout 与 xargs 已占的 123–125。
# - 不用 64–78（BSD sysexits.h 有各自既定语义，78=EX_CONFIG 是"配置有误"，
#   与"写入结果不明"不是一回事）。
EXIT_AMBIGUOUS = 121

# 读 = 幂等查询，瞬时故障可安全重试。
LARK_READ_SHORTCUTS = frozenset({"+record-list", "+field-list"})
# 写 = 会改令牌表结构或内容。飞书 Base 这几个接口没有幂等键，本 skill 也没有
# 「先查后写」的对账通道，所以超时/连接中断后结果不明，一律不盲重试（规则 4）。
LARK_WRITE_SHORTCUTS = frozenset({"+record-batch-create", "+record-batch-update",
                                  "+field-create"})

# 无 JSON 信封时的兜底判据（参考 local-skills/deliver-files 的 TRANSIENT_HINTS）
TRANSIENT_HINTS = ("timeout", "timed out", "i/o timeout", "connection reset",
                   "connection refused", "broken pipe", "no such host", "dns",
                   "tls handshake", "eof", "temporarily unavailable",
                   "network is unreachable", "no route to host")


def _extract_envelope(text: str) -> dict | None:
    """从 lark-cli 输出里取出 JSON 信封。

    用 raw_decode 而不是 json.loads：信封前后都可能有非 JSON 的进度行 / 提示行，
    整段 loads 会因为尾部残留而失败。
    """
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx >= 0:
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and "ok" in obj:
            return obj
        idx = text.find("{", idx + 1)
    return None


def _transient_reason(proc, what: str) -> str | None:
    """瞬时故障返回原因文本；确定性失败（鉴权、参数、业务报错）返回 None。"""
    envelope = _extract_envelope(proc.stderr or "") or _extract_envelope(proc.stdout or "")
    err = (envelope or {}).get("error") or {}
    message = " ".join(str(err.get("message") or "").split())
    if err.get("type") == "network":
        return f"lark-cli {what} 网络失败：{message[:300]}"
    if envelope is not None:
        return None  # 有结构化分类且不是 network：确定性失败，重试必然同样失败
    tail = " ".join((proc.stderr or proc.stdout or "").split())[:300]
    if any(h in tail.lower() for h in TRANSIENT_HINTS):
        return f"lark-cli {what} 疑似网络失败：{tail}"
    return None


def _lark_exec(cmd: list, kind: str, what: str) -> subprocess.CompletedProcess:
    """执行一次 lark-cli，按 ADR 0006 处理瞬时故障。

    kind="read"：瞬时故障重试至多 LARK_MAX_ATTEMPTS 次，指数退避 2^n 秒。
    kind="write"：瞬时故障不重试，直接以 EXIT_AMBIGUOUS 终止（结果不明）。
    确定性失败原样返回 CompletedProcess，由调用方按既有逻辑 die()。
    """
    if kind not in ("read", "write"):
        die(f"内部错误：未知的 lark-cli 调用类型 {kind}")
    timeout = LARK_READ_TIMEOUT if kind == "read" else LARK_WRITE_TIMEOUT
    for attempt in range(LARK_MAX_ATTEMPTS):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            # 确定性失败：二进制不在 PATH，重试必然同样失败
            die("lark-cli 未安装或不在 PATH 中。请先安装 lark-cli 并完成 user 身份登录")
        except subprocess.TimeoutExpired:
            reason = f"lark-cli {what} 超过 {timeout}s 未返回"
        except OSError as exc:  # 进程起不来（fork 失败、资源暂时不足等），算瞬时
            reason = f"无法启动 lark-cli 进程：{exc}"
        else:
            if proc.returncode == 0:
                return proc
            reason = _transient_reason(proc, what)
            if reason is None:
                return proc  # 确定性失败，交回调用方
        if kind == "write":
            die(f"{reason}。写入结果不明（ambiguous）：该请求可能已经送达飞书、也可能没有，"
                "本 skill 不做盲重试（重发会造出重复记录/重复字段）。"
                "请先用 list 核实是否已写入，再决定要不要重试。", EXIT_AMBIGUOUS)
        if attempt == LARK_MAX_ATTEMPTS - 1:
            die(f"{reason}（已尝试 {LARK_MAX_ATTEMPTS} 次仍失败）")
        wait = 2 ** attempt  # 1s、2s
        warn(f"{reason}；{wait}s 后重试（第 {attempt + 2}/{LARK_MAX_ATTEMPTS} 次尝试）")
        time.sleep(wait)
    die("内部错误：lark-cli 重试循环未正常退出")  # 不可达，仅为控制流完整


def _capture_profile_identity(profile: str, *, snapshot=None) -> dict:
    """Read the local lark-cli profile identity without accessing Feishu APIs."""
    proc = _lark_exec(["lark-cli", "profile", "list"], "read", "profile list")
    profiles = None
    if proc.stdout.strip():
        try:
            profiles = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    if proc.returncode != 0 or not isinstance(profiles, list):
        _raise_profile_guidance(
            "feishu_profile_list_unavailable",
            "无法从 lark-cli 读取有效的 profile 列表；拒绝访问令牌表",
            profile,
            [],
            snapshot=snapshot,
        )
    candidates = [{
        key: item.get(key) for key in ("name", "tokenStatus", "active", "user", "brand", "appId")
    } for item in profiles if isinstance(item, dict)]
    entry = next((item for item in profiles
                  if isinstance(item, dict) and item.get("name") == profile), None)
    if entry is None:
        _raise_profile_guidance(
            "feishu_profile_not_found",
            f"配置的 profile 不在本机 lark-cli profile 列表中：{profile}",
            profile,
            candidates,
            snapshot=snapshot,
        )
    if entry.get("tokenStatus") != "valid":
        _raise_profile_guidance(
            "feishu_profile_not_authenticated",
            f"profile 的本地 token 未就绪：{profile}",
            profile,
            candidates,
            observed={
                "lark_profile": profile,
                "app_id": entry.get("appId"),
                "user": entry.get("user"),
                "open_id": None,
            },
            snapshot=snapshot,
        )

    proc = _lark_exec(
        ["lark-cli", "auth", "status", "--json", "--profile", profile],
        "read", "auth status",
    )
    status = {}
    if proc.stdout.strip():
        try:
            status = json.loads(proc.stdout)
        except json.JSONDecodeError:
            if proc.returncode == 0:
                die(f"lark-cli auth status 未返回 JSON（profile={profile}）")
    status_obj = status if isinstance(status, dict) else {}
    identities = status_obj.get("identities")
    identities_obj = identities if isinstance(identities, dict) else {}
    raw_user = identities_obj.get("user")
    user = raw_user if isinstance(raw_user, dict) else {}
    ready = (
        proc.returncode == 0
        and status_obj.get("identity") == "user"
        and user.get("status") == "ready"
        and user.get("available") is True
        and user.get("tokenStatus") == "valid"
    )
    if not ready:
        _raise_profile_guidance(
            "feishu_profile_not_authenticated",
            f"profile 的本地用户身份未就绪：{profile}",
            profile,
            candidates,
            observed={
                "lark_profile": profile,
                "app_id": entry.get("appId"),
                "user": entry.get("user"),
                "open_id": user.get("openId"),
            },
            snapshot=snapshot,
        )
    app_id = entry.get("appId")
    open_id = user.get("openId")
    if not isinstance(app_id, str) or not app_id or not isinstance(open_id, str) or not open_id:
        die(f"lark-cli 未提供完整的 appId/openId，无法确认 profile 身份：{profile}")
    return {
        "lark_profile": profile,
        "app_id": app_id,
        "user": entry.get("user"),
        "open_id": open_id,
        "_candidates": candidates,
    }


def _identity_confirmation_token(identity: dict) -> str:
    public_identity = {key: value for key, value in identity.items() if not key.startswith("_")}
    canonical = json.dumps(public_identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(
        ("secret-book identity confirmation v1\n" + canonical).encode("utf-8")
    ).hexdigest()


def _confirmed_profile_identity(profile: str, confirmation_token: str | None) -> dict:
    identity = _capture_profile_identity(profile)
    expected = _identity_confirmation_token(identity)
    if not confirmation_token or not secrets.compare_digest(confirmation_token, expected):
        print(json.dumps({
            "schema_version": CONFIG_IDENTITY_CONFIRMATION_SCHEMA,
            "status": "confirmation_required",
            "reason": "请确认该应用与用户就是要访问令牌表的飞书身份",
            "observed_identity": {key: value for key, value in identity.items()
                                  if not key.startswith("_")},
            "confirmation_token": expected,
        }, ensure_ascii=False, sort_keys=True))
        raise SystemExit(EXIT_GUIDANCE)
    return identity


# ---------- 后端适配器 ----------

class FeishuBackend:
    """飞书多维表格后端，经 lark-cli。所有方法只吞吐 {字段名: 字符串} 平面记录。"""

    def __init__(self, app_token: str, table_id: str, profile: str = "",
                 user_open_id: str = ""):
        self.app_token = app_token
        self.table_id = table_id
        # 空 = 不传 --profile，沿用 lark-cli 当前 active profile
        self.profile = profile or ""
        self.user_open_id = user_open_id

    def _profile_args(self) -> list:
        return ["--profile", self.profile] if self.profile else []

    def _profile_note(self) -> str:
        return f"（profile={self.profile}）" if self.profile else "（未指定 profile，用的是 lark-cli 当前 active profile）"

    def current_user_open_id(self) -> str:
        """当前 lark-cli user 身份的 open_id，visible_to 过滤的比对基准。
        取不到时 die（fail-closed）：不能确定「我是谁」就不放行受限记录。"""
        if not self.user_open_id:
            die(f"令牌配置没有已验证的飞书用户 open_id{self._profile_note()}，"
                "无法执行 visible_to 可见范围过滤，拒绝继续")
        return self.user_open_id

    def _run(self, shortcut: str, extra: list) -> dict:
        # 读 / 写归类是重试策略的唯一依据（ADR 0006 规则 4），未归类的子命令
        # 直接报错：默认按读重试有可能把一次写操作盲重放成两条记录。
        if shortcut in LARK_WRITE_SHORTCUTS:
            kind = "write"
        elif shortcut in LARK_READ_SHORTCUTS:
            kind = "read"
        else:
            die(f"内部错误：lark-cli 子命令 {shortcut} 未在读/写集合中归类")
        cmd = ["lark-cli", "base", shortcut, "--as", "user", "--format", "json",
               *self._profile_args(),
               "--base-token", self.app_token, "--table-id", self.table_id, *extra]
        proc = _lark_exec(cmd, kind, shortcut)
        if proc.returncode != 0:
            die(f"lark-cli {shortcut} 失败 (exit {proc.returncode}){self._profile_note()}: "
                f"{proc.stderr.strip()[:500]}")
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            die(f"lark-cli {shortcut} 返回非 JSON 输出（前 200 字符）: {proc.stdout[:200]}")
        if not isinstance(payload, dict):
            die(f"lark-cli {shortcut} 返回的 JSON 不是对象")
        return payload

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
        # 人员单元格 = [{"id": "ou_xxx", "name": "..."}]，空为 null；缺列（存量
        # 8 列表）投影被忽略、取值 None，等价于空 = 不受限（实测 lark-cli 1.0.82）
        cell = fields.get("visible_to")
        if cell is None:
            users = []
        elif not isinstance(cell, list):
            die("令牌记录的 visible_to 不是人员多选数组，无法安全判断可见范围")
        else:
            users = []
            for item in cell:
                user_id = item.get("id") if isinstance(item, dict) else None
                if (not isinstance(user_id, str) or not user_id
                        or user_id != user_id.strip()
                        or any(ord(ch) < 32 for ch in user_id)):
                    die("令牌记录的 visible_to 含无效人员项，无法安全判断可见范围")
                users.append(item)
        rec["_visible_to_ids"] = [u["id"] for u in users]
        rec["visible_to"] = ", ".join(str(u.get("name") or u["id"]) for u in users)
        rec["_record_id"] = fields.get("_record_id", "")
        return rec

    def list_records(self, filter_json: str | None = None, with_secret: bool = False,
                     visible_only: bool = True) -> list:
        projection = []
        for f in META_FIELDS + ["visible_to"] + (["secret"] if with_secret else []):
            projection += ["--field-id", f]
        records, offset = [], 0
        while True:
            extra = ["--limit", "200", "--offset", str(offset), *projection]
            if filter_json:
                extra += ["--filter-json", filter_json]
            rows, has_more = self._rows(self._run("+record-list", extra))
            records += [self._flatten(r) for r in rows]
            if not has_more:
                break
            offset += 200
        if visible_only and any(r["_visible_to_ids"] for r in records):
            me = self.current_user_open_id()
            records = [r for r in records
                       if not r["_visible_to_ids"] or me in r["_visible_to_ids"]]
        return records

    def find(self, by: str, value: str, with_secret: bool, visible_only: bool = True) -> list:
        cond = json.dumps({"logic": "and", "conditions": [[by, "==", value]]}, ensure_ascii=False)
        return self.list_records(filter_json=cond, with_secret=with_secret, visible_only=visible_only)

    def create_record(self, fields: dict) -> None:
        body = json.dumps({"create_records": [fields]}, ensure_ascii=False)
        self._run("+record-batch-create", ["--json", body])

    def update_record(self, record_id: str, fields: dict) -> None:
        body = json.dumps({"update_records": {record_id: fields}}, ensure_ascii=False)
        self._run("+record-batch-update", ["--json", body])


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
    """按 --id 或 --name 解析目标记录；拒绝没有令牌表身份的旧版 ID 配置。"""
    ids: list = list(getattr(args, "id", None) or [])
    name = getattr(args, "name", None)
    if not ids and not name:
        snapshot = _snapshot_for_args(args)
        if snapshot.ids:
            info(f"检测到旧版 {ENV_IDS}，但裸记录 ID 无法证明属于当前令牌表。"
                 "请删除该变量，并用 run --id <id> --bind 建立带令牌表身份的自动绑定")
            raise SystemExit(EXIT_GUIDANCE)
        die("未指定 --id/--name；请精确指定令牌记录，或使用 run --auto")
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


def _require_single_lookup(args, action: str) -> None:
    ids = getattr(args, "id", None) or []
    if getattr(args, "name", None) and ids:
        die(f"{action} 的 --name 与 --id 互斥；一次只能指定一条令牌记录")
    if len(ids) > 1:
        die(f"{action} 一次只能指定一个 --id")


def merged_env_pairs(records: list) -> dict:
    """多记录键值对合并注入；键名冲突报错退出，不静默覆盖（design.md §4.1）。"""
    merged, owner = {}, {}
    for rec in records:
        for key, val in parse_payload(rec["secret"]).items():
            if key in merged:
                die(f"键名冲突：{key} 同时来自记录 {owner[key]} 与 {rec['name'] or rec['id']}")
            merged[key], owner[key] = val, rec["name"] or rec["id"]
    return merged


# ---------- 自动绑定（bindings.json）----------
# (令牌表身份, 项目根, 命令名) → 记录 id 列表。只存元数据不存值；本机私有，不同步进令牌表
# （里面是本机路径；v2 不再使用无法证明所属令牌表的 SECRET_BOOK_IDS）。

def bindings_path() -> Path:
    return Path.home() / ".config" / SKILL_NAME / "bindings.json"


def _load_bindings() -> dict:
    path = bindings_path()
    if not path.is_file():
        return {"version": 2, "bindings": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise BindingsFileError(f"{path} 不是合法 JSON，请检查或删除该文件后重试")
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data.get("version") not in (1, 2)):
        raise BindingsFileError(f"{path} 的 schema version 不受支持")
    entries = data.get("bindings")
    if not isinstance(entries, list):
        raise BindingsFileError(f"{path}.bindings 必须是数组")
    for entry in entries:
        namespace = entry.get("resource_namespace") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict)
                or not isinstance(entry.get("scope"), str)
                or not isinstance(entry.get("command"), str)
                or not isinstance(entry.get("ids"), list)
                or not entry["ids"]
                or not all(isinstance(item, str) and item for item in entry["ids"])
                or ("hits" in entry
                    and (type(entry["hits"]) is not int or entry["hits"] < 0))
                or ("created" in entry and not isinstance(entry["created"], str))
                or ("last_used" in entry and not isinstance(entry["last_used"], str))
                or ("resource_namespace" in entry
                    and (not isinstance(namespace, str)
                         or re.fullmatch(r"[0-9a-f]{64}", namespace) is None))):
            raise BindingsFileError(f"{path} 含无效的自动绑定条目")
    return data


def _save_bindings(data: dict) -> None:
    data["version"] = 2
    path = bindings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_replace_bytes(path, payload)


@contextlib.contextmanager
def _bindings_file_lock():
    with _config_file_lock(bindings_path()):
        yield


def _project_scope() -> str:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    root = proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else str(Path.cwd())
    return str(Path(root).resolve())


def _find_binding(data: dict, scope: str, command: str,
                  resource_namespace: str) -> dict | None:
    for entry in data["bindings"]:
        if (entry.get("scope") == scope
                and entry.get("command") == command
                and entry.get("resource_namespace") == resource_namespace):
            return entry
    return None


def _find_legacy_binding(data: dict, scope: str, command: str) -> dict | None:
    for entry in data["bindings"]:
        if (entry.get("scope") == scope
                and entry.get("command") == command
                and not entry.get("resource_namespace")):
            return entry
    return None


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _warn_expired(records: list) -> None:
    today = datetime.date.today().isoformat()
    for rec in records:
        exp = rec.get("expires_at", "")
        if exp and exp[:10] < today:
            info(f"警告：记录 {rec['name'] or rec['id']} 已于 {exp} 过期（仍继续执行）")


def _resolve_auto(backend: FeishuBackend, args) -> list:
    """run --auto 仅接受带令牌表身份的 bindings.json 自动绑定。

    旧版 SECRET_BOOK_IDS、无绑定或绑定失效均以退出码 3 结束，agent 据此转入
    list + 意图匹配流程。
    """
    snapshot = _snapshot_for_args(args)
    if snapshot.ids:
        info(f"检测到旧版 {ENV_IDS}，但裸记录 ID 无法证明属于当前令牌表。"
             "请删除该变量，并用 run --id <id> --bind 建立带令牌表身份的自动绑定")
        raise SystemExit(EXIT_GUIDANCE)
    scope, cmdname = _project_scope(), Path(args.command[0]).name
    data = _load_bindings()
    entry = _find_binding(data, scope, cmdname, snapshot.resource_namespace)
    if entry is None:
        if _find_legacy_binding(data, scope, cmdname) is not None:
            info("发现旧版自动绑定，但它没有令牌表身份，无法判断属于哪套令牌配置。"
                 "请重新按意图选择令牌记录并用 run --bind 建立绑定")
            sys.exit(3)
        info(f"无绑定（项目={scope}, 命令={cmdname}）。请走 list+意图匹配流程，"
             "匹配成功后用 run --bind 建立绑定")
        sys.exit(3)
    records, stale = [], []
    for sid in entry["ids"]:
        matches = backend.find("id", sid, with_secret=True)
        records.extend(matches) if matches else stale.append(sid)
    if stale:
        removed = False
        with _bindings_file_lock():
            current_data = _load_bindings()
            current_entry = _find_binding(
                current_data, scope, cmdname, snapshot.resource_namespace
            )
            if current_entry is not None and current_entry.get("ids") == entry.get("ids"):
                current_data["bindings"].remove(current_entry)
                _save_bindings(current_data)
                removed = True
        suffix = "绑定已自动解除" if removed else "绑定已被其它进程更新，未删除新绑定"
        info(f"绑定失效：令牌记录 {', '.join(stale)} 已不在令牌表，{suffix}。请重新匹配")
        sys.exit(3)
    for rec in records:
        parse_payload(rec["secret"])  # 把值登记进 _SENSITIVE
    with _bindings_file_lock():
        current_data = _load_bindings()
        current_entry = _find_binding(
            current_data, scope, cmdname, snapshot.resource_namespace
        )
        if current_entry is not None and current_entry.get("ids") == entry.get("ids"):
            current_entry["last_used"] = _now()
            current_entry["hits"] = current_entry.get("hits", 0) + 1
            _save_bindings(current_data)
    info("按绑定使用 " + ", ".join(
        f"{r['id']}（{r['name']}, service={r['service']}）" for r in records))
    return records


# ---------- 动作 ----------

def cmd_config_save(args) -> None:
    name = _validate_config_name(args.name)
    app_token = _validate_resource_id(args.app_token, "--app-token")
    table_id = _validate_resource_id(args.table_id, "--table-id")
    current_values = _parse_env_file(global_config_path())
    legacy = [key for key in RESOURCE_ENV_KEYS if current_values.get(key)]
    if legacy:
        die("检测到旧版平面令牌配置。请先运行 config migrate --name <现有配置名称>，"
            "避免同一文件同时存在两种配置格式")
    current_store = _load_named_config_store(current_values)
    if any(record["name"] == name for record in current_store["configs"].values()):
        die(f"令牌配置名称已存在：{name}")

    identity = _confirmed_profile_identity(args.lark_profile, args.confirm_identity)
    target = global_config_path()
    with _config_file_lock(target):
        values = _parse_env_file(target)
        legacy = [key for key in RESOURCE_ENV_KEYS if values.get(key)]
        if legacy:
            die("检测到旧版平面令牌配置。请先运行 config migrate --name <现有配置名称>，"
                "避免同一文件同时存在两种配置格式")
        store = _load_named_config_store(values)
        if any(record["name"] == name for record in store["configs"].values()):
            die(f"令牌配置名称已存在：{name}")
        config_id = _new_config_id(store["configs"])
        store["configs"][config_id] = {
            "name": name,
            "app_token": app_token,
            "table_id": table_id,
            "lark_profile": args.lark_profile,
            "feishu_app_id": identity["app_id"],
            "feishu_user_open_id": identity["open_id"],
        }
        if store["active_id"] is None:
            store["active_id"] = config_id
        _write_env_updates(target, {ENV_CONFIGS_JSON: _named_config_json(store)})
    current_note = "，并设为当前配置" if store["active_id"] == config_id else ""
    info(f"已保存令牌配置：{name}（id={config_id}{current_note}）")


def cmd_config_list(args) -> None:
    store = _load_named_config_store(_parse_env_file(global_config_path()))
    if not store["configs"]:
        info("还没有令牌配置")
        return
    print("当前配置  ID              名称  lark-cli profile")
    for config_id, record in store["configs"].items():
        current = "是" if config_id == store["active_id"] else ""
        print(f"{current:<8}  {config_id}  {record['name']}  {record['lark_profile']}")


def cmd_config_use(args) -> None:
    name = _validate_config_name(args.name)
    target = global_config_path()
    with _config_file_lock(target):
        values = _parse_env_file(target)
        store = _load_named_config_store(values, allow_missing=False)
        matches = [config_id for config_id, record in store["configs"].items()
                   if record["name"] == name]
        if not matches:
            die(f"找不到令牌配置：{name}。请先运行 config list")
        store["active_id"] = matches[0]
        _write_env_updates(target, {ENV_CONFIGS_JSON: _named_config_json(store)})
    info(f"当前配置已切换为：{name}（id={matches[0]}）")
    override = _higher_priority_resource_source()
    if override:
        preferred = "进程环境变量中的令牌配置" if override == "进程环境变量" else "项目配置"
        warn(f"{override} 定义了令牌配置；业务命令仍会优先使用{preferred}，"
             "不会使用刚切换的全局当前配置")


def cmd_config_rebind(args) -> None:
    name = _validate_config_name(args.name)
    target = global_config_path()
    initial_store = _load_named_config_store(_parse_env_file(target), allow_missing=False)
    config_id = _config_id_by_name(initial_store, name)
    identity = _confirmed_profile_identity(args.lark_profile, args.confirm_identity)
    with _config_file_lock(target):
        store = _load_named_config_store(_parse_env_file(target), allow_missing=False)
        record = store["configs"].get(config_id)
        if record is None or record["name"] != name:
            die("令牌配置在身份确认期间被删除或重命名；未执行更新，请重新开始")
        record["lark_profile"] = args.lark_profile
        record["feishu_app_id"] = identity["app_id"]
        record["feishu_user_open_id"] = identity["open_id"]
        _write_env_updates(target, {ENV_CONFIGS_JSON: _named_config_json(store)})
    info(f"已更新令牌配置的飞书身份：{name}（id={config_id}，profile={args.lark_profile}）")


def _config_id_by_name(store: dict, name: str) -> str:
    matches = [config_id for config_id, record in store["configs"].items()
               if record["name"] == name]
    if not matches:
        die(f"找不到令牌配置：{name}。请先运行 config list")
    return matches[0]


def cmd_config_rename(args) -> None:
    name = _validate_config_name(args.name)
    new_name = _validate_config_name(args.new_name)
    target = global_config_path()
    with _config_file_lock(target):
        values = _parse_env_file(target)
        store = _load_named_config_store(values, allow_missing=False)
        config_id = _config_id_by_name(store, name)
        if any(record["name"] == new_name and other_id != config_id
               for other_id, record in store["configs"].items()):
            die(f"令牌配置名称已存在：{new_name}")
        store["configs"][config_id]["name"] = new_name
        _write_env_updates(target, {ENV_CONFIGS_JSON: _named_config_json(store)})
    info(f"令牌配置已重命名：{name} → {new_name}（id={config_id}）")


def cmd_config_remove(args) -> None:
    name = _validate_config_name(args.name)
    target = global_config_path()
    with _config_file_lock(target):
        values = _parse_env_file(target)
        store = _load_named_config_store(values, allow_missing=False)
        config_id = _config_id_by_name(store, name)
        if config_id == store["active_id"] and len(store["configs"]) > 1:
            die(f"{name} 是当前配置。请先用 config use 切换到另一套，再删除这套令牌配置")
        del store["configs"][config_id]
        if not store["configs"]:
            store["active_id"] = None
        _write_env_updates(target, {ENV_CONFIGS_JSON: _named_config_json(store)})
    info(f"已删除令牌配置：{name}（id={config_id}）")


def _legacy_global_config(values: dict, profile_override: str | None) -> dict:
    if values.get(ENV_CONFIGS_JSON):
        die("全局配置已经使用命名令牌配置，不需要重复迁移")
    app_token = values.get(ENV_APP_TOKEN, "")
    table_id = values.get(ENV_TABLE_ID, "")
    configured_profile = values.get(ENV_LARK_PROFILE, "")
    if profile_override and configured_profile and profile_override != configured_profile:
        die(f"旧版全局令牌配置已指定 {ENV_LARK_PROFILE}={configured_profile}；"
            "--lark-profile 只能在旧配置缺少该字段时补充，不能覆盖")
    profile = configured_profile or profile_override or ""
    missing = []
    if not app_token:
        missing.append(ENV_APP_TOKEN)
    if not table_id:
        missing.append(ENV_TABLE_ID)
    if not profile:
        missing.append(ENV_LARK_PROFILE)
    if missing:
        die("旧版全局令牌配置不完整，缺少：" + ", ".join(missing))
    return {"app_token": app_token, "table_id": table_id, "lark_profile": profile}


def cmd_config_migrate(args) -> None:
    name = _validate_config_name(args.name)
    target = global_config_path()
    initial_values = _parse_env_file(target)
    initial_named_raw = initial_values.get(ENV_CONFIGS_JSON, "")
    if initial_named_raw:
        store = _load_named_config_store(initial_values, allow_missing=False)
        stale_keys = [key for key in (*RESOURCE_ENV_KEYS, ENV_IDS)
                      if initial_values.get(key)]
        if store["configs"]:
            config_id = _config_id_by_name(store, name)
            if not stale_keys:
                die("全局配置已经使用命名令牌配置，不需要重复迁移")
            with _config_file_lock(target):
                current_values = _parse_env_file(target)
                current_store = _load_named_config_store(current_values, allow_missing=False)
                if (current_store != store
                        or current_store["configs"].get(config_id, {}).get("name") != name):
                    die("命名令牌配置在清理旧字段期间发生变化；未执行清理，请重新开始")
                current_stale = [key for key in (*RESOURCE_ENV_KEYS, ENV_IDS)
                                 if current_values.get(key)]
                if current_stale != stale_keys:
                    die("旧版平面字段在清理期间发生变化；未执行清理，请重新开始")
                _write_env_updates(target, {key: None for key in stale_keys})
            info("已清理与命名令牌配置并存的旧版平面字段：" + ", ".join(stale_keys))
            return
        if not any(initial_values.get(key) for key in RESOURCE_ENV_KEYS):
            if stale_keys == [ENV_IDS]:
                with _config_file_lock(target):
                    current_values = _parse_env_file(target)
                    if current_values.get(ENV_CONFIGS_JSON, "") != initial_named_raw:
                        die("命名令牌配置在清理旧字段期间发生变化；未执行清理，请重新开始")
                    _write_env_updates(target, {ENV_IDS: None})
                info(f"已从空命名配置中清理旧版 {ENV_IDS}")
                return
            die("命名令牌配置为空，且没有可迁移的旧版平面令牌配置")
    legacy_values = dict(initial_values)
    legacy_values.pop(ENV_CONFIGS_JSON, None)
    legacy = _legacy_global_config(legacy_values, args.lark_profile)
    identity = _confirmed_profile_identity(legacy["lark_profile"], args.confirm_identity)
    with _config_file_lock(target):
        current_values = _parse_env_file(target)
        if current_values.get(ENV_CONFIGS_JSON, "") != initial_named_raw:
            die("命名令牌配置在身份确认期间发生变化；未执行迁移，请重新开始")
        current_legacy_values = dict(current_values)
        current_legacy_values.pop(ENV_CONFIGS_JSON, None)
        current_legacy = _legacy_global_config(current_legacy_values, args.lark_profile)
        if current_legacy != legacy:
            die("旧版全局令牌配置在身份确认期间发生变化；未执行迁移，请重新开始")
        store = _empty_named_config_store()
        config_id = _new_config_id(store["configs"])
        store["configs"][config_id] = {
            "name": name,
            "app_token": legacy["app_token"],
            "table_id": legacy["table_id"],
            "lark_profile": legacy["lark_profile"],
            "feishu_app_id": identity["app_id"],
            "feishu_user_open_id": identity["open_id"],
        }
        store["active_id"] = config_id
        updates = {key: None for key in (*RESOURCE_ENV_KEYS, ENV_IDS)}
        updates[ENV_CONFIGS_JSON] = _named_config_json(store)
        _write_env_updates(target, updates)
    info(f"旧版令牌配置已迁移为：{name}（id={config_id}，当前配置）")


def cmd_save(args) -> None:
    payload_text = sys.stdin.read()
    pairs = parse_payload(payload_text)
    backend = require_backend(args)
    # 查重跨全表（含对当前用户隐藏的记录）：name 是全局查找键，可见范围不豁免唯一性
    if backend.find("name", args.name, with_secret=False, visible_only=False):
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
        info("令牌表为空")
        return
    header = META_FIELDS
    widths = [max(len(h), *(len(r[h]) for r in records)) for h in header]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for r in records:
        print("  ".join(r[h].ljust(w) for h, w in zip(header, widths)))


def cmd_get(args) -> None:
    _require_single_lookup(args, "get")
    backend = require_backend(args)
    rec = resolve_records(backend, args, need_secret=True)[0]
    for key in META_FIELDS + ["visible_to", "notes"]:
        print(f"{key}: {rec.get(key, '')}")
    print(f"keys: {', '.join(parse_payload(rec['secret']))}")


def cmd_run(args) -> None:
    if not args.command:
        die("run 需要 '-- <命令>'")
    if args.auto and (args.name or args.id):
        die("--auto 与 --name/--id 互斥：绑定查找与显式指定二选一")
    if args.auto and args.bind:
        die("--auto 与 --bind 互斥：复用已有绑定时不能再次建立绑定")
    backend = require_backend(args)
    binding_to_save = None
    if args.auto:
        records = _resolve_auto(backend, args)
    else:
        records = resolve_records(backend, args, need_secret=True)
        if args.bind:
            binding_to_save = {"scope": _project_scope(),
                               "command": Path(args.command[0]).name,
                               "resource_namespace": _snapshot_for_args(args).resource_namespace,
                               "ids": [r["id"] for r in records]}
    pairs = merged_env_pairs(records)
    _warn_expired(records)
    env = dict(os.environ)
    env.update(pairs)
    info(f"注入 {len(pairs)} 个变量（{', '.join(pairs)}）后执行：{' '.join(args.command)}")
    if binding_to_save is None:
        try:
            os.execvpe(args.command[0], args.command, env)
        except FileNotFoundError:
            die(f"命令不存在：{args.command[0]}")
    # --bind 需要观察子进程退出码（成功才落绑定），所以不能 execvpe 替换进程
    try:
        proc = subprocess.run(args.command, env=env)
    except FileNotFoundError:
        die(f"命令不存在：{args.command[0]}")
    if proc.returncode == 0:
        try:
            with _bindings_file_lock():
                data = _load_bindings()
                existing = _find_binding(
                    data,
                    binding_to_save["scope"],
                    binding_to_save["command"],
                    binding_to_save["resource_namespace"],
                )
                if existing:
                    data["bindings"].remove(existing)
                data["bindings"] = [entry for entry in data["bindings"] if not (
                    entry.get("scope") == binding_to_save["scope"]
                    and entry.get("command") == binding_to_save["command"]
                    and not entry.get("resource_namespace")
                )]
                binding_to_save.update({"created": (existing or {}).get("created", _now()),
                                        "last_used": _now(),
                                        "hits": (existing or {}).get("hits", 0) + 1})
                data["bindings"].append(binding_to_save)
                _save_bindings(data)
        except LocalWriteResultUnknown as exc:
            warn("被包装命令已经成功，本次仍返回命令的退出码 0；"
                 "自动绑定持久化结果不明，bindings.json 当前可能已经包含新绑定。"
                 f"请先运行 bindings 核对，不要直接重放 --bind：{exc}")
        except (BindingsFileError, OSError) as exc:
            warn("被包装命令已经成功，但自动绑定保存失败；本次仍返回命令的退出码 0。"
                 f"未建立绑定，修复本地 bindings.json 后再执行一次 --bind：{exc}")
        else:
            info(f"已绑定（项目={binding_to_save['scope']}, 命令={binding_to_save['command']}）"
                 f"→ {', '.join(binding_to_save['ids'])}；下次 run --auto 直用，unbind 可解除")
    sys.exit(proc.returncode)


def cmd_bindings(args) -> None:
    data = _load_bindings()
    if not data["bindings"]:
        info("无自动绑定")
        return
    for e in data["bindings"]:
        namespace = e.get("resource_namespace")
        label = namespace[:12] if namespace else "旧版-需重新绑定"
        print(f"{label}  {e['scope']}  {e['command']}  →  {', '.join(e['ids'])}"
              f"  (last_used={e.get('last_used', '')}, hits={e.get('hits', 0)})")


def cmd_unbind(args) -> None:
    scope = str(Path(args.scope).resolve()) if args.scope else _project_scope()
    resource_namespace = None
    legacy = args.legacy
    if args.namespace:
        if not re.fullmatch(r"[0-9a-f]{12,64}", args.namespace):
            die("--namespace 必须是 bindings 输出中的至少 12 位小写十六进制前缀")
    elif not legacy:
        resource_namespace = resolve_config_snapshot(args.use_global_config).resource_namespace
    with _bindings_file_lock():
        data = _load_bindings()
        if args.namespace:
            matches = [entry for entry in data["bindings"] if (
                entry.get("scope") == scope
                and entry.get("command") == args.command_name
                and isinstance(entry.get("resource_namespace"), str)
                and entry["resource_namespace"].startswith(args.namespace)
            )]
        elif legacy:
            matches = [entry for entry in data["bindings"] if (
                entry.get("scope") == scope
                and entry.get("command") == args.command_name
                and not entry.get("resource_namespace")
            )]
        else:
            entry = _find_binding(data, scope, args.command_name, resource_namespace)
            matches = [entry] if entry is not None else []
        if not matches:
            die(f"无此绑定（项目={scope}, 命令={args.command_name}）。用 bindings 列出全部")
        if len(matches) > 1:
            die("匹配到多条绑定；请用 bindings 核对并提供更长的 --namespace 前缀")
        data["bindings"].remove(matches[0])
        _save_bindings(data)
    info(f"已解除绑定（项目={scope}, 命令={args.command_name}）")


def cmd_copy(args) -> None:
    _require_single_lookup(args, "copy")
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
    profile = args.lark_profile
    identity = _confirmed_profile_identity(profile, args.confirm_identity)
    profile_args = ["--profile", profile] if profile else []
    cmd = ["lark-cli", "base", "+base-create", "--as", "user", "--format", "json",
           *profile_args,
           "--name", args.base_name, "--table-name", "credentials",
           "--fields", json.dumps(FIELD_SCHEMA, ensure_ascii=False)]
    # 建 Base 是写操作：超时后可能已经建出一个 Base，重发会造出第二个（规则 4）
    proc = _lark_exec(cmd, "write", "+base-create")
    if proc.returncode != 0:
        die(f"+base-create 失败 (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        die(f"+base-create 返回非 JSON 输出（前 200 字符）：{proc.stdout[:200]}")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        die("+base-create 返回的 data 不是对象，无法取得新令牌表定位")
    base = payload["data"].get("base")
    table = payload["data"].get("table")
    if not isinstance(base, dict) or not isinstance(table, dict):
        die("+base-create 返回的 data.base/data.table 不是对象，无法取得新令牌表定位")
    app_token = _validate_resource_id(
        base.get("base_token") or base.get("app_token"),
        "+base-create 返回的 base_token/app_token",
    )
    table_id = _validate_resource_id(
        table.get("table_id") or table.get("id"),
        "+base-create 返回的 table_id/id",
    )
    print(proc.stdout.rstrip())
    confirmation = _identity_confirmation_token(identity)
    info("令牌表已创建。与用户确认配置名称和表定位后，运行下一行命令保存令牌配置：")
    print(_config_save_handoff(app_token, table_id, profile, confirmation))


def _config_save_handoff(app_token: str, table_id: str, profile: str,
                         confirmation: str) -> str:
    return shlex.join([
        "uv", "run", "--project", _SKILL_DIR,
        os.path.join(_SKILL_DIR, "scripts", "secret_book.py"),
        "config", "save", "--name", "<名称>",
        "--app-token", app_token,
        "--table-id", table_id,
        "--lark-profile", profile,
        "--confirm-identity", confirmation,
    ])


def cmd_init_adopt(args) -> None:
    profile = args.lark_profile
    identity = _confirmed_profile_identity(profile, args.confirm_identity)
    profile_args = ["--profile", profile] if profile else []
    proc = _lark_exec(["lark-cli", "base", "+url-resolve", "--as", "user",
                       "--format", "json", *profile_args, "--url", args.url],
                      "read", "+url-resolve")
    if proc.returncode != 0:
        die(f"+url-resolve 失败 (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        die(f"+url-resolve 返回非 JSON 输出（前 200 字符）：{proc.stdout[:200]}")
    if not isinstance(data, dict):
        die("+url-resolve 返回的 JSON 不是对象，无法解析令牌表地址")
    inner = data.get("data", data)
    if not isinstance(inner, dict):
        die("+url-resolve 返回的 data 不是对象，无法解析令牌表地址")
    app_token = inner.get("base_token") or inner.get("app_token")
    table_id = inner.get("table_id")
    block_id = inner.get("block_id")
    if not table_id and isinstance(block_id, str) and block_id.startswith("tbl"):
        table_id = block_id
    if not app_token or not table_id:
        die(f"无法从 URL 解析 base_token/table_id，+url-resolve 返回：{json.dumps(inner, ensure_ascii=False)[:300]}")
    app_token = _validate_resource_id(app_token, "+url-resolve 返回的 base_token/app_token")
    table_id = _validate_resource_id(table_id, "+url-resolve 返回的 table_id/block_id")
    backend = FeishuBackend(app_token, table_id, profile)
    listed = backend._run("+field-list", ["--limit", "200"])
    # +field-list 返回 data.fields = [{name, type, ...}]（实测 lark-cli 1.0.82）
    if "data" not in listed:
        die("+field-list 返回中缺少 data，无法校验令牌表字段")
    listed_data = listed["data"]
    if not isinstance(listed_data, dict):
        die("+field-list 返回的 data 不是对象，无法校验令牌表字段")
    if "fields" not in listed_data:
        die("+field-list 返回的 data 中缺少 fields，无法校验令牌表字段")
    fields = listed_data["fields"]
    if not isinstance(fields, list) or any(not isinstance(item, dict) for item in fields):
        die("+field-list 返回的 fields 不是字段对象数组，无法校验令牌表字段")
    existing = {}
    for index, item in enumerate(fields):
        name = item.get("name")
        field_type = item.get("type")
        if (not isinstance(name, str) or not name or name != name.strip()
                or any(ord(ch) < 32 for ch in name)
                or not isinstance(field_type, str) or not field_type
                or field_type != field_type.strip()
                or any(ord(ch) < 32 for ch in field_type)):
            die(f"+field-list 返回的 fields[{index}] 缺少有效 name/type，无法校验令牌表字段")
        if name in existing:
            die(f"令牌表存在重复字段名 {name}，无法安全接管")
        existing[name] = item

    # 先检查全部已有字段，再创建缺失字段，避免发现后置冲突前已经改动表结构。
    for spec in FIELD_SCHEMA:
        name, want = spec["name"], spec["type"]
        if name in existing and existing[name]["type"] != want:
            die(f"字段 {name} 类型不符：表内为 {existing[name]['type']}，需要 {want}。"
                "拒绝接管令牌表，请修正后重试")
        if name == "visible_to" and name in existing and existing[name].get("multiple") is not True:
            die("字段 visible_to 必须是人员多选字段；当前字段不是多选，拒绝接管令牌表")
    for spec in FIELD_SCHEMA:
        name, want = spec["name"], spec["type"]
        if name not in existing:
            backend._run("+field-create", ["--json", json.dumps(spec, ensure_ascii=False)])
            info(f"已补建缺失字段 {name} ({want})")
    confirmation = _identity_confirmation_token(identity)
    info("字段校验通过。与用户确认配置名称和表定位后，运行下一行命令保存令牌配置：")
    print(_config_save_handoff(app_token, table_id, profile, confirmation))


# ---------- agent-rule（多 Agent 全局指令文件的兜底规则块）----------

# v3（2026-09-03）：自动绑定加入令牌表身份，unbind 必须选择全局当前配置的命名空间。
# 递增版本号是必须的——不递增的话已安装 v1 的文件会被判成「手工改动」而跳过更新。
RULE_VERSION = 3
RULE_BEGIN = re.compile(r"<!-- secret-book:fallback-rule v(\d+) -->")
RULE_END = "<!-- /secret-book:fallback-rule -->"

# (key, 名称, 检测目录, 目标文件, 模式)。模式 block=共享文件里追加哨兵块；
# file=独立文件整份归本 skill；manual=无全局文件（不可脚本写入），输出手动指引。
# 各路径均经官方文档逐家查证，来源清单见外层仓
# docs/deliverables/secret-book/agent-rule-and-bindings.md（2026-08-10）。
AGENT_TARGETS = [
    ("claude-code", "Claude Code", "~/.claude", "~/.claude/CLAUDE.md", "block"),
    ("codex", "Codex CLI", "~/.codex", "~/.codex/AGENTS.md", "block"),
    ("gemini", "Gemini CLI", "~/.gemini", "~/.gemini/GEMINI.md", "block"),
    ("opencode", "OpenCode", "~/.config/opencode", "~/.config/opencode/AGENTS.md", "block"),
    ("qwen", "Qwen Code", "~/.qwen", "~/.qwen/QWEN.md", "block"),
    ("iflow", "iFlow CLI", "~/.iflow", "~/.iflow/IFLOW.md", "block"),
    ("amp", "Amp", "~/.config/amp", "~/.config/amp/AGENTS.md", "block"),
    ("windsurf", "Windsurf", "~/.codeium/windsurf",
     "~/.codeium/windsurf/memories/global_rules.md", "block"),
    ("cline", "Cline", "~/Documents/Cline", "~/Documents/Cline/Rules/secret-book.md", "file"),
    ("copilot", "Copilot CLI", "~/.copilot",
     "~/.copilot/instructions/secret-book.instructions.md", "file"),
    ("goose", "Goose", "~/.config/goose", "~/.config/goose/AGENTS.md", "block"),
    ("cursor", "Cursor", "~/.cursor", None, "manual"),
]
WINDSURF_CHAR_LIMIT = 6000  # global_rules.md 官方字符上限


def _is_inside_worktree(path: str) -> bool:
    """路径是否落在 `<repo>/.worktrees/<name>/` 这类临时检出里。

    判据只看路径分量里有没有 `.worktrees`——这是本工作区 worktree 的固定落点
    （见工作区 CLAUDE.md「Worktree 创建已由 WorktreeCreate hook 接管」）。粗，
    但足够：装规则是低频的一次性动作，宁可在极少数同名目录上多问一句，也不能
    把随时会被删除的路径写进各 agent 的**全局**指令文件。
    """
    return ".worktrees" in os.path.realpath(path).split(os.sep)


def rule_block() -> str:
    # realpath：入口通常是 ~/.claude/skills/secret-book 这类 symlink，解析到实体
    # 才是各 agent 长期可用的稳定路径。
    script = os.path.realpath(__file__)
    project = os.path.dirname(os.path.dirname(script))  # pyproject.toml 所在目录
    # shlex.quote：路径含空格或 shell 元字符时，规则块里的命令会被 agent 原样
    # 复制执行，未引用的 `--project /Users/me/My Skills/secret-book` 会被切成两个
    # 参数（与 bootstrap 的 _bootstrap_manual_hint 同一条理由）。安全路径下
    # quote 原样返回，已装好的规则块文本不受影响，因此不需要 bump RULE_VERSION。
    run = f"uv run --project {shlex.quote(project)} {shlex.quote(script)}"
    return f"""<!-- secret-book:fallback-rule v{RULE_VERSION} -->
## secret-book 令牌兜底
命令或 skill 因缺少令牌/API key/token/凭证配置而失败时：
1. 先试 `{run} run --auto --use-global-config -- <原命令>`；命中绑定即注入重试，退出码 3 = 无绑定。
2. 无绑定则 `{run} list --use-global-config` 查元数据按意图匹配：唯一命中 → `run --id <id> --bind --use-global-config -- <原命令>`（成功自动记住绑定）；多条候选或无命中 → 列给用户选择，禁止自选。
3. 注入后仍鉴权失败 → `unbind --command <命令名> --use-global-config` 解除当前令牌配置下的绑定后重新匹配，禁止重试同一绑定。
4. MCP server 缺配置无法注入已运行进程：用 `copy` 取值引导用户配置后重启会话。令牌值一律不上屏。
{RULE_END}"""


def _inspect_target(text: str) -> tuple[str, tuple[int, int] | None]:
    """返回 (状态, 规则块在文本中的位置)。状态：missing/current/outdated/modified。"""
    m = RULE_BEGIN.search(text)
    if not m:
        return "missing", None
    end = text.find(RULE_END, m.start())
    if end < 0:  # 起始哨兵在、结束哨兵丢了：按手工改动处理，不自动覆盖
        return "modified", (m.start(), len(text))
    span = (m.start(), end + len(RULE_END))
    if int(m.group(1)) < RULE_VERSION:
        return "outdated", span
    return ("current" if text[span[0]:span[1]] == rule_block() else "modified"), span


def cmd_agent_rule(args) -> None:
    only = set(args.agent or [])
    known = {k for k, *_ in AGENT_TARGETS}
    if only - known:
        die(f"未知 agent key：{', '.join(sorted(only - known))}（可用：{', '.join(sorted(known))}）")
    action = "install" if args.install else ("remove" if args.remove else "check")
    # 规则块里写的是本脚本的绝对路径，会被装进各 agent 的**全局**指令文件、长期
    # 生效。从 worktree 里装，写进去的就是一条 worktree 删除后即失效的路径——
    # 症状是"以后每次缺令牌兜底都报文件不存在"，且发生在别的会话里，没人会联想
    # 到当初是在哪装的。因此安装一律拒绝，让用户回主检出执行；check / remove 不
    # 写路径，只提醒（check 还会因路径不同把已装的块误报成 modified）。
    if _is_inside_worktree(__file__):
        hint = ("当前脚本在 worktree 检出内运行"
                f"（{os.path.realpath(__file__)}）。规则块会把这个路径写进各 agent "
                "的全局指令文件，worktree 一删就永久失效。")
        if action == "install":
            die(hint + "请到主检出（非 .worktrees/ 下）再执行 agent-rule --install")
        warn(hint + "下方状态仅供参考：路径不同会把已装好的规则块显示成 modified")
    for key, label, detect, target, mode in AGENT_TARGETS:
        if only and key not in only:
            continue
        tag = f"{key:12} {label:12}"
        if not Path(detect).expanduser().is_dir():
            print(f"{tag} 未检测到（{detect} 不存在），跳过")
            continue
        if mode == "manual":
            if action == "install":
                print(f"{tag} 无全局指令文件（User Rules 存 IDE 设置内），"
                      "请手动把下面的规则粘贴进 Cursor Settings → Rules：")
                print(rule_block())
            else:
                print(f"{tag} 无全局指令文件，不可脚本写入（如已手动粘贴请手动维护）")
            continue
        path = Path(target).expanduser()
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        status, span = _inspect_target(text)
        if action == "check":
            print(f"{tag} {status:9} {path}")
            continue
        if action == "remove":
            if status == "missing":
                print(f"{tag} 本就未安装，跳过")
                continue
            remaining = (text[:span[0]] + text[span[1]:]).strip("\n")
            if mode == "file" and not remaining.strip():
                path.unlink()
                print(f"{tag} 已删除 {path}")
            else:
                path.write_text(remaining + ("\n" if remaining else ""), encoding="utf-8")
                print(f"{tag} 已移除规则块：{path}")
            continue
        # install
        if status == "current":
            print(f"{tag} 已是最新，跳过")
            continue
        if status == "modified" and not args.force:
            print(f"{tag} 规则块有手工改动，跳过（--force 覆盖）：{path}")
            continue
        block = rule_block()
        if span:
            new_text = text[:span[0]] + block + text[span[1]:]
        else:
            new_text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block + "\n"
        if key == "windsurf" and len(new_text) > WINDSURF_CHAR_LIMIT:
            print(f"{tag} 跳过：写入后 {len(new_text)} 字符超过 {WINDSURF_CHAR_LIMIT} 上限，"
                  f"请手工精简 {path} 后重试")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
        print(f"{tag} {'已安装' if status == 'missing' else '已更新'}：{path}")


# ---------- CLI ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=SKILL_NAME, description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="action", required=True)

    def profile_flag(sp, *, required=False, with_confirmation=False):
        sp.add_argument("--lark-profile", dest="lark_profile",
                        required=required,
                        help="访问令牌表所用的 lark-cli profile 名；不会修改 active profile")
        if with_confirmation:
            sp.add_argument("--confirm-identity",
                            help="确认前一次调用捕获到的飞书身份；身份变化时必须重新确认")

    def common(sp, with_lookup: bool = False):
        sp.add_argument("--use-global-config", action="store_true",
                        help=f"启用第 4 层配置 {global_config_path()}（ADR 0003 要求显式 flag）")
        if with_lookup:
            sp.add_argument("--name", help="按令牌记录名称定位")
            sp.add_argument("--id", action="append", help="按机器键 sec_xxx 定位记录，可重复")

    config = sub.add_parser("config", help="管理本机保存的多套令牌配置")
    config_sub = config.add_subparsers(dest="config_action", required=True)

    sp = config_sub.add_parser("save", help="保存一套有名称的令牌配置")
    sp.add_argument("--name", required=True, help="令牌配置名称，例如 工作、个人")
    sp.add_argument("--app-token", required=True)
    sp.add_argument("--table-id", required=True)
    sp.add_argument("--lark-profile", required=True,
                    help="访问这张令牌表所用的 lark-cli profile")
    sp.add_argument("--confirm-identity",
                    help="确认前一次调用捕获到的飞书身份；身份变化时必须重新确认")
    sp.set_defaults(func=cmd_config_save)

    sp = config_sub.add_parser("list", help="列出令牌配置及唯一的当前配置")
    sp.set_defaults(func=cmd_config_list)

    sp = config_sub.add_parser("use", help="按名称持久切换当前配置")
    sp.add_argument("--name", required=True, help="要设为当前配置的令牌配置名称")
    sp.set_defaults(func=cmd_config_use)

    sp = config_sub.add_parser("rebind", help="更新令牌配置绑定的 lark-cli profile 和身份固定值")
    sp.add_argument("--name", required=True, help="要更新的令牌配置名称")
    sp.add_argument("--lark-profile", required=True,
                    help="重新绑定后访问令牌表所用的 lark-cli profile")
    sp.add_argument("--confirm-identity",
                    help="确认前一次调用捕获到的飞书身份；身份变化时必须重新确认")
    sp.set_defaults(func=cmd_config_rebind)

    sp = config_sub.add_parser("rename", help="重命名令牌配置，稳定 ID 保持不变")
    sp.add_argument("--name", required=True, help="当前名称")
    sp.add_argument("--new-name", required=True, help="新名称")
    sp.set_defaults(func=cmd_config_rename)

    sp = config_sub.add_parser("remove", help="删除令牌配置")
    sp.add_argument("--name", required=True, help="要删除的令牌配置名称")
    sp.set_defaults(func=cmd_config_remove)

    sp = config_sub.add_parser(
        "migrate",
        help="迁移旧版全局平面配置，或清理与命名配置并存的旧字段",
    )
    sp.add_argument("--name", required=True,
                    help="迁移后的名称；清理混合格式时填写一个现有配置名称")
    sp.add_argument("--lark-profile",
                    help=f"旧配置缺少 {ENV_LARK_PROFILE} 时，明确指定要绑定的 profile")
    sp.add_argument("--confirm-identity",
                    help="确认前一次调用捕获到的飞书身份；身份变化时必须重新确认")
    sp.set_defaults(func=cmd_config_migrate)

    sp = sub.add_parser("save", help="从 stdin 读 dotenv payload 保存新令牌记录",
                        description="从 stdin 读取 dotenv payload，并保存为一条新令牌记录。")
    sp.add_argument("--name", required=True)
    sp.add_argument("--service", required=True)
    sp.add_argument("--purpose", required=True)
    sp.add_argument("--account")
    sp.add_argument("--expires-at", dest="expires_at", help="YYYY-MM-DD")
    sp.add_argument("--notes")
    common(sp)
    sp.set_defaults(func=cmd_save)

    sp = sub.add_parser("list", help="列出令牌记录（仅元数据，不含 secret/notes）",
                        description="列出当前令牌表中的令牌记录元数据，不读取 secret/notes。")
    common(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("get", help="查单条元数据与键名（不回显值）")
    common(sp, with_lookup=True)
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("run", help="键值对注入子进程环境后执行命令")
    common(sp, with_lookup=True)
    sp.add_argument("--auto", action="store_true",
                    help="按 (项目根, 命令名) 查历史绑定注入；无绑定退出码 3")
    sp.add_argument("--bind", action="store_true",
                    help="子进程退出码 0 时把本次记录绑定到 (项目根, 命令名)")
    sp.add_argument("command", nargs=argparse.REMAINDER,
                    help="'-- <命令及参数>'")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("bindings", help="列出全部自动绑定（bindings.json）")
    sp.set_defaults(func=cmd_bindings)

    sp = sub.add_parser("unbind", help="解除一条自动绑定")
    sp.add_argument("--command", dest="command_name", required=True, help="命令名（basename）")
    sp.add_argument("--scope", help="项目根路径，默认当前项目")
    selector = sp.add_mutually_exclusive_group()
    selector.add_argument("--use-global-config", action="store_true",
                          help="启用全局当前配置层；高优先级项目配置仍优先")
    selector.add_argument("--namespace",
                          help="按 bindings 输出的 namespace 前缀解除不可再由配置引用的绑定")
    selector.add_argument("--legacy", action="store_true",
                          help="解除没有 namespace 的 v1 旧绑定")
    sp.set_defaults(func=cmd_unbind)

    sp = sub.add_parser("agent-rule",
                        help="检查/安装/移除各 agent 全局指令文件里的缺配置兜底规则块")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true")
    g.add_argument("--remove", action="store_true")
    sp.add_argument("--agent", action="append", help="只处理指定 agent key，可重复")
    sp.add_argument("--force", action="store_true", help="覆盖有手工改动的规则块")
    sp.set_defaults(func=cmd_agent_rule)

    sp = sub.add_parser("copy", help="单个值写入剪贴板（多键记录需 --key）")
    common(sp, with_lookup=True)
    sp.add_argument("--key")
    sp.set_defaults(func=cmd_copy)

    sp = sub.add_parser("init-create", help="新建令牌表 Base（credentials 表 + 9 字段）")
    sp.add_argument("--base-name", default="令牌表")
    profile_flag(sp, required=True, with_confirmation=True)
    sp.set_defaults(func=cmd_init_create)

    sp = sub.add_parser("init-adopt", help="接管用户自备令牌表：校验字段，缺列补建，类型不符报错")
    sp.add_argument("--url", required=True)
    profile_flag(sp, required=True, with_confirmation=True)
    sp.set_defaults(func=cmd_init_adopt)

    return p


def main() -> None:
    args = build_parser().parse_args()
    if getattr(args, "command", None) and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    try:
        args.func(args)
    except ProfileGuidance as exc:
        print(json.dumps(exc.payload, ensure_ascii=False, sort_keys=True))
        raise SystemExit(EXIT_GUIDANCE)
    except BindingsFileError as exc:
        die(str(exc))
    except LocalWriteResultUnknown as exc:
        die(str(exc))
    except OSError as exc:
        die(f"本地文件写入失败：{exc}")


if __name__ == "__main__":
    main()
