/**
 * #314: helpers for building "external link" capsule chips from
 * VegaNotes link-token attributes (``#url`` / ``#hsd`` / ``#jira`` / ``#pr``).
 *
 * Each helper returns ``{label, href}`` for a single raw token value.
 * The frontend `<LinkChips />` component maps ``task.attrs[<kind>]``
 * values through these helpers to render clickable pills.
 *
 * Base URLs for the named shortcuts are constants here; if we ever
 * need per-deployment overrides they should move to a config file
 * loaded at boot.
 */

export type LinkKind = "url" | "hsd" | "jira" | "pr";

export interface LinkChipData {
  kind: LinkKind;
  label: string;
  href: string;
  /** Tailwind class names for the chip background + text. */
  colorClass: string;
  /** Tooltip shown on hover; includes the full URL. */
  title: string;
}

export const HSD_BASE = "https://hsdes.intel.com/appstore/article/#/";
export const JIRA_BASE = "https://jira.devtools.intel.com/browse/";
export const GITHUB_BASE = "https://github.com/";

const COLOR: Record<LinkKind, string> = {
  hsd:  "bg-sky-50 text-sky-700 border-sky-300 hover:bg-sky-100",
  jira: "bg-teal-50 text-teal-700 border-teal-300 hover:bg-teal-100",
  pr:   "bg-violet-50 text-violet-700 border-violet-300 hover:bg-violet-100",
  url:  "bg-slate-50 text-slate-700 border-slate-300 hover:bg-slate-100",
};

/**
 * Extract a compact hostname/path label from a URL string. Used as the
 * default label for generic ``#url`` values.
 * - ``https://foo.com/bar`` -> ``foo.com``
 * - ``https://sub.foo.com/very/long/path/thing`` -> ``sub.foo.com``
 * - Non-URL input -> the raw value truncated to 40 chars.
 */
export function urlDisplayLabel(raw: string): string {
  const trimmed = raw.trim();
  try {
    const u = new URL(trimmed);
    return u.hostname || trimmed;
  } catch {
    return trimmed.length > 40 ? trimmed.slice(0, 37) + "…" : trimmed;
  }
}

/**
 * Build a chip for a generic ``#url`` value. Supports an optional
 * ``LABEL:url`` prefix: ``#url Design:https://…`` renders as ``Design``.
 */
export function buildUrlChip(raw: string): LinkChipData | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  let label = trimmed;
  let href = trimmed;
  const colonIdx = trimmed.indexOf(":");
  if (colonIdx > 0) {
    const prefix = trimmed.slice(0, colonIdx);
    const rest = trimmed.slice(colonIdx + 1);
    if (
      /^[A-Za-z][\w.-]*$/.test(prefix) &&
      /^https?:\/\//.test(rest)
    ) {
      label = prefix;
      href = rest;
    }
  }
  if (label === href) label = urlDisplayLabel(href);
  if (!/^https?:\/\//.test(href)) return null;
  return {
    kind: "url", label, href,
    colorClass: COLOR.url,
    title: href,
  };
}

/** ``#hsd 1234567`` -> {label: "HSD-1234567", href: hsdes...}. */
export function buildHsdChip(raw: string): LinkChipData | null {
  const id = raw.trim();
  if (!id) return null;
  const href = `${HSD_BASE}${encodeURIComponent(id)}`;
  const label = /^\d+$/.test(id) ? `HSD-${id}` : id;
  return {
    kind: "hsd", label, href,
    colorClass: COLOR.hsd,
    title: href,
  };
}

/** ``#jira ABC-42`` -> {label: "ABC-42", href: jira.devtools...}. */
export function buildJiraChip(raw: string): LinkChipData | null {
  const key = raw.trim();
  if (!key) return null;
  const href = `${JIRA_BASE}${encodeURIComponent(key)}`;
  return {
    kind: "jira", label: key, href,
    colorClass: COLOR.jira,
    title: href,
  };
}

/**
 * ``#pr owner/repo#42`` -> {label: "owner/repo#42",
 *                           href: https://github.com/owner/repo/pull/42}.
 * Falls back to a plain repo link if the ``#N`` PR suffix is missing.
 */
export function buildPrChip(raw: string): LinkChipData | null {
  const spec = raw.trim();
  if (!spec) return null;
  const m = spec.match(/^([^\s#]+)\/([^\s#]+)#(\d+)$/);
  if (m) {
    const [, owner, repo, pr] = m;
    const href = `${GITHUB_BASE}${owner}/${repo}/pull/${pr}`;
    return {
      kind: "pr", label: spec, href,
      colorClass: COLOR.pr,
      title: href,
    };
  }
  // Bare owner/repo -> repo home; useful for tracking a repo w/o a PR.
  const m2 = spec.match(/^([^\s#]+)\/([^\s#]+)$/);
  if (m2) {
    const href = `${GITHUB_BASE}${spec}`;
    return {
      kind: "pr", label: spec, href,
      colorClass: COLOR.pr,
      title: href,
    };
  }
  return null;
}

/**
 * Build the ordered list of chips for a task, given its ``attrs`` map.
 * Deterministic order: hsd -> jira -> pr -> url.
 */
export function buildLinkChips(
  attrs: Record<string, string | string[]> | undefined,
): LinkChipData[] {
  if (!attrs) return [];
  const out: LinkChipData[] = [];
  const each = (key: LinkKind, fn: (raw: string) => LinkChipData | null) => {
    const raw = attrs[key];
    if (!raw) return;
    const arr = Array.isArray(raw) ? raw : [raw];
    for (const v of arr) {
      const chip = fn(v);
      if (chip) out.push(chip);
    }
  };
  each("hsd", buildHsdChip);
  each("jira", buildJiraChip);
  each("pr", buildPrChip);
  each("url", buildUrlChip);
  return out;
}
