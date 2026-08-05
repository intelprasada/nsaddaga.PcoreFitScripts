"use strict";

const state = {
  items: [],
  plans: [],
  catalogPlans: new Set(),
  planStats: new Map(),
  monitoredPlans: new Set(),
  monitorDraft: new Set(),
  refreshRequest: null,
  refreshPoller: null,
  apiConfig: null,
  jobs: new Map(),
  pollers: new Set(),
  editingItemPath: null,
  selectedPlan: "",
  dimension: "t",
  itemPage: 0,
  itemPageSize: 100,
  itemFilters: { query: "", status: "", type: "", subtype: "" },
  expanded: new Set(),
  treeExpandedAll: false,
  treeFocusPath: "",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const numberFormatter = new Intl.NumberFormat("en-US");
const formatNumber = (value) => numberFormatter.format(value);
const formatPercent = (value) => `${Math.round(value * 100)}%`;
const completionCountsLabel = (counts) =>
  `${formatNumber(counts.open)} open / ${formatNumber(counts.complete)} completed`;
const completionLabel = (counts) =>
  `${formatPercent(counts.completion)} · ${completionCountsLabel(counts)}`;
const safe = (value, fallback = "—") => value || fallback;
const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

function statusCounts(items) {
  const counts = { complete: 0, open: 0, future: 0, rejected: 0, none: 0 };
  items.forEach((item) => {
    const key = Object.hasOwn(counts, item.s) ? item.s : "none";
    counts[key] += 1;
  });
  counts.active = counts.complete + counts.open;
  counts.completion = counts.active ? counts.complete / counts.active : 0;
  return counts;
}

function normalizedPlanCandidates(name) {
  const stripped = name.replace(/(?:_\d+){1,2}$/, "");
  const slashNormalized = stripped.replaceAll("_slash_", "/");
  return [name, stripped, slashNormalized, slashNormalized.trim()];
}

function canonicalPlanName(name, catalog) {
  const direct = normalizedPlanCandidates(name).find((candidate) => catalog.has(candidate));
  if (direct) return direct;
  const trimmed = normalizedPlanCandidates(name).at(-1).toLowerCase();
  return [...catalog].find((candidate) => candidate.trim().toLowerCase() === trimmed) || name;
}

function buildPlanStats() {
  const groups = new Map();
  const catalog = state.catalogPlans;
  state.items.forEach((item) => {
    const name = canonicalPlanName(item.g || "", catalog);
    if (!name) return;
    item.cp = name;
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(item);
  });
  const owners = new Map(state.plans.map((plan) => [plan.vplan_name, plan.owner || ""]));
  groups.forEach((items, name) => {
    state.planStats.set(name, {
      name,
      owner: owners.get(name) || mostCommon(items.map((item) => item.o).filter(Boolean)) || "Unassigned",
      items,
      counts: statusCounts(items),
      references: new Set(items.map((item) => item.g)).size,
    });
  });
  const known = new Set(state.plans.map((plan) => plan.vplan_name));
  state.planStats.forEach((stats, name) => {
    if (!known.has(name)) {
      state.plans.push({ vplan_name: name, owner: stats.owner, aggregate_only: true });
    }
  });
}

function loadMonitoredPlans() {
  const available = new Set(state.plans.map((plan) => plan.vplan_name));
  const saved = localStorage.getItem("valtrak-monitored-plans-v1");
  if (saved === null) {
    state.monitoredPlans = new Set(available);
    return;
  }
  try {
    const values = JSON.parse(saved);
    state.monitoredPlans = new Set(
      Array.isArray(values) ? values.filter((name) => available.has(name)) : [...available]
    );
  } catch {
    state.monitoredPlans = new Set(available);
  }
}

function overviewItems() {
  return state.items.filter((item) => state.monitoredPlans.has(item.cp));
}

function mostCommon(values) {
  const counts = new Map();
  values.forEach((value) => counts.set(value, (counts.get(value) || 0) + 1));
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
}

function summaryCard(label, value, detail, progress, icon) {
  return `
    <article class="summary-card">
      <div class="summary-card-header"><span>${label}</span><span class="summary-card-icon">${icon}</span></div>
      <strong>${value}</strong>
      <footer><span>${detail}</span><span>${Math.round(progress * 100)}%</span></footer>
      <div class="mini-bar"><span style="width:${Math.max(0, Math.min(100, progress * 100))}%"></span></div>
    </article>`;
}

function renderOverview() {
  const scopedItems = overviewItems();
  const scopedPlans = [...state.planStats.values()].filter((plan) => state.monitoredPlans.has(plan.name));
  const counts = statusCounts(scopedItems);
  const planCount = scopedPlans.length;
  const owned = scopedPlans.filter((plan) => plan.owner !== "Unassigned").length;
  $("#monitored-count").textContent = state.monitoredPlans.size;

  $("#summary-cards").innerHTML = [
    summaryCard("Active completion", formatPercent(counts.completion), completionCountsLabel(counts), counts.completion, "✓"),
    summaryCard("Validation plans", formatNumber(state.monitoredPlans.size), `${planCount} linked · ${owned} owned`, planCount ? owned / planCount : 0, "☷"),
    summaryCard("Complete items", formatNumber(counts.complete), `${formatNumber(counts.open)} remain open`, counts.active ? counts.complete / counts.active : 0, "●"),
    summaryCard("Active scope share", formatPercent(scopedItems.length ? counts.active / scopedItems.length : 0), `${formatNumber(counts.future + counts.rejected)} deferred · ${formatNumber(counts.none)} unclassified`, scopedItems.length ? counts.active / scopedItems.length : 0, "↗"),
  ].join("");

  const ring = $("#completion-ring");
  ring.style.setProperty("--completion", `${counts.completion * 100}%`);
  ring.innerHTML = `<div class="ring-label"><strong>${formatPercent(counts.completion)}</strong><span>${completionCountsLabel(counts)}</span></div>`;
  ring.setAttribute("aria-label", `${formatPercent(counts.completion)} active completion, ${completionCountsLabel(counts)}`);

  const legend = [
    ["Complete", counts.complete, "var(--complete)"],
    ["Open", counts.open, "var(--open)"],
    ["Future", counts.future, "var(--future)"],
    ["Rejected", counts.rejected, "var(--rejected)"],
  ];
  $("#status-legend").innerHTML = legend.map(([name, count, color]) => `
    <div class="legend-row">
      <span class="legend-dot" style="background:${color}"></span>
      <span>${name}</span>
      <strong>${formatNumber(count)}</strong>
    </div>`).join("");

  const attention = scopedPlans
    .filter((plan) => plan.counts.open > 0 && plan.counts.active >= 10)
    .sort((a, b) => a.counts.completion - b.counts.completion || b.counts.open - a.counts.open)
    .slice(0, 6);
  $("#attention-list").innerHTML = attention.length ? attention.map((plan) => `
    <div class="attention-row">
      <button data-open-plan="${escapeAttribute(plan.name)}">
        <strong>${escapeHtml(plan.name)}</strong>
        <span>${formatNumber(plan.counts.open)} open · ${escapeHtml(plan.owner)}</span>
      </button>
      <span class="completion-badge">${completionLabel(plan.counts)}</span>
    </div>`).join("") : `<div class="empty-state">No monitored plans currently need attention.</div>`;

  const ownerGroups = new Map();
  scopedItems.filter((item) => item.o && (item.s === "open" || item.s === "complete")).forEach((item) => {
    if (!ownerGroups.has(item.o)) ownerGroups.set(item.o, []);
    ownerGroups.get(item.o).push(item);
  });
  const owners = [...ownerGroups.entries()]
    .map(([name, items]) => ({ name, count: items.length, counts: statusCounts(items) }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 7);
  const maxOwnerCount = owners[0]?.count || 1;
  $("#owner-list").innerHTML = owners.length ? owners.map((owner) => `
    <div class="owner-row">
      <div>
        <strong>${escapeHtml(owner.name)}</strong>
        <span>${completionLabel(owner.counts)}</span>
        <div class="owner-meter"><span style="width:${owner.count / maxOwnerCount * 100}%"></span></div>
      </div>
      <strong>${formatNumber(owner.counts.open)} open</strong>
    </div>`).join("") : `<div class="empty-state">No active owner scopes in the monitored plans.</div>`;

  const referenced = scopedItems.filter((item) => item.k === "Reference").length;
  const ports = scopedItems.filter((item) => item.mp).length;
  const ownersCount = new Set(scopedItems.map((item) => item.o).filter(Boolean)).size;
  $("#portfolio-stats").innerHTML = [
    [planCount, "Plans in aggregate"],
    [referenced, "Plan references"],
    [ports, "Evidence ports"],
    [ownersCount, "Contributing owners"],
  ].map(([value, label]) => `<div class="portfolio-stat"><strong>${formatNumber(value)}</strong><span>${label}</span></div>`).join("");

  renderTypeCompletion(scopedItems);
}

function dimensionLabel(key) {
  const labels = {
    t: "Unclassified",
    st: "No hierarchy type",
    mp: "No evidence port",
  };
  return labels[key];
}

function renderTypeCompletion(items = overviewItems()) {
  const key = state.dimension;
  const grouped = new Map();
  items.forEach((item) => {
    const value = item[key] || dimensionLabel(key);
    if (!grouped.has(value)) grouped.set(value, []);
    grouped.get(value).push(item);
  });
  const rows = [...grouped.entries()]
    .map(([name, items]) => ({ name, counts: statusCounts(items) }))
    .filter((row) => row.counts.active > 0)
    .sort((a, b) => b.counts.active - a.counts.active);

  $("#type-completion").innerHTML = rows.length ? rows.map((row) => {
    const completeWidth = row.counts.active ? row.counts.complete / row.counts.active * 100 : 0;
    const openWidth = 100 - completeWidth;
    return `
      <div class="type-row">
        <span class="type-name" title="${escapeAttribute(row.name)}">${escapeHtml(row.name)}</span>
        <div class="progress-track" aria-label="${escapeAttribute(row.name)}: ${Math.round(completeWidth)}% complete">
          <span class="progress-complete" style="width:${completeWidth}%"></span>
          <span class="progress-open" style="width:${openWidth}%"></span>
        </div>
        <span class="type-score">${Math.round(completeWidth)}%</span>
        <span class="type-count">${completionCountsLabel(row.counts)}</span>
      </div>`;
  }).join("") : `<div class="empty-state">Choose at least one monitored plan to see completion breakdowns.</div>`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  })[char]);
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}

