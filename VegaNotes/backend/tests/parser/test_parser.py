import json
from datetime import date
from pathlib import Path

import pytest

from app.parser import parse, parse_eta, parse_duration, parse_priority_rank

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _fixture_pairs():
    for md in sorted(FIXTURES.glob("*.md")):
        js = md.with_suffix(".json")
        if js.exists():
            yield pytest.param(md, js, id=md.stem)


@pytest.mark.parametrize("md_path,json_path", list(_fixture_pairs()))
def test_golden(md_path: Path, json_path: Path) -> None:
    actual = parse(md_path.read_text())
    expected = json.loads(json_path.read_text())
    assert actual == expected


def test_eta_iso():
    assert parse_eta("2026-05-01") == "2026-05-01"


def test_eta_relative():
    today = date(2026, 4, 19)
    assert parse_eta("+3d", today=today) == "2026-04-22"
    assert parse_eta("+1w", today=today) == "2026-04-26"


def test_eta_words():
    today = date(2026, 4, 19)  # a Sunday
    assert parse_eta("today", today=today) == "2026-04-19"
    assert parse_eta("tomorrow", today=today) == "2026-04-20"
    assert parse_eta("next mon", today=today) == "2026-04-20"
    assert parse_eta("next fri", today=today) == "2026-04-24"


def test_eta_invalid():
    assert parse_eta("not a date") is None


def test_eta_intel_workweek():
    today = date(2026, 4, 19)  # Sunday → WW17.0 in Intel calendar
    assert parse_eta("WW17.0", today=today) == "2026-04-19"
    assert parse_eta("WW17.1", today=today) == "2026-04-20"
    assert parse_eta("WW17", today=today) == "2026-04-24"  # day defaults to .5 (Friday)
    assert parse_eta("ww16.6", today=today) == "2026-04-18"
    assert parse_eta("2026WW17.3", today=today) == "2026-04-22"
    assert parse_eta("WW1.0", today=today) == "2025-12-28"  # 2026 WW1 starts Sun Dec 28 2025
    assert parse_eta("WW1", today=today) == "2026-01-02"  # WW1 default → Friday Jan 2 2026
    assert parse_eta("WW99", today=today) is None  # out of range
    assert parse_eta("WW17.7", today=today) is None  # day 7 invalid


def test_duration():
    assert parse_duration("4h") == 4
    assert parse_duration("1d") == 8
    assert parse_duration("0.5w") == 20
    assert parse_duration("bad") is None


def test_priority_rank():
    assert parse_priority_rank("P0") == 0
    assert parse_priority_rank("P1") == 1
    assert parse_priority_rank("low") == 7
    assert parse_priority_rank("???") == 999


# ---------------------------------------------------------------------------
# Parser: title extraction with new #id-first format (#85)
# ---------------------------------------------------------------------------

def test_title_extracted_after_id_token():
    """When !task #id T-XXXX appears before the title, the parser must
    extract the correct title from the TextChunk that follows #id."""
    from app.parser import parse
    md = "!task #id T-DVND79 Disable IDQ assertion #priority P1 #eta 2026-W18\n"
    result = parse(md)
    tasks = result["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Disable IDQ assertion"
    assert tasks[0]["attrs"].get("priority") == "P1"
    assert tasks[0]["attrs"].get("id") == "T-DVND79"


def test_title_extracted_after_id_ar():
    """`!AR #id T-XXXX My title` should also extract the title correctly."""
    from app.parser import parse
    md = "  !AR #id T-8XTQ99 why failing now #eta WW17.5 #status todo\n"
    result = parse(md)
    tasks = result["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "why failing now"
    assert tasks[0]["kind"] == "ar"
    assert tasks[0]["attrs"].get("id") == "T-8XTQ99"


def test_title_old_format_still_works():
    """Old format (!task <title> #attr) must still work unchanged."""
    from app.parser import parse
    md = "!task My old task #priority P2\n"
    result = parse(md)
    tasks = result["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "My old task"


def test_title_no_title_is_empty():
    """A task with only attributes and no title text should have empty title."""
    from app.parser import parse
    md = "!task #id T-XXXXX #priority P1\n"
    result = parse(md)
    tasks = result["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == ""
