"""Tests for markdown round-trip helpers."""
from app.markdown_ops import update_task_status, replace_attr


def test_replace_existing_status():
    md = "- !task Foo #status todo #owner alice\n"
    out = update_task_status(md, 0, "in-progress")
    assert "#status in-progress" in out
    assert out.endswith("\n")


def test_append_when_absent():
    md = "- !task Foo #owner alice\n"
    out = update_task_status(md, 0, "done")
    assert out.rstrip("\n").endswith("#status done")


def test_preserves_other_lines():
    md = "# Title\n- !task A #status todo\n- !task B\n"
    out = update_task_status(md, 1, "done")
    assert out.splitlines()[0] == "# Title"
    assert out.splitlines()[2] == "- !task B"
    assert "#status done" in out.splitlines()[1]


def test_replace_attr_eta():
    md = "- !task A #eta 2026-01-01\n"
    out = replace_attr(md, 0, "eta", "2026-12-31")
    assert "#eta 2026-12-31" in out
    assert "2026-01-01" not in out


def test_roll_to_next_week_strips_done_and_bumps_title_only():
    from app.markdown_ops import roll_to_next_week
    md = (
        "# Sprint ww16\n"
        "@nancy\n"
        "\n"
        "!task Done parent #status done #eta ww16.5\n"
        "\t!AR finished sub #status done\n"
        "\n"
        "!task Active #status wip #eta ww16.3\n"
        "\t!AR still open #status todo\n"
        "\n"
        "!task Future #status todo #eta ww17.2\n"
        "!task Continuation done\n"
        "\t#status done\n"
        "\t#eta ww16.1\n"
    )
    new_md, new_base, cur, nxt, _src = roll_to_next_week(md, "sprint-ww16.md")
    assert (cur, nxt) == (16, 17)
    assert new_base == "sprint-ww17.md"
    # done parent + its (also-done) nested item dropped
    assert "Done parent" not in new_md
    assert "finished sub" not in new_md
    # done task whose status is on a continuation line is also dropped
    assert "Continuation done" not in new_md
    # active items survive
    assert "Active" in new_md
    assert "still open" in new_md
    # title bumped
    assert "# Sprint ww17" in new_md
    # ETA values are NOT bumped — user reviews/marks done or sets new ETA
    assert "#eta ww16.3" in new_md
    assert "#eta ww17.2" in new_md
    assert "#eta ww17.3" not in new_md


def test_roll_keeps_parent_when_children_unfinished():
    """A parent line literally written as `#status done` whose children are
    not done should survive the roll — parser-level rollup downgrades it."""
    from app.markdown_ops import roll_to_next_week
    md = (
        "# Sprint ww20\n"
        "!task Mostly done parent #status done #eta ww20.1\n"
        "\t!AR remaining work #status todo\n"
    )
    new_md, *_ = roll_to_next_week(md, "x-ww20.md")
    assert "Mostly done parent" in new_md
    assert "remaining work" in new_md


def test_roll_rejects_non_ww_filename():
    from app.markdown_ops import roll_to_next_week
    import pytest
    with pytest.raises(ValueError):
        roll_to_next_week("# x\n", "no-week-tag.md")


def test_generate_task_id_unique_and_format():
    from app.markdown_ops import generate_task_id
    seen = set()
    for _ in range(200):
        i = generate_task_id(seen)
        assert i.startswith("T-") and len(i) == 8
        assert i not in seen
        seen.add(i)


def test_inject_missing_ids_idempotent():
    from app.markdown_ops import inject_missing_ids
    md = (
        "# Plan\n"
        "- !task Alpha #priority P1\n"
        "- !task Beta\n"
        "  - !AR sub\n"
    )
    once, added1 = inject_missing_ids(md)
    twice, added2 = inject_missing_ids(once)
    assert len(added1) == 3
    assert added2 == {}
    assert once == twice
    # Each !task / !AR line has an #id token.
    for line in once.splitlines():
        if "!task" in line or "!AR" in line:
            assert "#id T-" in line