function statusPill(status) {
  const value = status || "none";
  return `<span class="status-pill status-${escapeAttribute(value)}">${escapeHtml(value === "none" ? "No status" : value)}</span>`;
}

function itemIsEditable(item) {
  if (!state.apiConfig?.writesEnabled) return false;
  if (!state.apiConfig.statuses.includes(item.s)) return false;
  if (!state.catalogPlans.has(item.cp)) return false;
  return item.k?.includes("Section") || item.k?.includes("Metrics Port");
}

function statusControl(item) {
  const pill = statusPill(item.s);
  if (!itemIsEditable(item)) return pill;
  return `
    <button class="status-edit-button" data-edit-status="${escapeAttribute(item.p)}"
      aria-label="Change status for ${escapeAttribute(item.n)}. Current status: ${escapeAttribute(item.s)}">
      ${pill}
    </button>`;
}

function planSort(a, b) {
  const aStats = state.planStats.get(a.vplan_name);
  const bStats = state.planStats.get(b.vplan_name);
  if (Boolean(aStats) !== Boolean(bStats)) return aStats ? -1 : 1;
  return a.vplan_name.localeCompare(b.vplan_name);
}

function renderPlanList() {
  const query = $("#plan-search").value.trim().toLowerCase();
  const plans = state.plans
    .filter((plan) => `${plan.vplan_name} ${plan.owner || ""}`.toLowerCase().includes(query))
    .sort(planSort);
  $("#plan-count-label").textContent = `${plans.length} shown`;
  $("#plan-list").innerHTML = plans.map((plan) => {
    const stats = state.planStats.get(plan.vplan_name);
    const completion = stats ? completionLabel(stats.counts) : "Not linked";
    return `
      <button class="plan-list-item ${plan.vplan_name === state.selectedPlan ? "is-selected" : ""}"
        data-plan="${escapeAttribute(plan.vplan_name)}"
        aria-pressed="${plan.vplan_name === state.selectedPlan}">
        <strong title="${escapeAttribute(plan.vplan_name)}">${escapeHtml(plan.vplan_name)}</strong>
        <span class="plan-list-meta"><span>${escapeHtml(plan.owner || "Unassigned")}</span><span>${completion}</span></span>
      </button>`;
  }).join("");
}

