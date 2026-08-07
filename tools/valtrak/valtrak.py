#!/usr/bin/env python3
import json
import hmac
import os
import queue
import secrets
import ssl
import sys
import threading
import time
import uuid
from datetime import datetime
from functools import partial
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.append(
    "/usr/intel/pkgs/python3/3.11.1/modules/r1/lib/python3.11/site-packages"
)
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.poolmanager import PoolManager

VMGR_TOOLS = (
    "/p/cth/pu_tu/prd/intel_vms_utils/"
    "vmanager_utils/master_cheetah/latest"
)
sys.path.append(os.path.join(VMGR_TOOLS, "scripts", "vmgr_token"))
from vmgr_get_token import get_token


ROOT = Path(__file__).resolve().parent
STATE_ROOT = Path(
    os.environ.get("VALTRAK_STATE_DIR", "~/.valtrak")
).expanduser().resolve()
STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
DATA_PATH = STATE_ROOT / "data.json"
PLANS_PATH = STATE_ROOT / "plans.json"
OVERRIDES_PATH = STATE_ROOT / "status-overrides.json"
JOBS_PATH = STATE_ROOT / "status-jobs.json"
TARGETS_PATH = STATE_ROOT / "completion-targets.json"
PINNED_CERT_PATH = ROOT / "vmanager-ca.pem"
ACCESS_TOKEN_PATH = STATE_ROOT / "access-token"
SERVER = os.environ.get("VALTRAK_VMGR_SERVER", "scygrnit337.sc.intel.com:8090")
PROJECT = os.environ.get("VALTRAK_PROJECT", "jnc")
ROOT_PLAN = os.environ.get("VALTRAK_ROOT_PLAN", "JNC All vplans")
ALLOWED_STATUSES = {"open", "complete", "future", "rejected"}
STRUCTURAL_SUBTYPES = {"TCD", "TPF"}
CSRF_TOKEN = secrets.token_urlsafe(32)
ACCESS_TOKEN = ""
REQUIRE_AUTH = False

JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_QUEUE = queue.Queue()
OVERRIDES_LOCK = threading.Lock()
TARGETS_LOCK = threading.Lock()
DATA_LOCK = threading.RLock()
REFRESH_JOBS = {}
REFRESH_JOBS_LOCK = threading.Lock()
REFRESH_QUEUE = queue.Queue()
PLAN_ID_CACHE = {"loadedAt": 0, "names": {}}
PLAN_ID_CACHE_LOCK = threading.Lock()


class PinnedCertificateAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context(cafile=str(PINNED_CERT_PATH))
        context.check_hostname = False
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=context,
            assert_hostname=False,
            **pool_kwargs,
        )


def normalized_plan_candidates(name):
    parts = name.rsplit("_", 2)
    stripped = name
    if len(parts) >= 2 and parts[-1].isdigit():
        stripped = "_".join(parts[:-1])
        second_parts = stripped.rsplit("_", 1)
        if len(second_parts) == 2 and second_parts[-1].isdigit():
            stripped = second_parts[0]
    slash_normalized = stripped.replace("_slash_", "/")
    return [name, stripped, slash_normalized, slash_normalized.strip()]


def load_dashboard_files():
    if not DATA_PATH.exists():
        DATA_PATH.write_text(
            json.dumps(
                {
                    "meta": {
                        "project": PROJECT.upper(),
                        "rootPlan": ROOT_PLAN,
                        "generatedAt": None,
                        "completionRule": (
                            "complete / (complete + open); "
                            "future and rejected excluded"
                        ),
                    },
                    "items": [],
                }
            )
            + "\n"
        )
        DATA_PATH.chmod(0o600)
    if not PLANS_PATH.exists():
        PLANS_PATH.write_text("[]\n")
        PLANS_PATH.chmod(0o600)
    with DATA_PATH.open() as handle:
        dashboard_data = json.load(handle)
    with PLANS_PATH.open() as handle:
        plans = json.load(handle)
    return dashboard_data, plans


DASHBOARD_DATA, PLAN_ROWS = load_dashboard_files()
ITEMS_BY_PATH = {}
REFERENCE_PREFIX_BY_GROUP = {}
PLAN_CATALOG = set()


def rebuild_data_indexes():
    global ITEMS_BY_PATH, REFERENCE_PREFIX_BY_GROUP, PLAN_CATALOG
    ITEMS_BY_PATH = {
        item["p"]: item
        for item in DASHBOARD_DATA["items"]
        if item.get("p")
    }
    REFERENCE_PREFIX_BY_GROUP = {
        item["g"]: item["p"]
        for item in DASHBOARD_DATA["items"]
        if item.get("g") and item.get("p") and item.get("k") == "Reference"
    }
    PLAN_CATALOG = {
        plan["vplan_name"]
        for plan in PLAN_ROWS
        if plan.get("vplan_name")
    }