def test_roll_emits_refs_and_patches_source():
    from app.markdown_ops import roll_to_next_week
    md = (
        "# Sprint ww30\n"
        "- !task Carry over #status todo #priority P1\n"
        "- !task Done item #status done\n"
    )
    new_md, new_base, cur, nxt, patched = roll_to_next_week(md, "x-ww30.md")
    assert (cur, nxt) == (30, 31)
    assert new_base == "x-ww31.md"
    assert "#id T-" in patched  # ID injected back into source
    assert "Done item" not in new_md  # done stripped
    assert "!task Carry over" not in new_md  # rewritten as ref row
    assert "#task T-" in new_md  # ref row emitted
    assert "Carry over" in new_md  # title preserved
    # Source carries IDs right after the !task keyword; title follows the ID
    assert "!task #id T-" in patched
    assert "Carry over" in patched
    assert "#task T-" not in patched


def test_parser_ref_rows():
    from app.parser import parse
    md = (
        "# Agenda ww31\n"
        "- #task T-ABC123 Carry over #status in-progress\n"
    )
    out = parse(md)
    assert out["tasks"] == []
    assert len(out["ref_rows"]) == 1
    rr = out["ref_rows"][0]
    assert rr["ref_id"] == "T-ABC123"
    assert rr["attrs"].get("status") == "in-progress"


def test_update_task_status_strips_continuation_status():
    """#146: status change must remove a stale #status from a continuation
    line, otherwise the parser's last-wins behaviour silently undoes the
    user's edit and the markdown carries two contradictory tags."""
    md = "- !task Foo\n  #status wip\n"
    out = update_task_status(md, 0, "done")
    assert out.count("#status") == 1
    assert "#status done" in out
    assert "#status wip" not in out


def test_update_task_status_drops_continuation_line_when_only_status():
    md = "- !task Foo\n\t#status wip\n\t#note keep me\n"
    out = update_task_status(md, 0, "done")
    lines = out.splitlines()
    assert lines == ["- !task Foo #status done", "\t#note keep me"]


def test_update_task_status_preserves_other_tokens_on_continuation():
    md = "- !task Foo\n\t#status wip #priority P1\n"
    out = update_task_status(md, 0, "done")
    lines = out.splitlines()
    assert lines == ["- !task Foo #status done", "\t#priority P1"]


def test_update_task_status_does_not_cross_sibling_boundary():
    md = "- !task A\n\t#status wip\n- !task B\n\t#status wip\n"
    out = update_task_status(md, 0, "done")
    # Only A's continuation should be touched; B's remains.
    assert out == "- !task A #status done\n- !task B\n\t#status wip\n"


def test_replace_attr_strips_continuation_priority():
    md = "- !task Foo\n\t#priority P0\n"
    out = replace_attr(md, 0, "priority", "P1")
    assert out.count("#priority") == 1
    assert "#priority P1" in out


def test_remove_attr_strips_continuation_priority():
    from app.markdown_ops import remove_attr
    md = "- !task Foo #priority P1\n\t#priority P0\n"
    out = remove_attr(md, 0, "priority")
    assert "#priority" not in out


# ---------------------------------------------------------------------------
# replace_notes indent style (#54)
# ---------------------------------------------------------------------------

from app.markdown_ops import replace_notes


def test_replace_notes_preserves_tab_indent_style():
    md = "\t- !task Foo\n\t\t#status todo\n"
    # task line is index 0, _line_indent counts the tab as 1 char.
    out = replace_notes(md, 0, 1, "first note\nsecond note")
    lines = out.splitlines()
    assert lines[0] == "\t- !task Foo"
    assert lines[1] == "\t\t#note first note"
    assert lines[2] == "\t\t#note second note"
    # The hand-authored continuation line below should be preserved.
    assert lines[3] == "\t\t#status todo"


def test_replace_notes_preserves_4space_indent_style():
    md = "    - !task Foo\n        #status todo\n"
    out = replace_notes(md, 0, 4, "n1")
    lines = out.splitlines()
    assert lines[0] == "    - !task Foo"
    assert lines[1] == "        #note n1"
    assert lines[2] == "        #status todo"