function renderPlanDetail({ resetTreeState = true } = {}) {
  const stats = state.planStats.get(state.selectedPlan);
  const listed = state.plans.find((plan) => plan.vplan_name === state.selectedPlan);
  const items = stats?.items || [];
  const counts = stats?.counts || statusCounts([]);
  $("#plan-detail-header").innerHTML = `
    <div class="detail-title-row">
      <div>
        <p class="eyebrow">Validation plan</p>
        <h2>${escapeHtml(state.selectedPlan)}</h2>
        <p class="detail-subtitle">Owned by ${escapeHtml(listed?.owner || stats?.owner || "Unassigned")}</p>
      </div>
      ${items.length ? `<div class="detail-title-actions">
        <button type="button" class="secondary-button" id="refresh-plan-data">Refresh data</button>
        <button type="button" class="secondary-button" id="open-plan-overview">Plan overview</button>
        <span class="completion-badge">${completionLabel(counts)}</span>
      </div>` : ""}
    </div>
    <div class="detail-metrics">
      <div class="detail-metric"><span>${stats?.references > 1 ? `Items across ${stats.references} refs` : "Total items"}</span><strong>${formatNumber(items.length)}</strong></div>
      <div class="detail-metric"><span>Complete</span><strong>${formatNumber(counts.complete)}</strong></div>
      <div class="detail-metric"><span>Open</span><strong>${formatNumber(counts.open)}</strong></div>
      <div class="detail-metric"><span>Deferred</span><strong>${formatNumber(counts.future + counts.rejected)}</strong></div>
    </div>`;
  if (resetTreeState) {
    state.expanded.clear();
    state.treeExpandedAll = false;
    state.treeFocusPath = "";
    $("#expand-tree").textContent = "Expand all";
  }
  renderTree();
}

function openMonitorDialog() {
  state.monitorDraft = new Set(state.monitoredPlans);
  $("#monitor-search").value = "";
  renderMonitorPlans();
  $("#monitor-dialog").showModal();
}

function renderMonitorPlans() {
  const query = $("#monitor-search").value.trim().toLowerCase();
  const plans = state.plans
    .filter((plan) => `${plan.vplan_name} ${plan.owner || ""}`.toLowerCase().includes(query))
    .sort(planSort);
  updateMonitorSummary();
  $("#monitor-plan-list").innerHTML = plans.length ? plans.map((plan) => {
    const stats = state.planStats.get(plan.vplan_name);
    const detail = stats
      ? `${formatNumber(stats.items.length)} items · ${completionLabel(stats.counts)}`
      : "Not referenced in aggregate snapshot";
    return `
      <label class="monitor-plan-row ${stats ? "" : "is-unlinked"}">
        <input type="checkbox" data-monitor-plan="${escapeAttribute(plan.vplan_name)}"
          ${state.monitorDraft.has(plan.vplan_name) ? "checked" : ""}>
        <span>
          <strong title="${escapeAttribute(plan.vplan_name)}">${escapeHtml(plan.vplan_name)}</strong>
          <span>${escapeHtml(plan.owner || "Unassigned")} · ${detail}</span>
        </span>
        <em>${stats ? "Linked" : "Catalog only"}</em>
      </label>`;
  }).join("") : `<div class="empty-state">No plans match this search.</div>`;
}

function updateMonitorSummary() {
  const linkedSelected = [...state.monitorDraft].filter((name) => state.planStats.has(name)).length;
  $("#monitor-selection-summary").textContent =
    `${formatNumber(state.monitorDraft.size)} selected · ${formatNumber(linkedSelected)} linked to this snapshot`;
}

function breakdownRows(items, key, fallback, limit = 8) {
  const groups = new Map();
  items.forEach((item) => {
    const name = item[key] || fallback;
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(item);
  });
  return [...groups.entries()]
    .map(([name, groupedItems]) => ({ name, counts: statusCounts(groupedItems) }))
    .filter((row) => row.counts.active)
    .sort((a, b) => b.counts.active - a.counts.active)
    .slice(0, limit)
    .map((row) => {
      const percent = row.counts.completion * 100;
      return `<div class="plan-breakdown-row">
        <span title="${escapeAttribute(row.name)}">${escapeHtml(row.name)}</span>
        <div class="progress-track" aria-label="${escapeAttribute(row.name)}: ${Math.round(percent)}% complete">
          <span class="progress-complete" style="width:${percent}%"></span>
          <span class="progress-open" style="width:${100 - percent}%"></span>
        </div>
        <strong>${Math.round(percent)}% · ${completionCountsLabel(row.counts)}</strong>
      </div>`;
    }).join("") || `<div class="empty-state">No active items in this category.</div>`;
}

