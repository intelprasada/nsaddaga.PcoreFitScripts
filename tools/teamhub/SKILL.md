---
name: resolve-intel-idsid
description: |
  Resolve Intel IDSID and WWID for one or more employees given their display
  name ("First [Middle] Last"). Uses the on-machine `phonebook` CLI with a
  best-effort last-name lookup, disambiguates by matching the first name in
  the returned BookName ("Last, First [Middle]"), and caches results to a
  local JSON file so repeated lookups are free.

  Trigger phrases:
    - "find the idsid of <name>"
    - "look up idsids for the team"
    - "what's the WWID for <name>"
    - "resolve intel identities"
    - "add IDSID column to <report>"
location: project
---

# resolve-intel-idsid

A tiny helper (packaged inside `dashboard_server.py`) that turns human-readable
Intel display names into `(idsid, wwid)` pairs using the Intel `phonebook` CLI,
with a persistent JSON cache and a manual override table for ambiguous names.

## When to use

Invoke this skill whenever you need to attach an IDSID (Intel unix login) or
WWID (worldwide ID) to a person referenced only by their full name — for
example when:

* Building any team-scoped dashboard, report, or CSV that must include
  identifiers alongside display names.
* Adding a new engineer to a roster that other tooling keys off IDSID (e.g.
  git commit filters, HR data joins, permission grants).
* Debugging a name-based git author regex that is picking up the wrong person
  because of a common surname.

## How it works

The skill lives in `dashboard_server.py` as three top-level functions:

| Function | Purpose |
| --- | --- |
| `_phonebook_lookup(display_name) -> (idsid, wwid) \| None` | Shells out to `phonebook -p phonebook -c BookName -c IDSID -c WWID -d BookName <last>` and matches the row whose BookName contains both the first and the last name (case-insensitive). |
| `resolve_identities(names) -> {name: {idsid, wwid, source}}` | Bulk resolver. Reads/writes `.idsid_cache.json`, applies the `IDSID_HINTS` override map first, then falls back to phonebook. Unresolved names are cached with empty strings so we don't keep re-hammering phonebook. |
| `IDSID_HINTS` (module-level dict) | Manual disambiguation for names that phonebook cannot resolve unambiguously (short surnames, middle-name mismatches, homonyms). |

### Configuration

* **Cache path** — controlled by the `IDSID_CACHE` env var; defaults to
  `./.idsid_cache.json` next to `dashboard_server.py`. Delete the file to
  force a re-lookup.
* **Overrides** — edit `IDSID_HINTS` in `dashboard_server.py` when a new
  hire's phonebook entry differs from the display name used in the dashboard.
  Each override wins over phonebook results.

### HTTP surface

The dashboard server exposes a lightweight endpoint that returns the resolved
identity map for the current TEAM roster:

```
GET /api/identities
→ { "Gautham Ajith": {"idsid":"gajith","wwid":"12336340","source":"phonebook"}, ... }
```

Every `/api/data` response also embeds `identities` at the top level and
sets `engineer.idsid` / `engineer.wwid` on each entry, which the dashboard
uses to render an IDSID column in the leaderboard, next to the name in the
engineer picker, and as its own KPI tile on the engineer detail tab.

## Programmatic use

```python
from dashboard_server import resolve_identities

people = ["Kelsey Byers", "Yongxi Li", "Edwin Mendez Valverde"]
for name, ident in resolve_identities(people).items():
    print(name, ident["idsid"], ident["wwid"])
```

## Notes and caveats

* Phonebook is an Intel-internal CLI. Outside the corporate network the
  lookup will fail silently — the skill just returns empty strings and marks
  the entry `"source": "unresolved"` in the cache, so downstream code should
  treat missing IDSIDs gracefully.
* Ambiguous last names (Li, Wu, Kim, ...) frequently match multiple people.
  Always confirm the first hit before trusting phonebook alone; add an entry
  to `IDSID_HINTS` if you find a mismatch.
* The BookName format is `"Last, First [Middle]"`. If someone commits git
  code under `"Last, Middle First"` (rare but possible) the first-name
  substring match may still succeed because we only require containment.

## Files owned by this skill

* `dashboard_server.py` — `IDSID_HINTS`, `_phonebook_lookup`,
  `resolve_identities`, `_load_idsid_cache`, `_save_idsid_cache`,
  `/api/identities` route.
* `.idsid_cache.json` — auto-generated cache (safe to delete).
* `SKILL.md` — this file.

---

# Related skill: gatekeeper-turnins

The same server also ships a `turnininfo`-backed drill-down. See functions
`_tcsh_hdk_run`, `mine_turnins`, `build_turnin_report`, and the
`/api/turnins?engineer=&project=&range=&year=` HTTP endpoint. The response
gives one row per turnin with `id`, `bundle_id`, `status`, `code_review_url`,
`comments`, `files_changed`, and a parsed `commits[]` list (extracted from
`turnin_notes`). The dashboard's **Turnins (TI)** tab renders the table with
an expandable row for each turnin showing commits and files; clicking a file
opens the git diff for it (via `/api/diff`) using the newest non-merge
commit in that turnin.

Results are cached per `(project, idsid)` for `TURNIN_TTL` seconds
(default 600) because sourcing the HDK env for `turnininfo` costs ~15s per
invocation.