def test_replace_notes_preserves_2space_indent_style():
    md = "  - !task Foo\n    #status todo\n"
    out = replace_notes(md, 0, 2, "n1")
    lines = out.splitlines()
    assert lines[1] == "    #note n1"


def test_replace_notes_root_level_task_uses_two_spaces():
    md = "- !task Foo\n  #status todo\n"
    out = replace_notes(md, 0, 0, "root note")
    lines = out.splitlines()
    assert lines[0] == "- !task Foo"
    assert lines[1] == "  #note root note"
    assert lines[2] == "  #status todo"


def test_replace_notes_overwrites_existing_notes_with_correct_indent():
    md = "\t- !task Foo\n\t\t#note old one\n\t\t#status todo\n"
    out = replace_notes(md, 0, 1, "fresh")
    lines = out.splitlines()
    assert lines[1] == "\t\t#note fresh"
    assert lines[2] == "\t\t#status todo"
    assert "old one" not in out


def test_replace_notes_empty_string_clears_block():
    md = "\t- !task Foo\n\t\t#note keep me? no\n\t\t#status todo\n"
    out = replace_notes(md, 0, 1, "")
    assert "#note" not in out
    assert "#status todo" in out


# --- replace_notes: learns indent from existing siblings ---------------------

def test_replace_notes_mirrors_existing_sibling_continuation():
    # Two tasks; the first one already has a #status continuation that shows
    # the file's convention. The note we insert under the SECOND task should
    # use the same delta.
    md = (
        "        - !task First\n"
        "            #status todo\n"
        "        - !task Second\n"
    )
    out = replace_notes(md, 2, 8, "second note")
    lines = out.splitlines()
    # Task at 8sp, sibling-task continuation at 12sp (delta = 4sp). New note
    # should match: 8 + 4 = 12 spaces — NOT 16.
    assert lines[3] == "            #note second note", repr(lines[3])


def test_replace_notes_under_8space_task_no_siblings_uses_2space_step():
    # No other task in the file has a continuation line, so we fall back
    # to the conservative 2-space step (not 8).
    md = "        - !task Lonely\n"
    out = replace_notes(md, 0, 8, "n")
    lines = out.splitlines()
    assert lines[1] == "          #note n", repr(lines[1])  # 8 + 2


def test_replace_notes_ignores_existing_note_block_for_indent_inference():
    # If THIS task only has a hand-authored #note above ours (no #status etc.),
    # we deliberately DO NOT trust its indent — it might be from an earlier
    # buggy save. Fall through to step 2/3.
    md = (
        "  - !task X\n"
        "      #note hand-authored at 6sp (possibly bad)\n"
    )
    out = replace_notes(md, 0, 2, "replacement")
    lines = out.splitlines()
    # No other task-with-continuation in the file → default 2-space step.
    # Task at 2sp + 2sp = 4sp.
    assert lines[1] == "    #note replacement", repr(lines[1])


# --- append_note (issue #53) -------------------------------------------------

from app.markdown_ops import append_note


def test_append_note_inserts_after_existing_block():
    md = (
        "\t- !task Foo\n"
        "\t\t#note first\n"
        "\t\t#status todo\n"
    )
    out = append_note(md, 0, "second")
    lines = out.splitlines()
    # First note kept, new one inserted directly after, then #status untouched.
    assert lines[1] == "\t\t#note first"
    assert lines[2] == "\t\t#note second"
    assert lines[3] == "\t\t#status todo"


def test_append_note_with_no_existing_notes():
    md = "\t- !task Foo\n\t\t#status todo\n"
    out = append_note(md, 0, "first")
    lines = out.splitlines()
    assert lines[1] == "\t\t#note first"
    assert lines[2] == "\t\t#status todo"


def test_append_note_plain_text_no_auto_prefix():
    md = "- !task Foo\n"
    out = append_note(md, 0, "investigated")
    lines = out.splitlines()
    assert lines[1] == "  #note investigated"


