#!/usr/bin/env python3
"""secret-book — 凭证台账 CLI（飞书多维表格后端）。

明文台账 + agent 取用通道：本脚本负责查表、注入、掩码；不做加密。
安全声明见仓库 README 与 SKILL.md 第一屏。

架构约束（design.md §6）：所有表格 CRUD 必须走 Backend 抽象；业务逻辑不得
直接拼 lark-cli 命令。Notion 后端（v1.1）只需新增一个 Backend 实现。

掩码纪律：凭证值只流向子进程环境（run）或剪贴板（copy），不打印、不进
错误消息；本进程输出中的凭证值一律经 _scrub() 掩码。

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
    # 本 skill 的 pyproject 目前没有 dependency-groups，写法仍与工作区统一——
    # 将来加了 dev 组不必再回头改这里，也不会有人照抄成"重建顺带装 pytest"。
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
import datetime
import json
import re
import secrets
import string
import time
from pathlib import Path

SKILL_NAME = "secret-book"
ENV_APP_TOKEN = "SECRET_BOOK_APP_TOKEN"
ENV_TABLE_ID = "SECRET_BOOK_TABLE_ID"
ENV_IDS = "SECRET_BOOK_IDS"
ENV_LARK_PROFILE = "SECRET_BOOK_LARK_PROFILE"

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

_SENSITIVE: list[str] = []  # 运行期收集到的凭证值，用于掩码任何将要打印的文本


# ---------- 通用工具 ----------

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


def load_config(use_global: bool) -> dict:
    """每个变量独立 first-found-wins：进程环境 → $PWD/.env.local → $PWD/.env
    → （仅 --use-global-config 时）~/.config/secret-book/.env。"""
    layers = [
        {k: v for k, v in os.environ.items() if k.startswith("SECRET_BOOK_")},
        _parse_env_file(Path.cwd() / ".env.local"),
        _parse_env_file(Path.cwd() / ".env"),
    ]
    if use_global:
        layers.append(_parse_env_file(global_config_path()))
    merged: dict = {}
    for key in (ENV_APP_TOKEN, ENV_TABLE_ID, ENV_IDS, ENV_LARK_PROFILE):
        for layer in layers:
            if layer.get(key):
                merged[key] = layer[key]
                break
    return merged


def global_config_path() -> Path:
    return Path.home() / ".config" / SKILL_NAME / ".env"


def resolve_profile(args) -> str:
    """台账所在租户对应的 lark-cli profile 名。`--lark-profile` 高于配置分层
    （init-create / init-adopt 在配置写入之前执行，只能靠 flag 指定租户）。
    空 = 不传 --profile，沿用 lark-cli 当前 active profile。"""
    flag = getattr(args, "lark_profile", None)
    if flag:
        return flag
    return load_config(getattr(args, "use_global_config", False)).get(ENV_LARK_PROFILE, "")


def require_backend(args) -> "FeishuBackend":
    cfg = load_config(args.use_global_config)
    app_token, table_id = cfg.get(ENV_APP_TOKEN), cfg.get(ENV_TABLE_ID)
    if not app_token or not table_id:
        hint = "" if args.use_global_config else "（未启用全局配置；若已 init，请加 --use-global-config）"
        die(f"缺少 {ENV_APP_TOKEN} / {ENV_TABLE_ID} 配置{hint}。先运行 init-create 或 init-adopt。")
    return FeishuBackend(app_token, table_id, resolve_profile(args))


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
# libcurl），调用方无从判断是"台账写入结果不明"还是"curl 缺特性"。
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
# 写 = 会改台账表结构或内容。飞书 Base 这几个接口没有幂等键，本 skill 也没有
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


# ---------- 后端适配器 ----------

class FeishuBackend:
    """飞书多维表格后端，经 lark-cli。所有方法只吞吐 {字段名: 字符串} 平面记录。"""

    _open_id_cache: dict = {}  # profile 名 → open_id，一个 profile 只查一次 auth status

    def __init__(self, app_token: str, table_id: str, profile: str = ""):
        self.app_token = app_token
        self.table_id = table_id
        # 空 = 不传 --profile，沿用 lark-cli 当前 active profile
        self.profile = profile or ""

    def _profile_args(self) -> list:
        return ["--profile", self.profile] if self.profile else []

    def _profile_note(self) -> str:
        return f"（profile={self.profile}）" if self.profile else "（未指定 profile，用的是 lark-cli 当前 active profile）"

    def current_user_open_id(self) -> str:
        """当前 lark-cli user 身份的 open_id，visible_to 过滤的比对基准。
        取不到时 die（fail-closed）：不能确定「我是谁」就不放行受限记录。"""
        if self.profile not in FeishuBackend._open_id_cache:
            proc = _lark_exec(["lark-cli", "auth", "status", *self._profile_args()],
                              "read", "auth status")
            open_id = ""
            try:
                open_id = json.loads(proc.stdout)["identities"]["user"]["openId"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            if proc.returncode != 0 or not open_id:
                die(f"无法从 lark-cli auth status 获取当前用户 open_id{self._profile_note()}，"
                    "无法执行 visible_to 可见范围过滤，拒绝继续。"
                    "请确认该 profile 存在且已完成 user 身份登录（lark-cli profile list）")
            FeishuBackend._open_id_cache[self.profile] = open_id
        return FeishuBackend._open_id_cache[self.profile]

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
        # 人员单元格 = [{"id": "ou_xxx", "name": "..."}]，空为 null；缺列（存量
        # 8 列表）投影被忽略、取值 None，等价于空 = 不受限（实测 lark-cli 1.0.82）
        cell = fields.get("visible_to")
        users = [u for u in cell if isinstance(u, dict)] if isinstance(cell, list) else []
        rec["_visible_to_ids"] = [str(u.get("id", "")) for u in users]
        rec["visible_to"] = ", ".join(u.get("name") or u.get("id", "") for u in users)
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
    """按 --id / --name / SECRET_BOOK_IDS 的顺序解析目标记录。"""
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


# ---------- 自动绑定（bindings.json）----------
# (项目根, 命令名) → 记录 id 列表。只存元数据不存值；本机私有，不同步进台账
# （里面是本机路径，跨机器诉求走项目 .env.local 的 SECRET_BOOK_IDS 显式绑定）。

def bindings_path() -> Path:
    return Path.home() / ".config" / SKILL_NAME / "bindings.json"


def _load_bindings() -> dict:
    path = bindings_path()
    if not path.is_file():
        return {"version": 1, "bindings": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        die(f"{path} 不是合法 JSON，请检查或删除该文件后重试")
    data.setdefault("bindings", [])
    return data


def _save_bindings(data: dict) -> None:
    path = bindings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)  # 原子替换；并发写 last-writer-wins（元数据缓存可接受）


def _project_scope() -> str:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    root = proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else str(Path.cwd())
    return str(Path(root).resolve())


def _find_binding(data: dict, scope: str, command: str) -> dict | None:
    for entry in data["bindings"]:
        if entry.get("scope") == scope and entry.get("command") == command:
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
    """run --auto 的解析：项目显式绑定 SECRET_BOOK_IDS 优先于 bindings.json；
    无绑定或绑定失效以退出码 3 结束（agent 据此转入 list+意图匹配流程）。"""
    cfg = load_config(args.use_global_config)
    if cfg.get(ENV_IDS):
        info(f"使用项目显式绑定 {ENV_IDS}={cfg[ENV_IDS]}")
        return resolve_records(backend, args, need_secret=True)
    scope, cmdname = _project_scope(), Path(args.command[0]).name
    data = _load_bindings()
    entry = _find_binding(data, scope, cmdname)
    if entry is None:
        info(f"无绑定（项目={scope}, 命令={cmdname}）。请走 list+意图匹配流程，"
             "匹配成功后用 run --bind 建立绑定")
        sys.exit(3)
    records, stale = [], []
    for sid in entry["ids"]:
        matches = backend.find("id", sid, with_secret=True)
        records.extend(matches) if matches else stale.append(sid)
    if stale:
        data["bindings"].remove(entry)
        _save_bindings(data)
        info(f"绑定失效：记录 {', '.join(stale)} 已不在台账，绑定已自动解除。请重新匹配")
        sys.exit(3)
    for rec in records:
        parse_payload(rec["secret"])  # 把值登记进 _SENSITIVE
    entry["last_used"], entry["hits"] = _now(), entry.get("hits", 0) + 1
    _save_bindings(data)
    info("按绑定使用 " + ", ".join(
        f"{r['id']}（{r['name']}, service={r['service']}）" for r in records))
    return records


# ---------- 动作 ----------

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
    for key in META_FIELDS + ["visible_to", "notes"]:
        print(f"{key}: {rec.get(key, '')}")
    print(f"keys: {', '.join(parse_payload(rec['secret']))}")


def cmd_run(args) -> None:
    if not args.command:
        die("run 需要 '-- <命令>'")
    if args.auto and (args.name or args.id):
        die("--auto 与 --name/--id 互斥：绑定查找与显式指定二选一")
    backend = require_backend(args)
    binding_to_save = None
    if args.auto:
        records = _resolve_auto(backend, args)
    else:
        records = resolve_records(backend, args, need_secret=True)
        if args.bind:
            binding_to_save = {"scope": _project_scope(),
                               "command": Path(args.command[0]).name,
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
        data = _load_bindings()
        existing = _find_binding(data, binding_to_save["scope"], binding_to_save["command"])
        if existing:
            data["bindings"].remove(existing)
        binding_to_save.update({"created": (existing or {}).get("created", _now()),
                                "last_used": _now(),
                                "hits": (existing or {}).get("hits", 0) + 1})
        data["bindings"].append(binding_to_save)
        _save_bindings(data)
        info(f"已绑定（项目={binding_to_save['scope']}, 命令={binding_to_save['command']}）"
             f"→ {', '.join(binding_to_save['ids'])}；下次 run --auto 直用，unbind 可解除")
    sys.exit(proc.returncode)


def cmd_bindings(args) -> None:
    data = _load_bindings()
    if not data["bindings"]:
        info("无自动绑定")
        return
    for e in data["bindings"]:
        print(f"{e['scope']}  {e['command']}  →  {', '.join(e['ids'])}"
              f"  (last_used={e.get('last_used', '')}, hits={e.get('hits', 0)})")


def cmd_unbind(args) -> None:
    scope = str(Path(args.scope).resolve()) if args.scope else _project_scope()
    data = _load_bindings()
    entry = _find_binding(data, scope, args.command_name)
    if entry is None:
        die(f"无此绑定（项目={scope}, 命令={args.command_name}）。用 bindings 列出全部")
    data["bindings"].remove(entry)
    _save_bindings(data)
    info(f"已解除绑定（项目={scope}, 命令={args.command_name}）")


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
    profile = resolve_profile(args)
    profile_args = ["--profile", profile] if profile else []
    cmd = ["lark-cli", "base", "+base-create", "--as", "user", "--format", "json",
           *profile_args,
           "--name", args.base_name, "--table-name", "credentials",
           "--fields", json.dumps(FIELD_SCHEMA, ensure_ascii=False)]
    # 建 Base 是写操作：超时后可能已经建出一个 Base，重发会造出第二个（规则 4）
    proc = _lark_exec(cmd, "write", "+base-create")
    if proc.returncode != 0:
        die(f"+base-create 失败 (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    print(proc.stdout)
    hint = f" --lark-profile {profile}" if profile else ""
    info("请从上方返回中提取 base token（app_token）与 credentials 表的 table_id，"
         f"与用户确认后运行 config-write{hint} 写入全局配置")


def cmd_init_adopt(args) -> None:
    profile = resolve_profile(args)
    profile_args = ["--profile", profile] if profile else []
    proc = _lark_exec(["lark-cli", "base", "+url-resolve", "--as", "user",
                       "--format", "json", *profile_args, "--url", args.url],
                      "read", "+url-resolve")
    if proc.returncode != 0:
        die(f"+url-resolve 失败 (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    data = json.loads(proc.stdout)
    inner = data.get("data", data)
    app_token = inner.get("base_token") or inner.get("app_token")
    table_id = inner.get("table_id")
    if not app_token or not table_id:
        die(f"无法从 URL 解析 base_token/table_id，+url-resolve 返回：{json.dumps(inner, ensure_ascii=False)[:300]}")
    backend = FeishuBackend(app_token, table_id, profile)
    listed = backend._run("+field-list", ["--limit", "200"])
    # +field-list 返回 data.fields = [{name, type, ...}]（实测 lark-cli 1.0.82）
    existing = {str(it.get("name", "")): str(it.get("type", ""))
                for it in listed.get("data", {}).get("fields", [])}
    for spec in FIELD_SCHEMA:
        name, want = spec["name"], spec["type"]
        if name not in existing:
            backend._run("+field-create", ["--json", json.dumps(spec, ensure_ascii=False)])
            info(f"已补建缺失字段 {name} ({want})")
        elif str(existing[name]) not in (want, ""):
            die(f"字段 {name} 类型不符：表内为 {existing[name]}，需要 {want}。拒绝接管脏表（design.md §5），请修正后重试")
    hint = f" --lark-profile {profile}" if profile else ""
    info(f"字段校验通过。与用户确认后运行 config-write --app-token {app_token} --table-id {table_id}{hint}")


def cmd_config_write(args) -> None:
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
    if args.lark_profile:
        existing[ENV_LARK_PROFILE] = args.lark_profile
    kept = [f"{k}={v}" for k, v in existing.items()]
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
    target.chmod(0o600)
    info(f"配置已写入 {target}")
    if args.lark_profile:
        info(f"台账固定使用 lark-cli profile：{args.lark_profile}（不再受 active profile 切换影响）")
    else:
        info(f"未写入 {ENV_LARK_PROFILE}：台账将沿用 lark-cli 当前 active profile，"
             "别的任务切换 profile 后本 skill 会打到另一个租户。"
             "要固定租户请带 --lark-profile <name> 重跑")
    info("提示：可用 agent-rule 检查/安装「缺配置兜底」规则到各 agent 的全局指令文件"
         "（安装前须把目标文件与规则全文给用户确认）")


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


# ---------- agent-rule（多 Agent 全局指令文件的兜底规则块）----------

# v2（2026-08-18）：命令改为 `uv run --project`，裸 python3 会用错解释器（ADR 0007）。
# 递增版本号是必须的——不递增的话已安装 v1 的文件会被判成「手工改动」而跳过更新。
RULE_VERSION = 2
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
## secret-book 凭证兜底
命令或 skill 因缺少凭证/API key/token 配置而失败时：
1. 先试 `{run} run --auto --use-global-config -- <原命令>`；命中绑定即注入重试，退出码 3 = 无绑定。
2. 无绑定则 `{run} list --use-global-config` 查元数据按意图匹配：唯一命中 → `run --id <id> --bind --use-global-config -- <原命令>`（成功自动记住绑定）；多条候选或无命中 → 列给用户选择，禁止自选。
3. 注入后仍鉴权失败 → `unbind --command <命令名>` 解除绑定后重新匹配，禁止重试同一绑定。
4. MCP server 缺配置无法注入已运行进程：用 `copy` 取值引导用户配置后重启会话。凭证值一律不上屏。
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
    # 症状是"以后每次缺凭证兜底都报文件不存在"，且发生在别的会话里，没人会联想
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

    def profile_flag(sp):
        sp.add_argument("--lark-profile", dest="lark_profile",
                        help=f"台账所在租户的 lark-cli profile 名（覆盖配置 {ENV_LARK_PROFILE}）；"
                             "省略且未配置时沿用 lark-cli 当前 active profile")

    def common(sp, with_lookup: bool = False):
        sp.add_argument("--use-global-config", action="store_true",
                        help=f"启用第 4 层配置 {global_config_path()}（ADR 0003 要求显式 flag）")
        profile_flag(sp)
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

    sp = sub.add_parser("init-create", help="新建台账 Base（credentials 表 + 9 字段）")
    sp.add_argument("--base-name", default="凭证台账")
    profile_flag(sp)
    sp.set_defaults(func=cmd_init_create)

    sp = sub.add_parser("init-adopt", help="接管用户自备表：校验字段，缺列补建，类型不符报错")
    sp.add_argument("--url", required=True)
    profile_flag(sp)
    sp.set_defaults(func=cmd_init_adopt)

    sp = sub.add_parser("config-write",
                        help="写入 app_token/table_id/lark_profile 配置（默认全局，--project 写 $PWD/.env.local）")
    sp.add_argument("--app-token", required=True)
    sp.add_argument("--table-id", required=True)
    sp.add_argument("--lark-profile", dest="lark_profile",
                    help=f"写入 {ENV_LARK_PROFILE}：台账所在租户的 lark-cli profile 名")
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