rebuild_data_indexes()


def canonical_plan_name(group_name):
    for candidate in normalized_plan_candidates(group_name):
        if candidate in PLAN_CATALOG:
            return candidate
    normalized = normalized_plan_candidates(group_name)[-1].lower()
    for plan_name in PLAN_CATALOG:
        if plan_name.strip().lower() == normalized:
            return plan_name
    return None


def load_overrides():
    if not OVERRIDES_PATH.exists():
        return {}
    with OVERRIDES_PATH.open() as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("status-overrides.json must contain an object")
    return data


STATUS_OVERRIDES = load_overrides()


def normalize_completion_targets(payload):
    if not isinstance(payload, dict):
        raise ValueError("Completion targets must be an object")
    if set(payload) - {"overall", "plans", "sections"}:
        raise ValueError("Completion targets contain unknown fields")

    def percentage(value, label):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{label} must be a number")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"{label} must be a whole percentage")
        value = int(value)
        if value < 0 or value > 100:
            raise ValueError(f"{label} must be between 0 and 100")
        return value

    def target_map(value, label):
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object")
        if len(value) > 10000:
            raise ValueError(f"{label} contains too many entries")
        normalized = {}
        for key, target in value.items():
            if not isinstance(key, str) or not key or len(key) > 2048:
                raise ValueError(f"{label} keys must be non-empty strings")
            normalized[key] = percentage(target, f"{label} target")
        return normalized

    return {
        "overall": percentage(payload.get("overall", 100), "Overall target"),
        "plans": target_map(payload.get("plans", {}), "Plan targets"),
        "sections": target_map(payload.get("sections", {}), "Section targets"),
    }


def apply_completion_target(targets, payload):
    if not isinstance(payload, dict) or set(payload) != {"scope", "key", "value"}:
        raise ValueError("Target update must include scope, key, and value")
    scope = payload["scope"]
    key = payload["key"]
    value = payload["value"]
    if scope not in {"overall", "plan", "section"}:
        raise ValueError("Target scope must be overall, plan, or section")
    if not isinstance(key, str) or len(key) > 2048:
        raise ValueError("Target key must be a string")
    if scope == "overall":
        if key:
            raise ValueError("Overall target must not include a key")
        candidate = {
            **targets,
            "overall": value,
        }
    else:
        if not key:
            raise ValueError("Plan and section targets require a key")
        candidate = {
            "overall": targets["overall"],
            "plans": dict(targets["plans"]),
            "sections": dict(targets["sections"]),
        }
        target_map = candidate[f"{scope}s"]
        if value is None:
            target_map.pop(key, None)
        else:
            target_map[key] = value
    return normalize_completion_targets(candidate)


def load_jobs():
    if not JOBS_PATH.exists():
        return {}
    with JOBS_PATH.open() as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError("status-jobs.json must contain an array")
    jobs = {}
    for job in rows:
        if job.get("state") in {"queued", "running"}:
            job["state"] = "queued"
            job.pop("startedAt", None)
        jobs[job["id"]] = job
    return jobs


JOBS.update(load_jobs())


def persist_overrides():
    temporary = OVERRIDES_PATH.with_suffix(".json.tmp")
    with temporary.open("w") as handle:
        json.dump(STATUS_OVERRIDES, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, OVERRIDES_PATH)


def persist_jobs_locked():
    temporary = JOBS_PATH.with_suffix(".json.tmp")
    rows = sorted(
        JOBS.values(),
        key=lambda value: value["createdAt"],
        reverse=True,
    )[:100]
    with temporary.open("w") as handle:
        json.dump(rows, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, JOBS_PATH)


def effective_status(item):
    with OVERRIDES_LOCK:
        override = STATUS_OVERRIDES.get(item["p"])
    return override["status"] if override else item.get("s")


def item_has_status(item):
    return item.get("st") not in STRUCTURAL_SUBTYPES


def record_verified_status(item, plan_name, status):
    with OVERRIDES_LOCK:
        STATUS_OVERRIDES[item["p"]] = {
            "status": status,
            "updatedAt": int(time.time()),
            "plan": plan_name,
        }
        persist_overrides()


def public_job(job):
    return {
        key: value
        for key, value in job.items()
        if key not in {"item"}
    }