def test_append_note_multiline_creates_multiple_entries():
    md = "- !task Foo\n"
    out = append_note(md, 0, "line a\nline b\n\nline c")
    lines = out.splitlines()
    assert lines[1] == "  #note line a"
    assert lines[2] == "  #note line b"
    assert lines[3] == "  #note line c"


def test_append_note_empty_input_is_noop():
    md = "- !task Foo\n"
    assert append_note(md, 0, "") == md
    assert append_note(md, 0, "   \n  ") == md


def test_append_note_preserves_prior_history_on_repeat_calls():
    md = "- !task Foo\n"
    md = append_note(md, 0, "one")
    md = append_note(md, 0, "two")
    md = append_note(md, 0, "three")
    lines = md.splitlines()
    assert lines[1] == "  #note one"
    assert lines[2] == "  #note two"
    assert lines[3] == "  #note three"


# ---------------------------------------------------------------------------
# strip_done_tasks — AR roll-forward behaviour (#70)
# ---------------------------------------------------------------------------

def test_strip_done_task_with_open_ar_child_survives():
    """Done parent (inline #status) + non-done AR child → rollup keeps parent alive.

    _rollup_to_parents intentionally downgrades a done parent to in-progress
    when any AR child is still open, so the open AR acts as a reminder.
    The block is stripped only once ALL AR children are done.
    """
    from app.markdown_ops import strip_done_tasks
    md = (
        "# ww16\n"
        "!task Done task #status done\n"
        "\t!AR Still open AR\n"
        "\n"
        "!task Open task #status wip\n"
        "\t!AR Open AR\n"
    )
    result = strip_done_tasks(md)
    # Rollup keeps the done-parent alive because the AR is still open
    assert "Done task" in result, "done parent with open AR should survive (rollup)"
    assert "Still open AR" in result, "open AR under kept parent should survive"
    assert "Open task" in result
    assert "Open AR" in result


def test_strip_done_task_continuation_status_with_open_ar_survives():
    """Done parent (#status done on continuation line) + open AR child → kept alive."""
    from app.markdown_ops import strip_done_tasks
    md = (
        "# ww16\n"
        "!task Done task\n"
        "\t#status done\n"
        "\t!AR Still open AR\n"
    )
    result = strip_done_tasks(md)
    # Rollup keeps the parent alive because the AR is still open
    assert "Done task" in result
    assert "Still open AR" in result


def test_strip_done_ar_under_open_parent():
    """Done AR under open parent is dropped; open AR and parent survive."""
    from app.markdown_ops import strip_done_tasks
    md = (
        "# ww16\n"
        "!task Open task #status wip\n"
        "\t!AR Open AR\n"
        "\t!AR Done AR #status done\n"
    )
    result = strip_done_tasks(md)
    assert "Open task" in result
    assert "Open AR" in result
    assert "Done AR" not in result


def test_strip_done_ar_continuation_status_under_open_parent():
    """Done AR (#status done on continuation line) under open parent is dropped."""
    from app.markdown_ops import strip_done_tasks
    md = (
        "# ww16\n"
        "!task Open task #status wip\n"
        "\t!AR Open AR\n"
        "\t!AR Done AR\n"
        "\t\t#status done\n"
    )
    result = strip_done_tasks(md)
    assert "Open task" in result
    assert "Open AR" in result
    assert "Done AR" not in result


def test_roll_forward_open_ar_carried_done_ar_dropped():
    """Full roll: open AR survives as #AR ref row; done AR is dropped."""
    from app.markdown_ops import roll_to_next_week
    md = (
        "# Sprint ww16\n"
        "!task Active #status wip\n"
        "\t!AR Open action #status todo\n"
        "\t!AR Done action #status done\n"
        "\n"
        "!task All done parent #status done\n"
        "\t!AR Done child AR #status done\n"
    )
    new_md, new_base, cur, nxt, _ = roll_to_next_week(md, "sprint-ww16.md")
    assert (cur, nxt) == (16, 17)
    # All-done parent + all-done AR → entire block stripped
    assert "All done parent" not in new_md
    assert "Done child AR" not in new_md
    # Open parent carries forward as #task ref row
    assert "Active" in new_md
    assert "#task T-" in new_md
    # Open AR carries forward as #AR ref row (not #task)
    assert "Open action" in new_md
    assert "#AR T-" in new_md
    # Done AR dropped
    assert "Done action" not in new_md