function renderPlanOverview(planName) {
  const stats = state.planStats.get(planName);
  if (!stats) return;
  const listed = state.plans.find((plan) => plan.vplan_name === planName);
  const { counts, items } = stats;
  const ownerGroups = new Map();
  items.filter((item) => item.o).forEach((item) => {
    if (!ownerGroups.has(item.o)) ownerGroups.set(item.o, []);
    ownerGroups.get(item.o).push(item);
  });
  const owners = [...ownerGroups.entries()]
    .map(([name, ownerItems]) => ({ name, counts: statusCounts(ownerItems), total: ownerItems.length }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 8);

  $("#plan-overview-title").textContent = planName;
  $("#plan-overview-subtitle").textContent =
    `${listed?.owner || stats.owner || "Unassigned"} · ${formatNumber(stats.references)} reference${stats.references === 1 ? "" : "s"} · ${formatNumber(counts.active)} active items`;
  $("#plan-overview-content").innerHTML = `
    <div class="plan-overview-hero">
      <div class="plan-score" style="--plan-score:${counts.completion * 100}%">
        <div><strong>${formatPercent(counts.completion)}</strong><span>${completionCountsLabel(counts)}</span></div>
      </div>
      <div class="plan-status-grid">
        ${[
          ["Complete", counts.complete],
          ["Open", counts.open],
          ["Future", counts.future],
          ["Rejected", counts.rejected],
        ].map(([label, value]) => `<div class="plan-status-card"><strong>${formatNumber(value)}</strong><span>${label}</span></div>`).join("")}
      </div>
    </div>
    <div class="plan-overview-grid">
      <section class="plan-overview-section">
        <h3>Functional completion</h3>
        <div class="plan-breakdown">${breakdownRows(items, "t", "Unclassified")}</div>
      </section>
      <section class="plan-overview-section">
        <h3>Hierarchy completion</h3>
        <div class="plan-breakdown">${breakdownRows(items, "st", "No hierarchy type")}</div>
      </section>
      <section class="plan-overview-section">
        <h3>Evidence-port completion</h3>
        <div class="plan-breakdown">${breakdownRows(items, "mp", "No evidence port")}</div>
      </section>
      <section class="plan-overview-section">
        <h3>Top owners</h3>
        <div class="plan-owner-list">
          ${owners.length ? owners.map((owner) => `<div class="plan-owner-row">
            <span>${escapeHtml(owner.name)}</span>
            <strong>${completionLabel(owner.counts)}</strong>
          </div>`).join("") : `<div class="empty-state">No owners are assigned in this plan.</div>`}
        </div>
      </section>
    </div>`;
  $("#plan-overview-dialog").showModal();
}

function openRefreshDialog(scope, plan = null) {
  state.refreshRequest = { scope, plan };
  const isAll = scope === "all";
  $("#refresh-title").textContent = isAll
    ? "Refresh all validation data"
    : `Refresh ${plan}`;
  $("#refresh-description").textContent = isAll
    ? "Re-read the full aggregate hierarchy and available-plan catalog from vManager. The current snapshot stays active until the new data is complete."
    : "Re-read this database plan from vManager and update every matching reference in the aggregate snapshot. Other plans will not be changed.";
  $("#refresh-progress").hidden = true;
  $("#refresh-progress-title").textContent = "Refreshing from vManager…";
  $("#refresh-progress-detail").textContent =
    "The current dashboard remains available while this runs.";
  $("#confirm-refresh").disabled = false;
  $("#confirm-refresh").hidden = false;
  $$(".close-refresh-dialog").forEach((button) => {
    if (button.textContent.trim() !== "×") button.textContent = "Cancel";
  });
  $("#refresh-dialog").showModal();
}

function pollRefreshJob(jobId) {
  if (state.refreshPoller === jobId) return;
  state.refreshPoller = jobId;
  const check = async () => {
    try {
      const job = await apiFetch(`/api/refresh-jobs/${jobId}`);
      if (job.state === "succeeded") {
        state.refreshPoller = null;
        const scope = job.scope === "all" ? "All validation data" : job.plan;
        sessionStorage.setItem(
          "vplan-refresh-message",
          `${scope} refreshed successfully.`
        );
        location.reload();
        return;
      }
      if (job.state === "failed") {
        state.refreshPoller = null;
        $("#refresh-progress").hidden = false;
        $("#refresh-progress-title").textContent = "Refresh failed";
        $("#refresh-progress-detail").textContent = job.error || "Unknown refresh error";
        $("#confirm-refresh").hidden = true;
        $$(".close-refresh-dialog").forEach((button) => {
          if (button.textContent.trim() !== "×") button.textContent = "Close";
        });
        if (!$("#refresh-dialog").open) $("#refresh-dialog").showModal();
        return;
      }
      setTimeout(check, 1500);
    } catch (error) {
      state.refreshPoller = null;
      showToast(`Refresh tracking failed: ${error.message}`);
    }
  };
  check();
}

async function queueDataRefresh(event) {
  event.preventDefault();
  if (!state.refreshRequest) return;
  const submit = $("#confirm-refresh");
  submit.disabled = true;
  try {
    const job = await apiFetch("/api/data-refreshes", {
      method: "POST",
      body: JSON.stringify(state.refreshRequest),
    });
    $("#refresh-progress").hidden = false;
    submit.hidden = true;
    $$(".close-refresh-dialog").forEach((button) => {
      if (button.textContent.trim() !== "×") button.textContent = "Close";
    });
    pollRefreshJob(job.id);
  } catch (error) {
    submit.disabled = false;
    showToast(`Unable to start refresh: ${error.message}`);
  }
}

async function resumeRefreshJob() {
  const jobs = await apiFetch("/api/refresh-jobs");
  const active = jobs.find((job) => job.state === "queued" || job.state === "running");
  if (!active) return;
  showToast("A vManager data refresh is in progress.");
  pollRefreshJob(active.id);
}

function buildTree(items) {
  const nodes = new Map();
  items.forEach((item) => nodes.set(item.p, { item, children: [] }));
  const roots = [];
  nodes.forEach((node, path) => {
    let parentPath = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    while (parentPath && !nodes.has(parentPath) && parentPath.includes("/")) {
      parentPath = parentPath.slice(0, parentPath.lastIndexOf("/"));
    }
    if (nodes.has(parentPath)) nodes.get(parentPath).children.push(node);
    else roots.push(node);
  });
  return roots;
}

function buildHierarchyRollups(roots) {
  const rollups = new Map();
  function visit(node) {
    const counts = node.children.length
      ? node.children.map(visit).reduce((total, child) => {
        ["complete", "open", "future", "rejected", "none"].forEach((key) => {
          total[key] += child[key];
        });
        total.active = total.complete + total.open;
        total.completion = total.active ? total.complete / total.active : 0;
        return total;
      }, statusCounts([]))
      : statusCounts([node.item]);
    rollups.set(node.item.p, counts);
    return counts;
  }
  roots.forEach(visit);
  return rollups;
}

function renderTree() {
  const stats = state.planStats.get(state.selectedPlan);
  if (!stats) {
    $("#plan-tree").innerHTML = `<div class="empty-state">This plan is available in the project but is not referenced by the aggregate plan.</div>`;
    return;
  }
  const query = $("#hierarchy-search").value.trim().toLowerCase();
  const status = $("#hierarchy-status").value;
  const roots = buildTree(stats.items);
  const rollups = buildHierarchyRollups(roots);
  const filtered = stats.items.filter((item) => {
    const matchesStatus = !status || item.s === status;
    const matchesQuery = !query || `${item.n} ${item.p} ${item.o || ""} ${item.t || ""}`.toLowerCase().includes(query);
    return matchesStatus && matchesQuery;
  });

  if (query || status) {
    const limit = 1000;
    const rendered = filtered.slice(0, limit);
    if (!rendered.some((item) => item.p === state.treeFocusPath)) state.treeFocusPath = rendered[0]?.p || "";
    const rows = rendered.map((item) =>
      treeRow(item, Math.max(0, item.p.split("/").length - 2), false, false, rollups.get(item.p))
    ).join("");
    const note = filtered.length > limit
      ? `<div class="tree-limit">Showing ${formatNumber(limit)} of ${formatNumber(filtered.length)} matches. Refine your search.</div>`
      : "";
    $("#plan-tree").innerHTML = rows || `<div class="empty-state">No hierarchy items match these filters.</div>`;
    $("#plan-tree").insertAdjacentHTML("beforeend", note);
    return;
  }

  if (!state.expanded.size) roots.forEach((root) => state.expanded.add(root.item.p));
  const visible = [];
  function walk(nodes, depth) {
    nodes.forEach((node) => {
      visible.push({ node, depth });
      if (state.expanded.has(node.item.p)) walk(node.children, depth + 1);
    });
  }
  walk(roots, 0);
  const limit = 4000;
  const rendered = visible.slice(0, limit);
  if (!rendered.some(({ node }) => node.item.p === state.treeFocusPath)) state.treeFocusPath = rendered[0]?.node.item.p || "";
  const rows = rendered.map(({ node, depth }) =>
    treeRow(node.item, depth, node.children.length > 0, state.expanded.has(node.item.p), rollups.get(node.item.p))
  ).join("");
  const note = visible.length > limit
    ? `<div class="tree-limit">Showing ${formatNumber(limit)} of ${formatNumber(visible.length)} visible items. Collapse branches or search within the plan.</div>`
    : "";
  $("#plan-tree").innerHTML = rows + note;
}

function treeRow(item, depth, hasChildren, expanded, rollup) {
  const expandedAttribute = hasChildren ? ` aria-expanded="${expanded}"` : "";
  return `
    <div class="tree-row" role="treeitem" tabindex="${item.p === state.treeFocusPath ? "0" : "-1"}"
      aria-level="${depth + 1}"${expandedAttribute} data-path="${escapeAttribute(item.p)}">
      <div class="tree-item-name" style="padding-left:${Math.min(depth, 12) * 15}px">
        <button class="tree-expander ${hasChildren ? "" : "is-empty"}" data-toggle-path="${escapeAttribute(item.p)}"
          aria-label="${expanded ? "Collapse" : "Expand"} ${escapeAttribute(item.n)}">${expanded ? "▾" : "▸"}</button>
        <span class="tree-label" title="${escapeAttribute(item.p)}">
          ${escapeHtml(item.n)}
          <span class="tree-kind">
            ${escapeHtml(item.st || item.k || "")}
            <span class="tree-rollup">· ${completionCountsLabel(rollup || statusCounts([item]))}</span>
          </span>
        </span>
      </div>
      <span>${item.t ? `<span class="type-chip">${escapeHtml(item.t)}</span>` : "—"}</span>
      <span class="owner-cell" title="${escapeAttribute(item.o || "")}">${escapeHtml(item.o || "—")}</span>
      ${statusControl(item)}
    </div>`;
}

function populateFilters() {
  const values = (key) => [...new Set(state.items.map((item) => item[key]).filter(Boolean))].sort();
  $("#item-status").innerHTML += values("s").map((value) => `<option value="${escapeAttribute(value)}">${escapeHtml(value)}</option>`).join("");
  $("#item-type").innerHTML += values("t").map((value) => `<option value="${escapeAttribute(value)}">${escapeHtml(value)}</option>`).join("");
  $("#item-subtype").innerHTML += values("st").map((value) => `<option value="${escapeAttribute(value)}">${escapeHtml(value)}</option>`).join("");
}

function filteredItems() {
  const { query, status, type, subtype } = state.itemFilters;
  const normalized = query.trim().toLowerCase();
  return state.items.filter((item) => {
    if (status && item.s !== status) return false;
    if (type && item.t !== type) return false;
    if (subtype && item.st !== subtype) return false;
    return !normalized || `${item.n} ${item.p} ${item.o || ""} ${item.team || ""}`.toLowerCase().includes(normalized);
  });
}

function renderItemTable() {
  const items = filteredItems();
  const pageCount = Math.max(1, Math.ceil(items.length / state.itemPageSize));
  state.itemPage = Math.min(state.itemPage, pageCount - 1);
  const start = state.itemPage * state.itemPageSize;
  const page = items.slice(start, start + state.itemPageSize);
  $("#item-results-count").textContent = `${formatNumber(items.length)} matching items`;
  $("#page-label").textContent = `Page ${state.itemPage + 1} of ${pageCount}`;
  $("#previous-page").disabled = state.itemPage === 0;
  $("#next-page").disabled = state.itemPage >= pageCount - 1;
  $("#item-table-body").innerHTML = page.length ? page.map((item) => `
    <tr>
      <td class="item-main"><strong>${escapeHtml(item.n)}</strong><span title="${escapeAttribute(item.p)}">${escapeHtml(item.p)}</span></td>
      <td>${item.t ? `<span class="type-chip">${escapeHtml(item.t)}</span>` : "—"}</td>
      <td>${escapeHtml(item.st || item.k || "—")}</td>
      <td>${escapeHtml(item.o || "—")}</td>
      <td>${statusControl(item)}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="empty-table-cell">No validation items match these filters.</td></tr>`;
}

function setView(view) {
  $$(".nav-item").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$(".view").forEach((panel) => panel.classList.toggle("is-active", panel.dataset.viewPanel === view));
  $(".sidebar").classList.remove("is-open");
  history.replaceState(null, "", `#${view}`);
}

function openPlan(name) {
  state.selectedPlan = name;
  setView("plans");
  renderPlanList();
  renderPlanDetail();
  $("#plan-list").querySelector(".is-selected")?.scrollIntoView({ block: "nearest" });
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("is-visible"), 2400);
}

async function apiFetch(path, options = {}, retryCsrf = true) {
  const headers = {
    Accept: "application/json",
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.method && options.method !== "GET"
      ? { "X-CSRF-Token": state.apiConfig.csrfToken }
      : {}),
    ...options.headers,
  };
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : { error: `HTTP ${response.status}: ${await response.text() || "empty response"}` };
  if (
    response.status === 403
    && retryCsrf
    && options.method
    && options.method !== "GET"
  ) {
    const configResponse = await fetch("/api/config");
    if (!configResponse.ok) throw new Error("Write service restarted; reload the dashboard");
    state.apiConfig = await configResponse.json();
    return apiFetch(path, options, false);
  }
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function openStatusEditor(itemPath) {
  const item = state.items.find((candidate) => candidate.p === itemPath);
  if (!item || !itemIsEditable(item)) {
    showToast("This item is read-only in the current dashboard context.");
    return;
  }
  state.editingItemPath = itemPath;
  $("#status-item-context").innerHTML = `
    <strong>${escapeHtml(item.n)}</strong>
    <span>${escapeHtml(item.cp)} · ${escapeHtml(item.p)}</span>
    <span>Current status: ${escapeHtml(item.s)}</span>`;
  const order = ["open", "complete", "future", "rejected"];
  $("#new-status").innerHTML = order
    .filter((status) => state.apiConfig.statuses.includes(status))
    .map((status) => `<option value="${status}" ${status === item.s ? "selected" : ""}>${status[0].toUpperCase() + status.slice(1)}</option>`)
    .join("");
  $("#queue-status-update").disabled = true;
  $("#status-dialog").showModal();
}

function renderJobs() {
  const jobs = [...state.jobs.values()].sort((a, b) => b.createdAt - a.createdAt);
  const active = jobs.filter((job) => job.state === "queued" || job.state === "running").length;
  $("#jobs-badge").hidden = active === 0;
  $("#jobs-badge").textContent = active;
  $("#jobs-list").innerHTML = jobs.length ? jobs.map((job) => `
    <div class="job-row">
      <div>
        <strong>${escapeHtml(job.itemName)}</strong>
        <span>${escapeHtml(job.plan)} · ${escapeHtml(job.expectedStatus)} → ${escapeHtml(job.targetStatus)}</span>
        ${job.error ? `<span class="job-error">${escapeHtml(job.error)}</span>` : ""}
      </div>
      <span class="job-state job-${escapeAttribute(job.state)}">${escapeHtml(job.state)}</span>
    </div>`).join("") : `<div class="empty-state">No status updates have been queued in this service session.</div>`;
}

async function loadJobs() {
  const jobs = await apiFetch("/api/jobs");
  jobs.forEach((job) => state.jobs.set(job.id, job));
  renderJobs();
  jobs.filter((job) => job.state === "queued" || job.state === "running")
    .forEach((job) => pollJob(job.id));
}

function applyCompletedJob(job) {
  const item = state.items.find((candidate) => candidate.p === job.itemPath);
  if (!item) return;
  item.s = job.verifiedStatus;
  const stats = state.planStats.get(item.cp);
  if (stats) stats.counts = statusCounts(stats.items);
  renderOverview();
  renderPlanList();
  renderPlanDetail({ resetTreeState: false });
  renderItemTable();
}

function pollJob(jobId) {
  if (state.pollers.has(jobId)) return;
  state.pollers.add(jobId);
  let failures = 0;

  const check = async () => {
    try {
      const job = await apiFetch(`/api/jobs/${jobId}`);
      failures = 0;
      const previous = state.jobs.get(jobId);
      state.jobs.set(jobId, job);
      renderJobs();
      if (job.state === "succeeded") {
        state.pollers.delete(jobId);
        if (previous?.state !== "succeeded") {
          applyCompletedJob(job);
          showToast(`${job.itemName} is now ${job.verifiedStatus}.`);
        }
        return;
      }
      if (job.state === "failed") {
        state.pollers.delete(jobId);
        if (previous?.state !== "failed") {
          showToast(`Update failed: ${job.error}`);
        }
        return;
      }
      setTimeout(check, 1200);
    } catch (error) {
      failures += 1;
      if (failures === 1) {
        showToast(`Update tracking interrupted; retrying in the background.`);
      }
      setTimeout(check, Math.min(10000, 1200 * (2 ** Math.min(failures, 3))));
    }
  };
  check();
}

async function queueStatusUpdate(event) {
  event.preventDefault();
  const item = state.items.find((candidate) => candidate.p === state.editingItemPath);
  if (!item) return;
  const targetStatus = $("#new-status").value;
  if (targetStatus === item.s) return;
  const submit = $("#queue-status-update");
  submit.disabled = true;
  submit.textContent = "Queueing…";
  try {
    const job = await apiFetch("/api/status-updates", {
      method: "POST",
      body: JSON.stringify({
        itemPath: item.p,
        expectedStatus: item.s,
        targetStatus,
      }),
    });
    state.jobs.set(job.id, job);
    renderJobs();
    $("#status-dialog").close();
    showToast(`Queued ${item.n}: ${item.s} → ${targetStatus}`);
    pollJob(job.id);
  } catch (error) {
    showToast(`Unable to queue update: ${error.message}`);
  } finally {
    submit.textContent = "Queue update";
    submit.disabled = false;
  }
}

function debounce(callback, delay = 150) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => callback(...args), delay);
  };
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $$("[data-go-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.goView)));
  $("#mobile-menu").addEventListener("click", () => $(".sidebar").classList.toggle("is-open"));

  $("#dimension-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-dimension]");
    if (!button) return;
    state.dimension = button.dataset.dimension;
    $$("#dimension-tabs button").forEach((item) => item.setAttribute("aria-selected", item === button));
    renderTypeCompletion();
  });

  $("#attention-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-open-plan]");
    if (button) openPlan(button.dataset.openPlan);
  });
  $("#plan-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-plan]");
    if (button) openPlan(button.dataset.plan);
  });
  $("#plan-detail-header").addEventListener("click", (event) => {
    if (event.target.closest("#open-plan-overview")) renderPlanOverview(state.selectedPlan);
    if (event.target.closest("#refresh-plan-data")) {
      openRefreshDialog("plan", state.selectedPlan);
    }
  });
  $("#plan-search").addEventListener("input", debounce(renderPlanList));
  $("#hierarchy-search").addEventListener("input", debounce(renderTree));
  $("#hierarchy-status").addEventListener("change", renderTree);
  $("#plan-tree").addEventListener("click", (event) => {
    const statusButton = event.target.closest("[data-edit-status]");
    if (statusButton) {
      openStatusEditor(statusButton.dataset.editStatus);
      return;
    }
    const button = event.target.closest("[data-toggle-path]");
    if (!button) return;
    const path = button.dataset.togglePath;
    if (state.expanded.has(path)) state.expanded.delete(path);
    else state.expanded.add(path);
    renderTree();
  });
  $("#plan-tree").addEventListener("keydown", (event) => {
    const row = event.target.closest(".tree-row");
    if (!row) return;
    const rows = $$(".tree-row", $("#plan-tree"));
    const index = rows.indexOf(row);
    if (event.key === "ArrowDown" && rows[index + 1]) {
      event.preventDefault();
      rows[index + 1].focus();
    } else if (event.key === "ArrowUp" && rows[index - 1]) {
      event.preventDefault();
      rows[index - 1].focus();
    } else if (["Enter", " ", "ArrowRight", "ArrowLeft"].includes(event.key)) {
      const path = row.dataset.path;
      const expander = row.querySelector(".tree-expander:not(.is-empty)");
      if (!expander) return;
      event.preventDefault();
      const shouldExpand = event.key === "ArrowRight";
      const shouldCollapse = event.key === "ArrowLeft";
      if (shouldExpand) state.expanded.add(path);
      else if (shouldCollapse) state.expanded.delete(path);
      else if (state.expanded.has(path)) state.expanded.delete(path);
      else state.expanded.add(path);
      renderTree();
      $(`[data-path="${CSS.escape(path)}"]`, $("#plan-tree"))?.focus();
    }
  });
  $("#plan-tree").addEventListener("focusin", (event) => {
    const row = event.target.closest(".tree-row");
    if (!row) return;
    state.treeFocusPath = row.dataset.path;
    $$(".tree-row", $("#plan-tree")).forEach((item) => {
      item.tabIndex = item === row ? 0 : -1;
    });
  });
  $("#expand-tree").addEventListener("click", () => {
    const stats = state.planStats.get(state.selectedPlan);
    state.treeExpandedAll = !state.treeExpandedAll;
    state.expanded.clear();
    if (state.treeExpandedAll && stats) stats.items.forEach((item) => state.expanded.add(item.p));
    $("#expand-tree").textContent = state.treeExpandedAll ? "Collapse all" : "Expand all";
    renderTree();
  });

  const syncItemFilters = () => {
    state.itemFilters = {
      query: $("#item-search").value,
      status: $("#item-status").value,
      type: $("#item-type").value,
      subtype: $("#item-subtype").value,
    };
    state.itemPage = 0;
    renderItemTable();
  };
  ["item-search", "item-status", "item-type", "item-subtype"].forEach((id) => {
    $(`#${id}`).addEventListener(id === "item-search" ? "input" : "change", id === "item-search" ? debounce(syncItemFilters) : syncItemFilters);
  });
  $("#clear-filters").addEventListener("click", () => {
    ["item-search", "item-status", "item-type", "item-subtype"].forEach((id) => { $(`#${id}`).value = ""; });
    syncItemFilters();
  });
  $("#previous-page").addEventListener("click", () => { state.itemPage -= 1; renderItemTable(); });
  $("#next-page").addEventListener("click", () => { state.itemPage += 1; renderItemTable(); });
  $("#item-table-body").addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit-status]");
    if (button) openStatusEditor(button.dataset.editStatus);
  });

  const dialog = $("#methodology-dialog");
  $$(".methodology-link").forEach((button) => button.addEventListener("click", () => dialog.showModal()));
  $("#help-button").addEventListener("click", () => dialog.showModal());
  $(".close-dialog").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  $$(".close-status-dialog").forEach((button) => button.addEventListener("click", () => $("#status-dialog").close()));
  $("#new-status").addEventListener("change", () => {
    const item = state.items.find((candidate) => candidate.p === state.editingItemPath);
    $("#queue-status-update").disabled = !item || $("#new-status").value === item.s;
  });
  $("#status-form").addEventListener("submit", queueStatusUpdate);
  $("#jobs-button").addEventListener("click", async () => {
    await loadJobs();
    $("#jobs-dialog").showModal();
  });
  $(".close-jobs-dialog").addEventListener("click", () => $("#jobs-dialog").close());

  $("#manage-monitored-plans").addEventListener("click", openMonitorDialog);
  $("#refresh-all-plans").addEventListener("click", () => openRefreshDialog("all"));
  $("#monitor-search").addEventListener("input", debounce(renderMonitorPlans));
  $("#monitor-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") event.preventDefault();
  });
  $("#monitor-plan-list").addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-monitor-plan]");
    if (!checkbox) return;
    if (checkbox.checked) state.monitorDraft.add(checkbox.dataset.monitorPlan);
    else state.monitorDraft.delete(checkbox.dataset.monitorPlan);
    updateMonitorSummary();
  });
  $("#select-all-monitored").addEventListener("click", () => {
    state.monitorDraft = new Set(state.planStats.keys());
    renderMonitorPlans();
  });
  $("#clear-monitored").addEventListener("click", () => {
    state.monitorDraft.clear();
    renderMonitorPlans();
  });
  $$(".close-monitor-dialog").forEach((button) => button.addEventListener("click", () => {
    $("#monitor-dialog").close();
  }));
  $("#monitor-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.monitoredPlans = new Set(state.monitorDraft);
    localStorage.setItem("valtrak-monitored-plans-v1", JSON.stringify([...state.monitoredPlans]));
    $("#monitor-dialog").close();
    renderOverview();
    showToast(`${formatNumber(state.monitoredPlans.size)} monitored plans saved.`);
  });
  $("#monitor-dialog").addEventListener("click", (event) => {
    if (event.target === $("#monitor-dialog")) $("#monitor-dialog").close();
  });
  $(".close-plan-overview").addEventListener("click", () => $("#plan-overview-dialog").close());
  $("#plan-overview-dialog").addEventListener("click", (event) => {
    if (event.target === $("#plan-overview-dialog")) $("#plan-overview-dialog").close();
  });
  $("#refresh-form").addEventListener("submit", queueDataRefresh);
  $$(".close-refresh-dialog").forEach((button) => button.addEventListener("click", () => {
    $("#refresh-dialog").close();
  }));

  $("#global-search").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const query = event.currentTarget.value.trim();
    if (!query) return;
    setView("items");
    $("#item-search").value = query;
    state.itemFilters.query = query;
    state.itemPage = 0;
    renderItemTable();
    showToast(`Showing results for “${query}”`);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      $("#global-search").focus();
    }
  });
}

