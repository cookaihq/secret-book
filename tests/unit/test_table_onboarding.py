"""Observable table boundary: schema checks never repair; maintenance is explicit."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import secret_book as core


class Table:
    app_token = "app_test"
    table_id = "tbl_test"

    def __init__(self):
        self.fields = copy.deepcopy(core.FIELD_SCHEMA)
        self.records = [{"id": "", "_record_id": "rec_test", "name": "manual-entry",
                         "service": "example", "account": "work"}]
        self.calls = []

    def _run(self, shortcut, extra):
        self.calls.append(shortcut)
        assert shortcut == "+field-list"
        return {"data": {"fields": self.fields}}

    def list_records(self):
        self.calls.append("list_metadata")
        return copy.deepcopy(self.records)

    def find(self, by, name, *, with_secret):
        assert not with_secret, "onboarding and ID maintenance must never read secret"
        return self.list_records()

    def update_record(self, record_id, fields):
        self.calls.append("update_id")
        assert record_id == self.records[0]["_record_id"]
        assert set(fields) == {"id"}
        self.records[0].update(fields)


def test_read_only_connection_accepts_empty_table_and_reports_missing_ids():
    table = Table()
    assert core._inspect_connection(table)["records_without_id"] == 1
    table.records.clear()
    assert core._inspect_connection(table)["visible_records"] == 0
    assert table.calls == ["+field-list", "list_metadata", "+field-list", "list_metadata"]


def test_missing_schema_routes_to_administrator_without_reading_records():
    table = Table()
    table.fields = table.fields[:-1]
    with pytest.raises(core.ProfileGuidance) as caught:
        core._inspect_connection(table)
    assert caught.value.payload["missing_fields"] == ["visible_to"]
    assert table.calls == ["+field-list"]


def test_wrong_field_type_fails_before_any_record_access():
    table = Table()
    table.fields[-1]["multiple"] = False
    with pytest.raises(SystemExit):
        core._inspect_connection(table)
    assert table.calls == ["+field-list"]


def test_id_repair_previews_then_changes_only_selected_id_and_reads_back(monkeypatch, capsys):
    table = Table()
    monkeypatch.setattr(core, "require_backend", lambda args: table)
    monkeypatch.setattr(core, "_snapshot_for_args", lambda args: SimpleNamespace(resource_namespace="namespace"))
    args = SimpleNamespace(name="manual-entry", confirm=None)
    with pytest.raises(core.ProfileGuidance) as pending:
        core._repair_one_record_id(args)
    assert table.calls == ["list_metadata"]
    assert pending.value.payload["review"]["operation"] == "assign_missing_id"
    args.confirm = pending.value.payload["confirmation_token"]
    core._repair_one_record_id(args)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "id_repaired"
    assert result["id"] == table.records[0]["id"]
    assert table.calls == ["list_metadata", "list_metadata", "update_id", "list_metadata"]
    core._repair_one_record_id(args)
    assert json.loads(capsys.readouterr().out)["status"] == "already_has_id"
    assert table.calls.count("update_id") == 1


def test_repair_confirmation_expires_when_actual_record_changes(monkeypatch):
    table = Table()
    monkeypatch.setattr(core, "require_backend", lambda args: table)
    monkeypatch.setattr(core, "_snapshot_for_args", lambda args: SimpleNamespace(resource_namespace="namespace"))
    args = SimpleNamespace(name="manual-entry", confirm=None)
    with pytest.raises(core.ProfileGuidance) as pending:
        core._repair_one_record_id(args)
    args.confirm = pending.value.payload["confirmation_token"]
    table.records[0]["_record_id"] = "rec_replaced"
    with pytest.raises(core.ProfileGuidance) as changed:
        core._repair_one_record_id(args)
    assert changed.value.payload["confirmation_token"] != args.confirm
    assert "update_id" not in table.calls