# ── find_ref_row_lines ────────────────────────────────────────────────────────

def test_find_ref_row_lines_basic():
    from app.markdown_ops import find_ref_row_lines
    md = (
        "# Weekly\n"
        "- #task T-ABCD12 My task #status todo\n"
        "- !task Another task #id T-ZZZZZZ\n"
        "- #AR T-ABCD12 some AR row\n"
    )
    lines = find_ref_row_lines(md, "T-ABCD12")
    assert lines == [1, 3]


def test_find_ref_row_lines_no_match():
    from app.markdown_ops import find_ref_row_lines
    md = "- !task Declared #id T-ABCD12\n"
    # Declaration lines must NOT be returned (they start with !task, not #task).
    assert find_ref_row_lines(md, "T-ABCD12") == []


def test_find_ref_row_lines_indented_bullet():
    from app.markdown_ops import find_ref_row_lines
    md = "  - #task T-XY1234 Carry-forward\n"
    assert find_ref_row_lines(md, "T-XY1234") == [0]


# ── patch_ref_rows ────────────────────────────────────────────────────────────

def test_patch_ref_rows_status():
    from app.markdown_ops import patch_ref_rows
    md = (
        "# Sprint ww17\n"
        "- #task T-ABCD12 Fix login #status todo\n"
        "Some prose\n"
    )
    new_md, changed = patch_ref_rows(md, "T-ABCD12", {"status": "done"})
    assert changed
    assert "#status done" in new_md
    assert "#status todo" not in new_md
    assert "Some prose" in new_md  # other lines untouched


def test_patch_ref_rows_clears_eta():
    from app.markdown_ops import patch_ref_rows
    md = "- #task T-ABCD12 Task #eta 2026-W18\n"
    new_md, changed = patch_ref_rows(md, "T-ABCD12", {"eta": ""})
    assert changed
    assert "#eta" not in new_md


def test_patch_ref_rows_owners():
    from app.markdown_ops import patch_ref_rows
    md = "- #task T-ABCD12 Task @alice\n"
    new_md, changed = patch_ref_rows(md, "T-ABCD12", {"owners": ["bob", "carol"]})
    assert changed
    assert "@bob" in new_md
    assert "@carol" in new_md
    assert "@alice" not in new_md


def test_patch_ref_rows_no_match_returns_unchanged():
    from app.markdown_ops import patch_ref_rows
    md = "- #task T-OTHER my task\n"
    new_md, changed = patch_ref_rows(md, "T-ABCD12", {"status": "done"})
    assert not changed
    assert new_md == md


def test_patch_ref_rows_multiple_lines():
    from app.markdown_ops import patch_ref_rows
    md = (
        "- #task T-ABCD12 In ww17 #status todo\n"
        "- #task T-ABCD12 In ww18 #status todo\n"
    )
    new_md, changed = patch_ref_rows(md, "T-ABCD12", {"status": "in-progress"})
    assert changed
    assert new_md.count("#status in-progress") == 2
    assert "#status todo" not in new_md


# ── insert_ar_ref_row_after (#148) ────────────────────────────────────────────

def test_insert_ar_ref_row_after_basic():
    from app.markdown_ops import insert_ar_ref_row_after
    md = (
        "# Weekly\n"
        "- #task T-PARENT0 Fix login #status todo\n"
        "Some prose\n"
    )
    out, changed = insert_ar_ref_row_after(md, "T-PARENT0", "T-NEWAR01", "investigate dns")
    assert changed
    lines = out.splitlines()
    assert lines[1] == "- #task T-PARENT0 Fix login #status todo"
    assert lines[2] == "- #AR T-NEWAR01 investigate dns"
    assert lines[3] == "Some prose"


