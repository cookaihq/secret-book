#!/usr/bin/env python3
"""Small process-level stand-in for the lark-cli boundary used by CLI tests."""

import json
import os
import sys
from pathlib import Path


DEFAULT_FIELDS = [
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


def _state():
    path = os.environ.get("FAKE_LARK_STATE")
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}


def _profile(argv):
    try:
        return argv[argv.index("--profile") + 1]
    except (ValueError, IndexError):
        return ""


def _arg(argv, flag, default=""):
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return default


def main():
    argv = sys.argv[1:]
    log_path = os.environ.get("FAKE_LARK_LOG")
    if log_path:
        with Path(log_path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv, ensure_ascii=False) + "\n")

    if os.environ.get("FAKE_LARK_FAIL_ON_CALL") == "1":
        print("fake lark-cli was not expected to run", file=sys.stderr)
        return 97

    state = _state()
    if argv[:2] == ["profile", "list"]:
        profile_exit = state.get("profile_exit")
        if profile_exit:
            print(json.dumps({
                "ok": False,
                "error": {"type": "config", "message": "cannot read profiles"},
            }), file=sys.stderr)
            return profile_exit
        print(json.dumps(state.get("profiles", []), ensure_ascii=False))
        return 0
    if argv[:2] == ["auth", "status"]:
        profile = _profile(argv)
        auth_exit = state.get("auth_exit", {}).get(profile)
        if auth_exit:
            print(json.dumps({
                "ok": False,
                "error": {"type": "config", "message": "profile is not authenticated"},
            }), file=sys.stderr)
            return auth_exit
        print(json.dumps(state.get("auth", {}).get(profile, {}), ensure_ascii=False))
        return 0
    if len(argv) >= 2 and argv[:2] == ["base", "+base-create"]:
        print(json.dumps(state.get("base_create_response", {
            "data": {
                "base": {"app_token": "app_test_created", "name": "令牌表"},
                "table": {"id": "tbl_test_created", "name": "credentials"},
                "created": True,
            }
        })))
        return 0
    if len(argv) >= 2 and argv[:2] == ["base", "+url-resolve"]:
        print(json.dumps(state.get("url_resolve_response", {
            "data": state.get("resolved_url", {
                "base_token": "app_test_adopted",
                "block_id": "tbl_test_adopted",
            })
        })))
        return 0
    if len(argv) >= 2 and argv[:2] == ["base", "+field-list"]:
        print(json.dumps(state.get("field_list_response", {
            "data": {"fields": state.get("fields", DEFAULT_FIELDS)}
        })))
        return 0
    if len(argv) >= 2 and argv[:2] == ["base", "+field-create"]:
        print(json.dumps({"data": {"field": json.loads(_arg(argv, "--json", "{}"))}}))
        return 0
    if len(argv) >= 2 and argv[:2] == ["base", "+record-list"]:
        app_token = _arg(argv, "--base-token")
        fields = [argv[index + 1] for index, value in enumerate(argv[:-1])
                  if value == "--field-id"]
        records = state.get("records", {}).get(app_token, [])
        filter_json = _arg(argv, "--filter-json")
        if filter_json:
            condition = json.loads(filter_json)["conditions"][0]
            records = [record for record in records if record.get(condition[0]) == condition[2]]
        print(json.dumps({"data": {
            "data": [[record.get(field) for field in fields] for record in records],
            "fields": fields,
            "record_id_list": [record.get("_record_id", "") for record in records],
            "has_more": False,
        }}))
        return 0
    print(json.dumps({"ok": False, "error": {"type": "config", "message": "unsupported fake call"}}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