def update_job(job_id, **changes):
    with JOBS_LOCK:
        JOBS[job_id].update(changes)
        persist_jobs_locked()
        return public_job(JOBS[job_id])


def api_session():
    token = get_token(server=SERVER)
    session = requests.Session()
    session.auth = (os.environ["USER"], token)
    session.headers.update(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    session.mount("https://", PinnedCertificateAdapter())
    return session


def request_json(session, endpoint, payload):
    response = session.post(
        f"https://{SERVER}/{PROJECT}/vapi/rest{endpoint}",
        json=payload,
        timeout=(60, 300),
        verify=True,
    )
    if not 200 <= response.status_code < 300:
        detail = response.text.strip().replace("\n", " ")[:500]
        raise RuntimeError(
            f"{endpoint} returned HTTP {response.status_code}"
            + (f": {detail}" if detail else "")
        )
    if response.status_code == HTTPStatus.NO_CONTENT or not response.content:
        return None
    return response.json()


def compact_item(row):
    fields = {
        "id": row.get("element_id"),
        "n": row.get("name"),
        "p": row.get("full_path"),
        "k": row.get("vplan_element_kind"),
        "st": row.get("sub_type_vmgr"),
        "t": row.get("i_type"),
        "s": row.get("i_status"),
        "o": row.get("i_owner"),
        "team": row.get("i_val_teams"),
        "pri": row.get("i_priority"),
        "mp": row.get("metrics_port_kind"),
    }
    return {
        key: value
        for key, value in fields.items()
        if value is not None and value != ""
    }


def fetch_plan_rows(session, plan_name, allow_empty=False):
    payload = {
        "recursive": True,
        "pageLength": 200000,
        "projection": {"type": "ALL"},
        "sticky-context": {
            "vplan": plan_name,
            "db-vplan": True,
            "ttl": 1,
        },
    }
    rows = request_json(session, "/planning/list-sub-elements", payload)
    if not isinstance(rows, list) or (not rows and not allow_empty):
        raise RuntimeError(f"vManager returned no hierarchy rows for '{plan_name}'")
    return rows


def compact_aggregate_rows(rows):
    items = []
    top_level_references = []
    for row in rows:
        item = compact_item(row)
        if not item.get("p") or not item.get("id") or not item.get("k"):
            raise RuntimeError("Aggregate refresh returned a row without path, ID, or kind")
        top_level = next(
            (
                reference
                for reference in top_level_references
                if item["p"] == reference["p"]
                or item["p"].startswith(reference["p"] + "/")
            ),
            None,
        )
        if top_level is None and item["k"] == "Reference":
            top_level = {"p": item["p"], "g": item.get("n")}
            top_level_references.append(top_level)
        if not top_level or not top_level.get("g"):
            raise RuntimeError("Aggregate hierarchy contains items before the first reference")
        item["g"] = top_level["g"]
        items.append(item)
    if not any(item["k"] == "Reference" for item in items):
        raise RuntimeError("Aggregate refresh returned no plan references")
    return items


def project_plan_rows(plan_name, rows, reference):
    projected = [reference]
    root_prefix = f"{plan_name}/"
    group = reference["g"]
    for row in rows:
        direct_path = row.get("full_path", "")
        if direct_path == plan_name:
            continue
        if not direct_path.startswith(root_prefix):
            raise RuntimeError(
                f"Unexpected path '{direct_path}' while refreshing '{plan_name}'"
            )
        item = compact_item(row)
        kind = item.get("k", "")
        if not item.get("id") or not kind:
            raise RuntimeError("Plan refresh returned a row without ID or kind")
        item["p"] = f"{reference['p']}/{direct_path[len(root_prefix):]}"
        if kind == "Reference":
            item["k"] = kind
        else:
            item["k"] = kind if kind.startswith("Referenced ") else f"Referenced {kind}"
        if not group:
            raise RuntimeError("Plan refresh returned an unnamed reference")
        item["g"] = group
        projected.append(item)
    return projected


def reject_destructive_empty_refresh(plan_name, rows, current_items, references):
    if rows:
        return
    if any(
        item.get("p", "").startswith(reference["p"] + "/")
        for item in current_items
        for reference in references
    ):
        raise RuntimeError(
            f"vManager returned an empty hierarchy for '{plan_name}'; "
            "the existing snapshot was preserved"
        )


def atomic_json_write(path, payload, mode=0o600):
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w") as handle:
        json.dump(payload, handle, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def load_completion_targets():
    if not TARGETS_PATH.exists():
        targets = normalize_completion_targets({})
        atomic_json_write(TARGETS_PATH, targets)
        return targets
    with TARGETS_PATH.open() as handle:
        return normalize_completion_targets(json.load(handle))


COMPLETION_TARGETS = load_completion_targets()


def refresh_catalog(session):
    rows = request_json(
        session,
        "/vplan/list-vplans",
        {"pageLength": 200000, "projection": {"type": "ALL"}},
    )
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("vManager returned an empty validation-plan catalog")
    plans = [
        {
            "owner": row.get("owner") or "",
            "vplan_name": row["vplan_name"],
        }
        for row in rows
        if row.get("vplan_name")
    ]
    if not plans:
        raise RuntimeError("vManager plan catalog contained no named plans")
    return sorted(plans, key=lambda plan: plan["vplan_name"].lower())


def clear_confirmed_overrides(items, refresh_started_at):
    refreshed = {item["p"]: item.get("s") for item in items if item.get("p")}
    with OVERRIDES_LOCK:
        stale = [
            path
            for path, override in STATUS_OVERRIDES.items()
            if path in refreshed
            and override.get("updatedAt", 0) < refresh_started_at
        ]
        for path in stale:
            del STATUS_OVERRIDES[path]
        if stale:
            persist_overrides()


def install_snapshot(
    items,
    plans=None,
    refresh_started_at=0,
    override_reconciliation_items=None,
):
    global DASHBOARD_DATA, PLAN_ROWS
    paths = [item.get("p") for item in items]
    if None in paths or len(paths) != len(set(paths)):
        raise RuntimeError("Refreshed snapshot contains missing or duplicate paths")
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with DATA_LOCK:
        next_data = {
            "meta": {
                **DASHBOARD_DATA.get("meta", {}),
                "generatedAt": generated_at,
            },
            "items": items,
        }
        atomic_json_write(DATA_PATH, next_data, 0o600)
        if plans is not None:
            atomic_json_write(PLANS_PATH, plans, 0o600)
            PLAN_ROWS = plans
        DASHBOARD_DATA = next_data
        rebuild_data_indexes()
    clear_confirmed_overrides(
        override_reconciliation_items
        if override_reconciliation_items is not None
        else items,
        refresh_started_at,
    )
    return generated_at


def direct_item_path(item, plan_name):
    prefix = REFERENCE_PREFIX_BY_GROUP.get(item.get("g"))
    if not prefix or not item["p"].startswith(prefix + "/"):
        raise RuntimeError(
            "The aggregate item path cannot be mapped safely to its database plan"
        )
    relative_path = item["p"][len(prefix) + 1 :]
    return f"{plan_name}/{relative_path}"


def fetch_live_rows(session, plan_name):
    payload = {
        "recursive": True,
        "pageLength": 200000,
        "projection": {
            "selection": [
                "element_id",
                "full_path",
                "name",
                "vplan_element_kind",
                "i_status",
                "reference_to_id",
            ]
        },
        "sticky-context": {
            "vplan": plan_name,
            "db-vplan": True,
            "ttl": 1,
        },
    }
    rows = request_json(session, "/planning/list-sub-elements", payload)
    return rows or []


def fetch_live_item(session, plan_name, item_id, expected_path):
    for row in fetch_live_rows(session, plan_name):
        if row.get("element_id") == item_id and row.get("full_path") == expected_path:
            return row
    raise RuntimeError(
        f"Item {item_id} at '{expected_path}' was not found in database plan {plan_name}"
    )


def plan_names_by_id(session, force=False):
    now = time.time()
    with PLAN_ID_CACHE_LOCK:
        if not force and now - PLAN_ID_CACHE["loadedAt"] < 300:
            return dict(PLAN_ID_CACHE["names"])
    rows = request_json(
        session,
        "/vplan/list-vplans",
        {
            "pageLength": 200000,
            "projection": {"selection": ["vplan_name", "plan_id__"]},
        },
    )
    names = {
        row["plan_id__"]: row["vplan_name"]
        for row in rows or []
        if row.get("plan_id__") and row.get("vplan_name")
    }
    if not names:
        raise RuntimeError("Could not resolve vManager plan ownership")
    with PLAN_ID_CACHE_LOCK:
        PLAN_ID_CACHE["loadedAt"] = now
        PLAN_ID_CACHE["names"] = names
    return names


def referenced_owner_plan_id(rows, live_item):
    target_path = live_item["full_path"]
    references = sorted(
        (
            row
            for row in rows
            if row.get("vplan_element_kind") == "Reference"
            and target_path.startswith(row.get("full_path", "") + "/")
        ),
        key=lambda row: len(row["full_path"]),
        reverse=True,
    )
    for reference in references:
        values = [
            value
            for value in (reference.get("reference_to_id") or "").split(";")
            if "/" in value
        ]
        matching_values = [
            value
            for value in values
            if value.rsplit("/", 1)[-1] == live_item.get("element_id")
        ]
        plan_ids = {
            value.split("/", 1)[0]
            for value in (matching_values or values)
        }
        if len(plan_ids) == 1:
            return plan_ids.pop()
    raise RuntimeError(
        "The referenced item owner could not be resolved from its parent Reference"
    )


def resolve_writable_item(session, plan_name, item_id, expected_path):
    rows = fetch_live_rows(session, plan_name)
    matches = [
        row
        for row in rows
        if row.get("element_id") == item_id
        and row.get("full_path") == expected_path
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Item {item_id} at '{expected_path}' was not uniquely found in {plan_name}"
        )
    live_item = matches[0]
    visited = {plan_name}
    for _ in range(8):
        kind = live_item.get("vplan_element_kind", "")
        if not kind.startswith("Referenced "):
            return plan_name, live_item
        owner_id = referenced_owner_plan_id(rows, live_item)
        owner_plan = plan_names_by_id(session).get(owner_id)
        if not owner_plan:
            owner_plan = plan_names_by_id(session, force=True).get(owner_id)
        if not owner_plan:
            raise RuntimeError(
                f"The owning plan {owner_id} is not available in the plan catalog"
            )
        if owner_plan in visited:
            raise RuntimeError("A cycle was found while resolving referenced-plan ownership")
        visited.add(owner_plan)
        rows = fetch_live_rows(session, owner_plan)
        matches = [
            row
            for row in rows
            if row.get("element_id") == item_id
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Item {item_id} was not uniquely found in owning plan {owner_plan}"
            )
        plan_name = owner_plan
        live_item = matches[0]
    raise RuntimeError("Referenced-plan ownership exceeds the supported nesting depth")


def write_live_status(session, plan_name, live_item, new_status):
    kind = live_item.get("vplan_element_kind", "")
    common = {
        "sticky-context": {
            "vplan": plan_name,
            "db-vplan": True,
        },
        "hierarchy": live_item["full_path"],
    }
    if "Metrics Port" in kind:
        endpoint = "/planning/update-metrics-port"
        payload = {
            **common,
            "metrics-port": {"i_status": new_status},
        }
    elif "Section" in kind:
        endpoint = "/planning/update-section"
        payload = {
            **common,
            "section": {"i_status": new_status},
        }
    else:
        raise RuntimeError(
            f"Items of type '{kind or 'unknown'}' do not support status updates"
        )
    request_json(session, endpoint, payload)


def process_job(job_id):
    with JOBS_LOCK:
        job = JOBS[job_id]
        item = job["item"]
        plan_name = job["plan"]
        expected_status = job["expectedStatus"]
        new_status = job["targetStatus"]
        expected_path = job["directPath"]

    update_job(job_id, state="running", startedAt=int(time.time()))
    session = api_session()
    resolved_plan, live_item = resolve_writable_item(
        session,
        plan_name,
        item["id"],
        expected_path,
    )
    resolved_path = live_item["full_path"]
    update_job(
        job_id,
        resolvedPlan=resolved_plan,
        resolvedPath=resolved_path,
        resolvedKind=live_item.get("vplan_element_kind"),
    )
    live_status = live_item.get("i_status")
    if live_status == new_status:
        record_verified_status(item, resolved_plan, new_status)
        update_job(
            job_id,
            state="succeeded",
            completedAt=int(time.time()),
            verifiedStatus=new_status,
            recovered=True,
        )
        return
    if live_status != expected_status:
        raise RuntimeError(
            "Status changed since this dashboard snapshot "
            f"(expected '{expected_status}', found '{live_status}'). Refresh before retrying."
        )

    write_live_status(session, resolved_plan, live_item, new_status)
    verified = fetch_live_item(
        session,
        resolved_plan,
        item["id"],
        resolved_path,
    )
    verified_status = verified.get("i_status")
    if verified_status != new_status:
        raise RuntimeError(
            "vManager accepted the update but verification returned "
            f"'{verified_status}' instead of '{new_status}'"
        )

    record_verified_status(item, resolved_plan, new_status)
    update_job(
        job_id,
        state="succeeded",
        completedAt=int(time.time()),
        verifiedStatus=verified_status,
    )


def worker():
    while True:
        job_id = JOB_QUEUE.get()
        try:
            process_job(job_id)
        except Exception as error:
            update_job(
                job_id,
                state="failed",
                completedAt=int(time.time()),
                error=str(error),
            )
        finally:
            JOB_QUEUE.task_done()


def public_refresh_job(job):
    return dict(job)


def update_refresh_job(job_id, **changes):
    with REFRESH_JOBS_LOCK:
        REFRESH_JOBS[job_id].update(changes)
        return public_refresh_job(REFRESH_JOBS[job_id])


def process_refresh_job(job_id):
    with REFRESH_JOBS_LOCK:
        job = dict(REFRESH_JOBS[job_id])
    started_at = int(time.time())
    update_refresh_job(job_id, state="running", startedAt=started_at)
    print(
        f"Refresh {job_id} started ({job['scope']}: {job.get('plan') or 'all'})",
        flush=True,
    )
    session = api_session()

    if job["scope"] == "all":
        rows = fetch_plan_rows(session, DASHBOARD_DATA["meta"]["rootPlan"])
        items = compact_aggregate_rows(rows)
        plans = refresh_catalog(session)
        generated_at = install_snapshot(items, plans, started_at)
        update_refresh_job(
            job_id,
            state="succeeded",
            completedAt=int(time.time()),
            generatedAt=generated_at,
            itemCount=len(items),
            planCount=len(plans),
        )
        print(f"Refresh {job_id} completed ({len(items)} items)", flush=True)
        return

    plan_name = job["plan"]
    with DATA_LOCK:
        references = [
            dict(item)
            for item in DASHBOARD_DATA["items"]
            if item.get("k") == "Reference"
            and canonical_plan_name(item.get("g", "")) == plan_name
        ]
        current_items = list(DASHBOARD_DATA["items"])
    references = [
        reference
        for reference in sorted(references, key=lambda item: len(item["p"]))
        if not any(
            reference["p"].startswith(parent["p"] + "/")
            for parent in references
            if parent["p"] != reference["p"]
            and len(parent["p"]) < len(reference["p"])
        )
    ]
    if not references:
        raise RuntimeError(
            f"'{plan_name}' is not referenced by the aggregate snapshot"
        )

    rows = fetch_plan_rows(session, plan_name, allow_empty=True)
    reject_destructive_empty_refresh(plan_name, rows, current_items, references)
    replacements = {
        reference["p"]: project_plan_rows(plan_name, rows, reference)
        for reference in references
    }
    refreshed_items = []
    inserted = set()
    for item in current_items:
        path = item.get("p", "")
        reference_path = next(
            (
                prefix
                for prefix in replacements
                if path == prefix or path.startswith(prefix + "/")
            ),
            None,
        )
        if reference_path is None:
            refreshed_items.append(item)
        elif path == reference_path and reference_path not in inserted:
            refreshed_items.extend(replacements[reference_path])
            inserted.add(reference_path)
    if inserted != set(replacements):
        raise RuntimeError("Could not locate every plan reference in the snapshot")
    projected_items = [
        item
        for replacement in replacements.values()
        for item in replacement
    ]
    generated_at = install_snapshot(
        refreshed_items,
        None,
        started_at,
        projected_items,
    )
    update_refresh_job(
        job_id,
        state="succeeded",
        completedAt=int(time.time()),
        generatedAt=generated_at,
        itemCount=sum(len(items) for items in replacements.values()),
        referenceCount=len(references),
    )
    print(
        f"Refresh {job_id} completed ({len(refreshed_items)} snapshot items)",
        flush=True,
    )


def refresh_worker():
    while True:
        job_id = REFRESH_QUEUE.get()
        try:
            process_refresh_job(job_id)
        except Exception as error:
            update_refresh_job(
                job_id,
                state="failed",
                completedAt=int(time.time()),
                error=str(error),
            )
        finally:
            REFRESH_QUEUE.task_done()


class DashboardHandler(SimpleHTTPRequestHandler):
    server_version = "ValTrak/1.0"

    def is_authorized(self):
        if not REQUIRE_AUTH:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        supplied = cookie.get("valtrak_access")
        return bool(
            supplied
            and hmac.compare_digest(supplied.value, ACCESS_TOKEN)
        )

    def establish_session(self):
        if not REQUIRE_AUTH:
            return False
        query = parse_qs(urlparse(self.path).query)
        supplied = query.get("access_token", [""])[0]
        if not supplied or not hmac.compare_digest(supplied, ACCESS_TOKEN):
            return False
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"valtrak_access={ACCESS_TOKEN}; HttpOnly; SameSite=Strict; Path=/",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        return True

    def reject_unauthorized(self, api=False):
        if api:
            self.send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "Dashboard access token required"},
            )
            return
        body = (
            "<!doctype html><title>Access required</title>"
            "<h1>Dashboard access required</h1>"
            "<p>Open the tokenized dashboard URL provided by the service owner.</p>"
        ).encode("utf-8")
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_state_file(self, path):
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path).path
        if self.establish_session():
            return
        if not self.is_authorized():
            self.reject_unauthorized(api=route.startswith("/api/"))
            return
        if route == "/data.json":
            self.send_state_file(DATA_PATH)
            return
        if route == "/plans.json":
            self.send_state_file(PLANS_PATH)
            return
        if route == "/api/config":
            with DATA_LOCK:
                generated_at = DASHBOARD_DATA.get("meta", {}).get("generatedAt")
            self.send_json(
                HTTPStatus.OK,
                {
                    "csrfToken": CSRF_TOKEN,
                    "statuses": sorted(ALLOWED_STATUSES),
                    "project": PROJECT,
                    "rootPlan": ROOT_PLAN,
                    "writesEnabled": True,
                    "refreshEnabled": True,
                    "generatedAt": generated_at,
                },
            )
            return
        if route == "/api/status-overrides":
            with OVERRIDES_LOCK:
                self.send_json(HTTPStatus.OK, STATUS_OVERRIDES)
            return
        if route == "/api/completion-targets":
            with TARGETS_LOCK:
                self.send_json(HTTPStatus.OK, COMPLETION_TARGETS)
            return
        if route == "/api/jobs":
            with JOBS_LOCK:
                jobs = [
                    public_job(job)
                    for job in sorted(
                        JOBS.values(),
                        key=lambda value: value["createdAt"],
                        reverse=True,
                    )[:50]
                ]
            self.send_json(HTTPStatus.OK, jobs)
            return
        if route.startswith("/api/jobs/"):
            job_id = route.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = public_job(job) if job else None
            if payload is None:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "Unknown update job"},
                )
            else:
                self.send_json(HTTPStatus.OK, payload)
            return
        if route == "/api/refresh-jobs":
            with REFRESH_JOBS_LOCK:
                jobs = sorted(
                    REFRESH_JOBS.values(),
                    key=lambda value: value["createdAt"],
                    reverse=True,
                )[:20]
            self.send_json(
                HTTPStatus.OK,
                [public_refresh_job(job) for job in jobs],
            )
            return
        if route.startswith("/api/refresh-jobs/"):
            job_id = route.rsplit("/", 1)[-1]
            with REFRESH_JOBS_LOCK:
                job = REFRESH_JOBS.get(job_id)
                payload = public_refresh_job(job) if job else None
            if payload is None:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "Unknown refresh job"},
                )
            else:
                self.send_json(HTTPStatus.OK, payload)
            return
        super().do_GET()

    def do_POST(self):
        route = urlparse(self.path).path
        if not self.is_authorized():
            self.reject_unauthorized(api=True)
            return
        if route not in {
            "/api/status-updates",
            "/api/data-refreshes",
            "/api/completion-targets",
        }:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
            return
        if self.headers.get("X-CSRF-Token") != CSRF_TOKEN:
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "Invalid CSRF token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request length"})
            return
        max_length = 262144 if route == "/api/completion-targets" else 4096
        if length <= 0 or length > max_length:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid request size"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
            return

        if route == "/api/completion-targets":
            try:
                with TARGETS_LOCK:
                    targets = (
                        apply_completion_target(COMPLETION_TARGETS, payload)
                        if "scope" in payload
                        else normalize_completion_targets(payload)
                    )
                    atomic_json_write(TARGETS_PATH, targets)
                    COMPLETION_TARGETS.clear()
                    COMPLETION_TARGETS.update(targets)
                    response = {
                        "overall": COMPLETION_TARGETS["overall"],
                        "plans": dict(COMPLETION_TARGETS["plans"]),
                        "sections": dict(COMPLETION_TARGETS["sections"]),
                    }
            except ValueError as error:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self.send_json(HTTPStatus.OK, response)
            return

        if route == "/api/data-refreshes":
            scope = payload.get("scope")
            plan_name = payload.get("plan")
            if scope not in {"plan", "all"}:
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Refresh scope must be 'plan' or 'all'"},
                )
                return
            if scope == "plan":
                if not isinstance(plan_name, str) or not plan_name:
                    self.send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "A validation-plan name is required"},
                    )
                    return
                with DATA_LOCK:
                    known = plan_name in PLAN_CATALOG
                    linked = any(
                        item.get("k") == "Reference"
                        and canonical_plan_name(item.get("g", "")) == plan_name
                        for item in DASHBOARD_DATA["items"]
                    )
                if not known:
                    self.send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "Unknown validation plan"},
                    )
                    return
                if not linked:
                    self.send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "This plan is not linked in the aggregate snapshot"},
                    )
                    return
            with REFRESH_JOBS_LOCK:
                active = next(
                    (
                        job
                        for job in REFRESH_JOBS.values()
                        if job["state"] in {"queued", "running"}
                    ),
                    None,
                )
                if active:
                    self.send_json(
                        HTTPStatus.CONFLICT,
                        {"error": "A data refresh is already in progress"},
                    )
                    return
                job_id = str(uuid.uuid4())
                job = {
                    "id": job_id,
                    "scope": scope,
                    "plan": plan_name if scope == "plan" else None,
                    "state": "queued",
                    "createdAt": int(time.time()),
                }
                REFRESH_JOBS[job_id] = job
            REFRESH_QUEUE.put(job_id)
            self.send_json(HTTPStatus.ACCEPTED, public_refresh_job(job))
            return

        item_path = payload.get("itemPath")
        target_status = payload.get("targetStatus")
        expected_status = payload.get("expectedStatus")
        with DATA_LOCK:
            item = ITEMS_BY_PATH.get(item_path)
        if item is None:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown validation item"})
            return
        if not item_has_status(item):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"{item.get('st')} structural headers do not have status"},
            )
            return
        if target_status not in ALLOWED_STATUSES:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Unsupported target status"})
            return
        if expected_status not in ALLOWED_STATUSES:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Unsupported expected status"})
            return
        current_status = effective_status(item)
        if current_status != expected_status:
            self.send_json(
                HTTPStatus.CONFLICT,
                {
                    "error": (
                        "Dashboard status changed; refresh before retrying "
                        f"(current: {current_status})"
                    )
                },
            )
            return
        if target_status == expected_status:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Status is unchanged"})
            return

        plan_name = canonical_plan_name(item.get("g", ""))
        if plan_name is None:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "This aggregate reference cannot be mapped safely to a database plan"},
            )
            return
        try:
            underlying_path = direct_item_path(item, plan_name)
        except RuntimeError as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        kind = item.get("k", "")
        if "Section" not in kind and "Metrics Port" not in kind:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": f"Items of type '{kind or 'unknown'}' are read-only"},
            )
            return

        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "state": "queued",
            "createdAt": int(time.time()),
            "itemPath": item_path,
            "elementId": item["id"],
            "itemName": item.get("n", ""),
            "plan": plan_name,
            "directPath": underlying_path,
            "expectedStatus": expected_status,
            "targetStatus": target_status,
            "item": item,
        }
        with JOBS_LOCK:
            JOBS[job_id] = job
            persist_jobs_locked()
        JOB_QUEUE.put(job_id)
        self.send_json(HTTPStatus.ACCEPTED, public_job(job))

    def log_message(self, format_string, *args):
        message = format_string % args
        if ACCESS_TOKEN:
            message = message.replace(ACCESS_TOKEN, "[redacted]")
        print(
            f"{self.address_string()} - {message}",
            flush=True,
        )