def test_insert_ar_ref_row_after_idempotent():
    from app.markdown_ops import insert_ar_ref_row_after
    md = (
        "- #task T-PARENT0 Fix login\n"
        "- #AR T-NEWAR01 investigate dns\n"
    )
    out, changed = insert_ar_ref_row_after(md, "T-PARENT0", "T-NEWAR01", "investigate dns")
    assert not changed
    assert out == md


def test_insert_ar_ref_row_after_no_parent_match():
    from app.markdown_ops import insert_ar_ref_row_after
    md = "# Empty week\n- #task T-OTHER01 Different task\n"
    out, changed = insert_ar_ref_row_after(md, "T-PARENT0", "T-NEWAR01", "investigate")
    assert not changed
    assert out == md


def test_insert_ar_ref_row_preserves_parent_indent():
    from app.markdown_ops import insert_ar_ref_row_after
    md = "\t- #task T-PARENT0 Indented parent\n"
    out, changed = insert_ar_ref_row_after(md, "T-PARENT0", "T-NEWAR01", "child")
    assert changed
    lines = out.splitlines()
    # New AR row matches the parent's leading tab + bullet style.
    assert lines[1] == "\t- #AR T-NEWAR01 child"


def test_insert_ar_ref_row_inserts_after_last_parent_ref():
    from app.markdown_ops import insert_ar_ref_row_after
    md = (
        "- #task T-PARENT0 First mention\n"
        "Some prose\n"
        "- #task T-PARENT0 Second mention\n"
        "Tail\n"
    )
    out, changed = insert_ar_ref_row_after(md, "T-PARENT0", "T-NEWAR01", "")
    assert changed
    lines = out.splitlines()
    # Inserted directly after the LAST parent ref row, before "Tail".
    assert lines[2] == "- #task T-PARENT0 Second mention"
    assert lines[3] == "- #AR T-NEWAR01"
    assert lines[4] == "Tail"


# ---------- normalize_indent_to_tabs ----------
from app.markdown_ops import normalize_indent_to_tabs


def test_normalize_two_space_indent_becomes_one_tab():
    md = "- !task Parent\n  - !task Child\n    - !task Grandchild\n"
    out = normalize_indent_to_tabs(md)
    assert out == "- !task Parent\n\t- !task Child\n\t- !task Grandchild\n"


def test_normalize_four_space_indent_becomes_one_tab():
    md = "- a\n    - b\n        - c\n"
    out = normalize_indent_to_tabs(md)
    assert out == "- a\n\t- b\n\t\t- c\n"


def test_normalize_idempotent_on_tab_input():
    md = "- a\n\t- b\n\t\t- c\n"
    assert normalize_indent_to_tabs(md) == md


def test_normalize_preserves_fenced_code_blocks():
    md = "para\n```python\n  indented_in_code = 1\n    deeper = 2\n```\n  - !task tabbed\n"
    out = normalize_indent_to_tabs(md)
    assert "  indented_in_code = 1\n    deeper = 2\n" in out
    assert "\t- !task tabbed\n" in out


def test_normalize_drops_residual_spaces():
    md = "- a\n     - b\n"  # 5 spaces -> 1 tab (4//4=1)
    assert normalize_indent_to_tabs(md) == "- a\n\t- b\n"


def test_normalize_preserves_blank_lines():
    md = "- a\n\n  - b\n"
    assert normalize_indent_to_tabs(md) == "- a\n\n\t- b\n"


def test_safe_write_normalizes_md_on_disk(tmp_path):
    from app.safe_io import safe_write
    p = tmp_path / "x.md"
    safe_write(p, "- a\n  - b\n", notes_dir=tmp_path)
    assert p.read_text() == "- a\n\t- b\n"


def test_safe_write_skips_non_md(tmp_path):
    from app.safe_io import safe_write
    p = tmp_path / "x.txt"
    safe_write(p, "  preserved\n", notes_dir=tmp_path)
    assert p.read_text() == "  preserved\n"
