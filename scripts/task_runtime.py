"""Bind a task journal to existing CLI actions without changing their payloads."""
import os
import platform
from pathlib import Path

from task_state import TaskStateError, fingerprint


def context(core, agent):
    if not agent or not agent.strip():
        raise TaskStateError("agent_required", "需要由实际会话明确当前宿主")
    return {"agent": agent, "cwd": os.path.normcase(str(Path.cwd().resolve())),
            "platform": platform.system() + ":" + platform.release(),
            "installation": os.path.normcase(str(Path(core._SKILL_DIR).resolve()))}


def allowed_roles(core, args):
    connected = {"administrator", "maintainer", "consumer"}
    if args.func in (core.cmd_init_create, core.cmd_init_adopt):
        return {"administrator"}
    if args.func in (core.cmd_save, core._repair_one_record_id):
        return {"maintainer"}
    if args.func in (core.cmd_run, core.cmd_copy):
        return {"consumer"}
    if args.func == core.cmd_consumer:
        return {"consumer"} if args.action == "configure" else None
    if args.func in (core.cmd_list, core.cmd_get, core._connect_existing_table,
                     core.cmd_config_save, core.cmd_config_rebind, core.cmd_config_migrate):
        return connected
    return None  # local inventory/config selection/rule inspection


def execute(core, args, journal, task_id, agent):
    current = context(core, agent)
    entry = journal.status(current, task_id)
    if entry["closed"]:
        raise TaskStateError("task_closed", "任务已经关闭；新任务重新确定角色")
    allowed = allowed_roles(core, args)
    if allowed is not None and entry["role"] not in allowed:
        raise TaskStateError("role_mismatch", "当前任务角色不能执行该动作；先明确相应职责并开始对应任务")
    if args.action == "configure" and args.agent != agent:
        raise TaskStateError("context_changed", "业务配置检查宿主与当前任务宿主不一致")
    # Diagnostics must not erase the pending initialization they help resolve.
    diagnostic = args.func in (core.cmd_list, core.cmd_get, core.cmd_bindings, core.cmd_config_list)
    diagnostic |= args.func == core.cmd_consumer and args.action == "configure-status"
    diagnostic |= (args.func == core.cmd_agent_rule
                   and not getattr(args, "install", False) and not getattr(args, "remove", False))
    if diagnostic:
        args.func(args)
        return
    parameters = {key: value for key, value in vars(args).items()
                  if key not in {"func", "confirm", "confirm_identity", "workflow", "workflow_agent"}
                  and not key.startswith("_")}
    action = args.action + (" " + args.config_action if args.action == "config" else "")
    target = {key: getattr(args, key) for key in ("url", "base_name", "app_token", "table_id", "config_name")
              if getattr(args, key, None)}
    if getattr(args, "lark_profile", None):
        target["profile"] = args.lark_profile
    if getattr(args, "name", None):
        target["config_name" if args.action == "config" else "record_name"] = args.name
    session = journal.begin(task_id, current, action, parameters, target)
    pending = session.previous_pending
    for flag in ("confirm_identity", "confirm"):
        if not getattr(args, flag, None):
            continue
        if (pending and pending["operation"] == session.operation
                and getattr(args, flag) == pending.get("confirmation_token")):
            setattr(args, flag, pending.get("source_confirmation_token", getattr(args, flag)))
        elif (flag == "confirm_identity" and not pending
              and entry["target"].get("app_id") and entry["target"].get("open_id")
              and args.func == core.cmd_config_save):
            pass  # Same task's successful table check supplies a reusable identity handoff.
        else:
            setattr(args, flag, None)
    core._ACTIVE_TASK = session
    try:
        args.func(args)
    except core.ProfileGuidance as exc:
        guidance = dict(exc.payload)
        if isinstance(guidance.get("confirmation_token"), str):
            guidance["source_confirmation_token"] = guidance["confirmation_token"]
            guidance["confirmation_token"] = fingerprint({"task": task_id,
                "operation": session.operation, "source": guidance["confirmation_token"]})
            exc.payload["confirmation_token"] = guidance["confirmation_token"]
        session.waiting(guidance)
        exc.payload["task_id"] = task_id
        raise
    except SystemExit as exc:
        if exc.code in (None, 0):
            session.succeeded()
        else:
            session.failed()
        raise
    except BaseException:
        session.failed()
        raise
    else:
        session.succeeded()
    finally:
        core._ACTIVE_TASK = None
