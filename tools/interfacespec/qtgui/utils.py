"""
Shared utilities: find latest pipeline result dir, load CSVs,
resolve analysis output paths, settings persistence.
"""

import glob
import json
import os
from typing import Dict, Optional

import pandas as pd

from .config import get_pipeline_out_base

# ---------------------------------------------------------------------------
# Result directory discovery
# ---------------------------------------------------------------------------

def find_latest_result_dir(cluster: str, model_root: str,
                            out_root: Optional[str] = None) -> Optional[str]:
    """Return the results/ dir of the newest pipeline run for the cluster."""
    base = get_pipeline_out_base(cluster, model_root, out_root=out_root)
    pattern = os.path.join(base, f"{cluster}_pipeline_*", "results")
    candidates = [d for d in glob.glob(pattern) if os.path.isdir(d)]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def list_pipeline_runs(cluster: str, model_root: str,
                       out_root: Optional[str] = None):
    """
    Return list of (display_label, result_dir) for all pipeline runs, newest first.
    Each result_dir is the 'results/' sub-directory of the pipeline run.
    """
    import datetime
    base = get_pipeline_out_base(cluster, model_root, out_root=out_root)
    pattern = os.path.join(base, f"{cluster}_pipeline_*", "results")
    candidates = [(d, os.path.getmtime(d)) for d in glob.glob(pattern) if os.path.isdir(d)]
    candidates.sort(key=lambda x: x[1], reverse=True)
    out = []
    for d, mtime in candidates:
        run_dir = os.path.basename(os.path.dirname(d))
        ts_part = run_dir[len(f"{cluster}_pipeline_"):]
        dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        # Count available analysis CSVs
        csv_count = len([f for f in os.listdir(d) if f.endswith(".csv")])
        label = f"{ts_part}   [{dt}]   {csv_count} CSVs"
        out.append((label, d))
    return out


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_csv_to_df(path: str) -> pd.DataFrame:
    """Load CSV into DataFrame; returns empty DataFrame on any error."""
    if not path or not os.path.isfile(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Resolve analysis CSV paths
# ---------------------------------------------------------------------------

def detect_name_token(result_dir: str) -> Optional[str]:
    """
    Detect the name token used in numbered pipeline CSV files.
    Looks for 03_<token>_connectivity_typed.csv and extracts <token>.
    Falls back to None if no match found.
    """
    pattern = os.path.join(result_dir, "03_*_connectivity_typed.csv")
    matches = glob.glob(pattern)
    if matches:
        fname = os.path.basename(matches[0])  # e.g. "03_ooo_connectivity_typed.csv"
        suffix = "_connectivity_typed.csv"
        prefix = "03_"
        if fname.startswith(prefix) and fname.endswith(suffix):
            return fname[len(prefix):-len(suffix)]
    return None


def resolve_analysis_csvs(result_dir: str, cluster: str) -> Dict[str, str]:
    """Map analysis type keys to expected CSV paths inside result_dir.

    The name token in pipeline CSV filenames is derived from the top-level
    RTL file stem (e.g. ooo.v -> "ooo").  This function auto-detects the
    token from the actual files so it works for both old (cluster-named) and
    new (top-module-named) runs.  Falls back to *cluster* if detection fails.
    """
    token = detect_name_token(result_dir) or cluster
    return {
        "typed_connectivity":     os.path.join(result_dir, f"03_{token}_connectivity_typed.csv"),
        "unresolved":             os.path.join(result_dir, f"03_{token}_connectivity_unresolved.csv"),
        "query_view":             os.path.join(result_dir, f"07_{token}_connectivity_query_view.csv"),
        "guard_anomalies":        os.path.join(result_dir, f"07_{token}_guard_alternatives_anomalies.csv"),
        "io_table":               os.path.join(result_dir, f"12_{token}_query_io_table.csv"),
        "io_table_alias_review":  os.path.join(result_dir, f"12_{token}_query_io_table_alias_review.csv"),
        "tiedoff_threaded":       os.path.join(result_dir, f"13_{token}_thread_tiedoff_from_io_table.csv"),
        "name_mismatch_cands":    os.path.join(result_dir, f"14_{token}_unresolved_name_mismatch_candidates.csv"),
        "name_mismatch_compact":  os.path.join(result_dir, f"15_{token}_unresolved_name_mismatch_compact.csv"),
        "connected_to_top":       os.path.join(result_dir, f"17_{token}_connected_to_top_rows.csv"),
    }


def resolve_cross_cluster_csv(pipeline_out_base: str) -> str:
    return os.path.join(pipeline_out_base, "dim_mismatches_cross_clusters.csv")


# ---------------------------------------------------------------------------
# Scripts directory
# ---------------------------------------------------------------------------

def scripts_dir() -> str:
    """Absolute path to the interfacespec scripts directory."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------
# Design:
#   ~/.copilot/interfacespec_gui/settings.json  — stable baseline, always kept
#   ~/.copilot/interfacespec_gui/live_{pid}.json — live overlay for this process
#
# On load  : merge stable + all live_{pid} files (newest-last → newest wins).
#            This means a fresh process always has the last-clean-save values,
#            AND picks up any changes made by a sibling instance that's still open.
# On save  : write live_{pid}.json immediately (survives crash/kill).
# On close : write stable settings.json, then remove live_{pid}.json.
# ---------------------------------------------------------------------------

import os as _os
import glob as _glob

_SETTINGS_DIR   = _os.path.expanduser("~/.copilot/interfacespec_gui")
_STABLE_FILE    = _os.path.join(_SETTINGS_DIR, "settings.json")
_LIVE_FILE      = _os.path.join(_SETTINGS_DIR, f"live_{_os.getpid()}.json")


def load_settings() -> Dict:
    """Merge stable baseline + any live per-process overlays, newest-last."""
    candidates: list = []

    # Stable baseline first (lowest priority)
    if _os.path.isfile(_STABLE_FILE):
        candidates.append(_STABLE_FILE)

    # Live per-process files sorted oldest → newest (so newest wins on conflict)
    live_pattern = _os.path.join(_SETTINGS_DIR, "live_*.json")
    live_files = sorted(_glob.glob(live_pattern), key=_os.path.getmtime)
    candidates.extend(live_files)

    merged: Dict = {}
    for path in candidates:
        try:
            with open(path) as f:
                merged.update(json.load(f))
        except Exception:
            pass
    return merged


def save_settings(settings: Dict) -> None:
    """Write live overlay for this process (fast, survives crash)."""
    _os.makedirs(_SETTINGS_DIR, exist_ok=True)
    try:
        with open(_LIVE_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


def cleanup_settings() -> None:
    """On clean close: promote live file → stable baseline, then remove live file."""
    _os.makedirs(_SETTINGS_DIR, exist_ok=True)
    try:
        if _os.path.isfile(_LIVE_FILE):
            # Promote to stable file
            import shutil as _shutil
            _shutil.copy2(_LIVE_FILE, _STABLE_FILE)
            _os.remove(_LIVE_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Markdown → HTML conversion (no external dependencies)
# ---------------------------------------------------------------------------

def md_to_html(md_text: str, title: str = "Interface Spec") -> str:
    """
    Convert a markdown string to a self-contained HTML page.

    Handles the subset of markdown used by InterfaceSpec output:
    headings, bold/italic, inline code, fenced code blocks, GFM tables,
    unordered/ordered lists, blockquotes, horizontal rules, and paragraphs.
    Uses the ``markdown`` package when available, otherwise falls back to a
    built-in lightweight converter so there are no hard dependencies.
    """
    try:
        import markdown as _md
        body = _md.markdown(
            md_text,
            extensions=["tables", "fenced_code", "toc"],
        )
    except ImportError:
        body = _md_to_html_builtin(md_text)

    css = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       font-size: 34px;
       max-width: 960px; margin: 40px auto; padding: 0 24px;
       color: #1a1a1a; line-height: 1.6; }
h1,h2,h3,h4 { border-bottom: 1px solid #e0e0e0; padding-bottom: .3em; margin-top: 1.4em; }
h1 { font-size: 1.8em; } h2 { font-size: 1.4em; } h3 { font-size: 1.15em; }
a { color: #0969da; }
pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px;
      padding: 14px 18px; overflow-x: auto; }
code { font-family: "SFMono-Regular", Consolas, monospace; font-size: .9em;
       background: #f6f8fa; border-radius: 3px; padding: 2px 5px; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #d0d7de; padding: 6px 13px; }
th { background: #f6f8fa; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
blockquote { border-left: 4px solid #d0d7de; margin: 0; padding: 4px 16px;
             color: #555; background: #fafafa; }
hr { border: none; border-top: 1px solid #e0e0e0; margin: 1.5em 0; }
"""
    return (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        f"<meta charset='utf-8'>\n<title>{title}</title>\n"
        f"<style>{css}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def _md_to_html_builtin(md_text: str) -> str:
    """
    Lightweight markdown-to-HTML converter (no dependencies).
    Handles: fenced code blocks, GFM tables, ATX headings, setext headings,
    unordered/ordered lists, blockquotes, horizontal rules, bold, italic,
    inline code, and paragraphs.
    """
    import re
    import html as _html

    lines = md_text.splitlines()
    out: list[str] = []
    i = 0
    in_ul = False
    in_ol = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def inline(text: str) -> str:
        """Apply inline formatting: escape HTML, then bold/italic/code/links."""
        text = _html.escape(text, quote=False)
        # Inline code (do first so inner content isn't processed)
        text = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", text)
        # Bold+italic
        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
        # Bold
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
        # Italic
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"_([^_]+)_", r"<em>\1</em>", text)
        # Auto-links
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                      lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
        return text

    while i < len(lines):
        line = lines[i]

        # ---- Fenced code block ----------------------------------------
        if line.startswith("```"):
            close_lists()
            lang = line[3:].strip()
            lang_attr = f' class="language-{lang}"' if lang else ""
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(_html.escape(lines[i]))
                i += 1
            out.append(f"<pre><code{lang_attr}>" +
                       "\n".join(code_lines) + "</code></pre>")
            i += 1
            continue

        # ---- GFM table ------------------------------------------------
        # Detect: line with pipes, next line is separator (---)
        if "|" in line and i + 1 < len(lines) and re.match(r"^[\s|:\-]+$", lines[i + 1]):
            close_lists()
            header_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 1  # skip separator
            out.append("<table>")
            out.append("<thead><tr>" +
                       "".join(f"<th>{inline(c)}</th>" for c in header_cells) +
                       "</tr></thead><tbody>")
            i += 1
            while i < len(lines) and "|" in lines[i]:
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>" +
                           "".join(f"<td>{inline(c)}</td>" for c in cells) +
                           "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        # ---- ATX Headings ---------------------------------------------
        hm = re.match(r"^(#{1,6})\s+(.*)", line)
        if hm:
            close_lists()
            level = len(hm.group(1))
            out.append(f"<h{level}>{inline(hm.group(2))}</h{level}>")
            i += 1
            continue

        # ---- Setext Headings ------------------------------------------
        if i + 1 < len(lines) and lines[i + 1].startswith("===") and line.strip():
            close_lists()
            out.append(f"<h1>{inline(line)}</h1>")
            i += 2
            continue
        if i + 1 < len(lines) and lines[i + 1].startswith("---") and line.strip():
            close_lists()
            out.append(f"<h2>{inline(line)}</h2>")
            i += 2
            continue

        # ---- Horizontal rule ------------------------------------------
        if re.match(r"^(\*\*\*|---|___)\s*$", line):
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        # ---- Blockquote -----------------------------------------------
        if line.startswith(">"):
            close_lists()
            out.append(f"<blockquote><p>{inline(line[1:].strip())}</p></blockquote>")
            i += 1
            continue

        # ---- Unordered list item --------------------------------------
        ulm = re.match(r"^(\s*)[*\-+]\s+(.*)", line)
        if ulm:
            if not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(ulm.group(2))}</li>")
            i += 1
            continue

        # ---- Ordered list item ----------------------------------------
        olm = re.match(r"^\d+\.\s+(.*)", line)
        if olm:
            if not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{inline(olm.group(1))}</li>")
            i += 1
            continue

        # ---- Blank line -----------------------------------------------
        if not line.strip():
            close_lists()
            out.append("")
            i += 1
            continue

        # ---- Plain paragraph line -------------------------------------
        close_lists()
        out.append(f"<p>{inline(line)}</p>")
        i += 1

    close_lists()
    return "\n".join(out)