async function init() {
  try {
    const [dataResponse, plansResponse, configResponse, overridesResponse] = await Promise.all([
      fetch("data.json"),
      fetch("plans.json"),
      fetch("/api/config"),
      fetch("/api/status-overrides"),
    ]);
    if (!dataResponse.ok || !plansResponse.ok || !configResponse.ok || !overridesResponse.ok) {
      throw new Error("Dashboard data or write-service configuration could not be loaded");
    }
    const [data, plans, config, overrides] = await Promise.all([
      dataResponse.json(),
      plansResponse.json(),
      configResponse.json(),
      overridesResponse.json(),
    ]);
    state.items = data.items;
    state.plans = plans;
    state.catalogPlans = new Set(plans.map((plan) => plan.vplan_name));
    state.apiConfig = config;
    const projectLabel = config.project.toUpperCase();
    $("#project-name").textContent = projectLabel;
    $("#project-avatar").textContent = projectLabel[0] || "V";
    $("#project-picker").setAttribute("aria-label", `Current project: ${projectLabel}`);
    $("#sidebar-project").textContent = `${projectLabel} · ${config.rootPlan}`;
    $("#snapshot-time").textContent = config.generatedAt
      ? dateFormatter.format(new Date(config.generatedAt))
      : "Unknown";
    state.items.forEach((item) => {
      if (overrides[item.p]) item.s = overrides[item.p].status;
    });
    buildPlanStats();
    loadMonitoredPlans();
    const preferred = [...state.planStats.values()]
      .sort((a, b) => b.counts.active - a.counts.active)[0]?.name;
    state.selectedPlan = preferred || state.plans[0]?.vplan_name || "";

    renderOverview();
    renderPlanList();
    renderPlanDetail();
    populateFilters();
    renderItemTable();
    bindEvents();
    loadJobs().catch((error) => showToast(`Unable to load update history: ${error.message}`));
    resumeRefreshJob().catch((error) => showToast(`Unable to check refresh status: ${error.message}`));

    const initialView = ["overview", "plans", "items"].includes(location.hash.slice(1))
      ? location.hash.slice(1)
      : "overview";
    setView(initialView);
    $("#app").classList.remove("is-loading");
    $("#loading-screen").classList.add("is-hidden");
    const refreshMessage = sessionStorage.getItem("vplan-refresh-message");
    if (refreshMessage) {
      sessionStorage.removeItem("vplan-refresh-message");
      showToast(refreshMessage);
    }
  } catch (error) {
    $("#loading-screen").innerHTML = `<div><strong>Unable to load dashboard</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

init();