def main():
    global ACCESS_TOKEN, REQUIRE_AUTH
    port = int(os.environ.get("VALTRAK_PORT", "8767"))
    host = os.environ.get("VALTRAK_HOST", "127.0.0.1")
    REQUIRE_AUTH = True
    if REQUIRE_AUTH:
        if ACCESS_TOKEN_PATH.exists():
            ACCESS_TOKEN = ACCESS_TOKEN_PATH.read_text().strip()
        else:
            ACCESS_TOKEN = secrets.token_urlsafe(32)
            ACCESS_TOKEN_PATH.write_text(f"{ACCESS_TOKEN}\n")
            ACCESS_TOKEN_PATH.chmod(0o600)
        if not ACCESS_TOKEN:
            raise RuntimeError("Dashboard access token is empty")
    threading.Thread(target=worker, name="vmanager-status-worker", daemon=True).start()
    threading.Thread(
        target=refresh_worker,
        name="vmanager-refresh-worker",
        daemon=True,
    ).start()
    with JOBS_LOCK:
        resumable = [
            job_id
            for job_id, job in JOBS.items()
            if job.get("state") == "queued"
        ]
    for job_id in resumable:
        JOB_QUEUE.put(job_id)
    handler = partial(DashboardHandler, directory=str(ROOT))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"ValTrak listening on http://{host}:{port}", flush=True)
    print(
        f"Access URL: http://{host}:{port}/?access_token={ACCESS_TOKEN}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
