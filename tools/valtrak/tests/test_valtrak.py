import importlib.util
import http.client
import json
import sys
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parents[1]


def load_valtrak(monkeypatch, tmp_path):
    monkeypatch.setenv("VALTRAK_STATE_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "valtrak_under_test",
        TOOL_DIR / "valtrak.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_first_run_creates_private_empty_snapshot(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)

    assert module.DASHBOARD_DATA["meta"]["rootPlan"] == "JNC All vplans"
    assert module.DASHBOARD_DATA["items"] == []
    assert module.PLAN_ROWS == []
    assert (tmp_path / "data.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "plans.json").stat().st_mode & 0o777 == 0o600
    assert module.COMPLETION_TARGETS == {
        "overall": 100,
        "plans": {},
        "sections": {},
    }
    assert (tmp_path / "completion-targets.json").stat().st_mode & 0o777 == 0o600


def test_compact_item_preserves_validation_milestone(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)

    item = module.compact_item(
        {
            "element_id": "item-1",
            "name": "Milestone item",
            "full_path": "Plan/Milestone item",
            "i_required_by_milestone": "VAL1.0",
        }
    )

    assert item["mil"] == "VAL1.0"


def test_completion_targets_are_normalized_and_persisted(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    targets = module.normalize_completion_targets(
        {
            "overall": 90,
            "plans": {"Plan A": 80.0},
            "sections": {"Root/Plan A/Feature": 75},
        }
    )

    module.atomic_json_write(module.TARGETS_PATH, targets)

    assert module.load_completion_targets() == {
        "overall": 90,
        "plans": {"Plan A": 80},
        "sections": {"Root/Plan A/Feature": 75},
    }


def test_completion_targets_reject_invalid_percentages(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)

    for payload in (
        {"overall": -1},
        {"overall": 101},
        {"overall": 99.5},
        {"plans": {"Plan A": True}},
        {"sections": []},
        {"unknown": 90},
    ):
        try:
            module.normalize_completion_targets(payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid completion targets were accepted: {payload}")


def test_completion_targets_api_persists_shared_state(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    module.PLAN_CATALOG = {"Plan A"}
    module.DASHBOARD_DATA = {
        "items": [
            {"g": "Plan A", "id": "section-1", "p": "Plan A/Feature", "st": "TCD"}
        ]
    }
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(module.DashboardHandler, directory=str(TOOL_DIR)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = {
        "overall": 92,
        "plans": {"Plan A": 88},
        "sections": {"Plan A::section-1": 84},
    }
    try:
        connection = http.client.HTTPConnection(*server.server_address)
        connection.request(
            "POST",
            "/api/completion-targets",
            body=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": module.CSRF_TOKEN,
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == payload

        connection.request(
            "POST",
            "/api/completion-targets",
            body=json.dumps({"scope": "plan", "key": "Plan B", "value": 81}),
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": module.CSRF_TOKEN,
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        payload["plans"]["Plan B"] = 81
        assert json.loads(response.read()) == payload

        connection.request("GET", "/api/completion-targets")
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == payload
        assert json.loads((tmp_path / "completion-targets.json").read_text()) == payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_section_targets_are_limited_to_structural_items(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    module.PLAN_CATALOG = {"Plan A"}
    eligible = [
        {"g": "Plan A", "id": "tcd", "p": "tcd-path", "st": "TCD", "k": "Referenced Section"},
        {"g": "Plan A", "id": "tpf", "p": "tpf-path", "st": "TPF", "k": "Referenced Section"},
        {"g": "Plan A", "id": "ref", "p": "ref-path", "k": "Reference"},
        {"g": "Plan A", "id": "nested", "p": "nested-path", "k": "Referenced Reference"},
    ]
    tc = {"g": "Plan A", "id": "tc", "p": "tc-path", "st": "TC", "k": "Referenced Section"}
    module.DASHBOARD_DATA = {"items": [*eligible, tc]}

    assert all(module.item_supports_section_target(item) for item in eligible)
    assert not module.item_supports_section_target(tc)
    module.validate_section_target_update(
        {"scope": "section", "key": "Plan A::tcd", "value": 90}
    )

    try:
        module.validate_section_target_update(
            {"scope": "section", "key": "Plan A::tc", "value": 90}
        )
    except ValueError as error:
        assert "TCD, TPF, and reference" in str(error)
    else:
        raise AssertionError("TC section target was accepted")

    try:
        module.validate_section_target_update(
            {
                "overall": 90,
                "plans": {},
                "sections": {"Plan A::tcd": 90, "Plan A::tc": 90},
            }
        )
    except ValueError as error:
        assert "TCD, TPF, and reference" in str(error)
    else:
        raise AssertionError("bulk TC section target was accepted")

    try:
        module.validate_section_target_update(
            {"scope": "section", "key": ["Plan A::tcd"], "value": 90}
        )
    except ValueError as error:
        assert "string" in str(error)
    else:
        raise AssertionError("non-string section target key was accepted")


def test_status_writes_use_native_element_id(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    requests = []
    monkeypatch.setattr(
        module,
        "request_json",
        lambda session, endpoint, payload: requests.append((endpoint, payload)),
    )

    module.write_live_status(
        object(),
        "Plan A",
        {
            "element_id": "section-id",
            "full_path": "Plan A/Section with fragile. path",
            "vplan_element_kind": "Section",
        },
        "complete",
    )
    module.write_live_status(
        object(),
        "Plan A",
        {
            "element_id": "port-id",
            "full_path": "Plan A/Section/Port",
            "vplan_element_kind": "Metrics Port",
        },
        "future",
    )

    assert requests == [
        (
            "/planning/update-section",
            {
                "sticky-context": {"vplan": "Plan A", "db-vplan": True},
                "element-id": "section-id",
                "section": {"i_status": "complete"},
            },
        ),
        (
            "/planning/update-metrics-port",
            {
                "sticky-context": {"vplan": "Plan A", "db-vplan": True},
                "element-id": "port-id",
                "metrics-port": {"i_status": "future"},
            },
        ),
    ]
    assert all("hierarchy" not in payload for _, payload in requests)


def test_projects_native_rows_under_aggregate_reference(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    reference = {
        "id": "reference-id",
        "n": "Plan A",
        "p": "Root/Plan A",
        "k": "Reference",
        "g": "Plan A",
    }
    rows = [
        {
            "element_id": "section-id",
            "name": "Feature",
            "full_path": "Plan A/Feature",
            "vplan_element_kind": "Section",
            "i_status": "open",
        }
    ]

    projected = module.project_plan_rows("Plan A", rows, reference)

    assert projected[1]["p"] == "Root/Plan A/Feature"
    assert projected[1]["k"] == "Referenced Section"
    assert projected[1]["g"] == "Plan A"


def test_nested_references_keep_top_level_plan_group(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    reference = {
        "id": "reference-id",
        "n": "Plan A",
        "p": "Root/Plan A",
        "k": "Reference",
        "g": "Plan A",
    }
    rows = [
        {
            "element_id": "nested-reference-id",
            "name": "Nested Plan",
            "full_path": "Plan A/Nested Plan",
            "vplan_element_kind": "Reference",
        },
        {
            "element_id": "section-id",
            "name": "Nested Feature",
            "full_path": "Plan A/Nested Plan/Nested Feature",
            "vplan_element_kind": "Section",
        },
    ]

    projected = module.project_plan_rows("Plan A", rows, reference)

    assert projected[1]["k"] == "Reference"
    assert projected[1]["g"] == "Plan A"
    assert projected[2]["k"] == "Referenced Section"
    assert projected[2]["g"] == "Plan A"


def test_aggregate_nested_references_keep_top_level_plan_group(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    rows = [
        {
            "element_id": "plan-a-id",
            "name": "Plan A",
            "full_path": "Root/Plan A",
            "vplan_element_kind": "Reference",
        },
        {
            "element_id": "nested-id",
            "name": "Nested Plan",
            "full_path": "Root/Plan A/Nested Plan",
            "vplan_element_kind": "Reference",
        },
        {
            "element_id": "feature-id",
            "name": "Feature",
            "full_path": "Root/Plan A/Nested Plan/Feature",
            "vplan_element_kind": "Referenced Section",
        },
        {
            "element_id": "plan-b-id",
            "name": "Plan B",
            "full_path": "Root/Plan B",
            "vplan_element_kind": "Reference",
        },
    ]

    compacted = module.compact_aggregate_rows(rows)

    assert [item["g"] for item in compacted] == [
        "Plan A",
        "Plan A",
        "Plan A",
        "Plan B",
    ]


def test_empty_refresh_preserves_existing_subtree(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    references = [{"p": "Root/Plan A"}]
    current_items = [
        {"p": "Root/Plan A"},
        {"p": "Root/Plan A/Feature"},
    ]

    try:
        module.reject_destructive_empty_refresh(
            "Plan A",
            [],
            current_items,
            references,
        )
    except RuntimeError as error:
        assert "existing snapshot was preserved" in str(error)
    else:
        raise AssertionError("empty refresh replaced a populated subtree")


def test_structural_headers_do_not_have_status(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)

    assert not module.item_has_status({"st": "TCD", "s": "open"})
    assert not module.item_has_status({"st": "TPF", "s": "complete"})
    assert module.item_has_status({"st": "TC", "s": "open"})
    assert module.item_has_status({"k": "Referenced Section", "s": "open"})


def test_resolves_renamed_plan_root_with_shared_elements(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    reference = {
        "id": "reference-id",
        "n": "Plan A DV",
        "p": "Root/Plan A DV",
        "k": "Reference",
        "g": "Plan A DV",
    }
    current_items = [
        reference,
        {"id": "shared-id", "p": "Root/Plan A DV/Feature"},
    ]
    rows = [
        {
            "element_id": "shared-id",
            "name": "Feature",
            "full_path": "Plan A/Feature",
            "vplan_element_kind": "Section",
        }
    ]

    source_plan_name = module.resolve_refresh_root(
        "Plan A DV",
        rows,
        current_items,
        [reference],
    )
    projected = module.project_plan_rows(
        "Plan A DV",
        rows,
        reference,
        source_plan_name,
    )

    assert source_plan_name == "Plan A"
    assert projected[1]["p"] == "Root/Plan A DV/Feature"
    assert projected[1]["g"] == "Plan A DV"


def test_rejects_unrelated_plan_root_without_shared_elements(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    reference = {"p": "Root/Plan A", "g": "Plan A"}
    current_items = [
        reference,
        {"id": "expected-id", "p": "Root/Plan A/Feature"},
    ]
    rows = [
        {
            "element_id": "foreign-id",
            "full_path": "Plan B/Feature",
        }
    ]

    try:
        module.resolve_refresh_root(
            "Plan A",
            rows,
            current_items,
            [reference],
        )
    except RuntimeError as error:
        assert "Unexpected path" in str(error)
    else:
        raise AssertionError("unrelated plan root was accepted")


def test_rejects_direct_rows_from_another_plan(monkeypatch, tmp_path):
    module = load_valtrak(monkeypatch, tmp_path)
    reference = {
        "id": "reference-id",
        "n": "Plan A",
        "p": "Root/Plan A",
        "k": "Reference",
        "g": "Plan A",
    }
    rows = [
        {
            "element_id": "section-id",
            "name": "Feature",
            "full_path": "Plan B/Feature",
            "vplan_element_kind": "Section",
        }
    ]

    try:
        module.project_plan_rows("Plan A", rows, reference)
    except RuntimeError as error:
        assert "Unexpected path" in str(error)
    else:
        raise AssertionError("foreign plan path was accepted")
