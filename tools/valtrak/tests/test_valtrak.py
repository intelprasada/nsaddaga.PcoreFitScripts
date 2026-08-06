import importlib.util
import sys
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
