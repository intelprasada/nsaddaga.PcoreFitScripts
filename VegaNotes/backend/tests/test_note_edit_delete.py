"""Tests for #333 note edit/delete markdown ops (edit_note / remove_note)."""
import pytest
from app.markdown_ops import (
    append_note, edit_note, remove_note, _note_continuation_lines,
    _retarget_ref_note_by_text,
)


def _task_with_notes():
    md = "- !task Foo #status todo\n"
    md = append_note(md, 0, "first note")
    md = append_note(md, 0, "second note")
    md = append_note(md, 0, "third note")
    return md


def test_scan_finds_all_note_lines():
    md = _task_with_notes()
    lines = md.splitlines(keepends=True)
    idxs = _note_continuation_lines(lines, 0)
    assert len(idxs) == 3


def test_edit_note_replaces_only_target():
    md = _task_with_notes()
    out = edit_note(md, 0, 1, "SECOND edited")
    assert "#note first note" in out
    assert "#note SECOND edited" in out
    assert "#note second note" not in out
    assert "#note third note" in out


def test_edit_note_preserves_indent_and_trailing_newline():
    md = "- !task Foo\n\t#note keep indent\n"
    out = edit_note(md, 0, 0, "changed")
    assert "\t#note changed\n" in out


def test_remove_note_deletes_only_target():
    md = _task_with_notes()
    out = remove_note(md, 0, 0)
    assert "#note first note" not in out
    assert "#note second note" in out
    assert "#note third note" in out
    assert out.count("#note") == 2


def test_edit_note_out_of_range_raises():
    md = _task_with_notes()
    with pytest.raises(IndexError):
        edit_note(md, 0, 9, "x")
    with pytest.raises(IndexError):
        remove_note(md, 0, -1)


def test_edit_note_empty_text_rejected():
    md = _task_with_notes()
    with pytest.raises(ValueError):
        edit_note(md, 0, 0, "   ")


def test_expect_guard_blocks_on_mismatch():
    md = _task_with_notes()
    # Wrong expected text -> refuse (safe against index drift / concurrent edit).
    with pytest.raises(ValueError):
        edit_note(md, 0, 0, "new", expect="not the note")
    with pytest.raises(ValueError):
        remove_note(md, 0, 0, expect="not the note")
    # Correct expected text -> succeeds.
    out = edit_note(md, 0, 0, "new", expect="first note")
    assert "#note new" in out


def test_scan_stops_at_non_note_continuation():
    md = "- !task Foo\n\t#note a\n\t#eta 2026-W18\n\t#note b\n"
    lines = md.splitlines(keepends=True)
    idxs = _note_continuation_lines(lines, 0)
    # Only the first #note is contiguous; the #eta line breaks the block.
    assert len(idxs) == 1


def test_ref_note_retarget_by_text_edit_and_delete():
    md = "- #task T-1 Foo\n\t#note keep me\n\t#note change me\n"
    out, did = _retarget_ref_note_by_text(md, 0, "change me", "changed!")
    assert did
    assert "#note changed!" in out
    assert "#note change me" not in out
    out2, did2 = _retarget_ref_note_by_text(md, 0, "change me", None)
    assert did2
    assert "#note change me" not in out2
    assert "#note keep me" in out2


def test_ref_note_retarget_no_match_is_noop():
    md = "- #task T-1 Foo\n\t#note only\n"
    out, did = _retarget_ref_note_by_text(md, 0, "absent", "x")
    assert not did
    assert out == md
