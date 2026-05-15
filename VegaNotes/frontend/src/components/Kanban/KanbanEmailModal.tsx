import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";
import type { FilterState } from "../../store/ui";
import {
  buildHtmlBody,
  buildMailto,
  buildPlainBody,
  countOpen,
  defaultSubject,
  looksLikeEmail,
  lookupResolved,
  parseCcList,
  partitionOwners,
  truncateBodyForMailto,
} from "./emailFormat";

interface Props {
  tasks: Task[];
  grouped: Record<string, Task[]>;
  columns: readonly string[];
  filters: FilterState;
  onClose: () => void;
}

/**
 * Phase-1 "Send Email" modal for the Kanban view (#210).
 *
 * Composes a plain-text snapshot of the currently visible board and opens
 * the user's mail client via a `mailto:` deeplink. No server SMTP is used.
 *
 * Owners that look like email addresses go to the To: field; everything else
 * is shown in the body and flagged as "unresolved" (will be wired to the
 * phonebook in Phase 2).
 */
export function KanbanEmailModal({ tasks, grouped, columns, filters, onClose }: Props) {
  const [includeDone, setIncludeDone] = useState(false);
  const [ccRaw, setCcRaw] = useState("");
  const [ccManagers, setCcManagers] = useState(false);
  const [subject, setSubject] = useState(() =>
    defaultSubject({
      project: filters.project,
      openCount: countOpen(grouped),
      blockedCount: grouped["blocked"]?.length ?? 0,
    }),
  );
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const visibleTasks = useMemo(() => {
    if (includeDone) return tasks;
    return tasks.filter((t) => t.status !== "done");
  }, [tasks, includeDone]);

  // Bulk-resolve owner tokens via the phonebook (#174 / #210 Phase 2).
  const ownerTokens = useMemo(() => {
    const s = new Set<string>();
    for (const t of visibleTasks) for (const o of t.owners ?? []) {
      const v = (o ?? "").trim();
      if (v) s.add(v);
    }
    return Array.from(s).sort();
  }, [visibleTasks]);

  // Parse the CC textarea once (for both display and resolution lookup).
  const ccPieces = useMemo(() => parseCcList(ccRaw), [ccRaw]);

  // CC names need the same phonebook resolution as owners (#216).
  // Send email-shaped pieces straight through; everything else goes to
  // the resolver alongside the owner tokens. The single bulk call keeps
  // requests batched and lets React Query cache by token-set.
  const ccNameTokens = useMemo(
    () => ccPieces.filter((p) => !looksLikeEmail(p)).map((p) => p),
    [ccPieces],
  );

  const allTokens = useMemo(() => {
    const merged = new Set<string>([...ownerTokens, ...ccNameTokens]);
    return Array.from(merged).sort();
  }, [ownerTokens, ccNameTokens]);

  const { data: pbData } = useQuery({
    queryKey: ["phonebook", allTokens],
    queryFn: () => api.phonebookResolve(allTokens),
    enabled: allTokens.length > 0,
    staleTime: 5 * 60_000,
  });

  const phonebook = pbData?.resolved ?? {};
  const ambiguousCount = Object.keys(pbData?.ambiguous ?? {}).length;

  const ownerInfo = useMemo(() => {
    const all = visibleTasks.flatMap((t) => t.owners ?? []);
    return partitionOwners(all, phonebook);
  }, [visibleTasks, phonebook]);

  // Apply phonebook resolution to CC tokens too — partitionOwners gives
  // us the same resolved/unresolved buckets so the warning banner can
  // call out unresolved CC names exactly like unresolved owners.
  const ccInfo = useMemo(
    () => partitionOwners(ccPieces, phonebook),
    [ccPieces, phonebook],
  );

  // Optionally CC each unique manager_email of the resolved owners (#217).
  const ownerManagers = useMemo(() => {
    if (!ccManagers) return [] as string[];
    const owners = new Set(ownerInfo.resolved);
    const seen = new Set<string>();
    for (const t of ownerTokens) {
      const ent = lookupResolved(phonebook, t);
      const me = (ent?.manager_email || "").trim().toLowerCase();
      if (me && !owners.has(me)) seen.add(me);
    }
    return Array.from(seen).sort();
  }, [ccManagers, ownerTokens, phonebook, ownerInfo.resolved]);

  // Final CC = resolved CC names ∪ user-typed emails ∪ owner-managers.
  const cc = useMemo(() => {
    const out = new Set<string>(ccInfo.resolved);
    for (const m of ownerManagers) out.add(m);
    return Array.from(out).sort();
  }, [ccInfo.resolved, ownerManagers]);

  const snapshotUrl = typeof window !== "undefined" ? window.location.href : "";

  const body = useMemo(
    () => buildPlainBody({ filters, grouped, columns, snapshotUrl, includeDone, phonebook }),
    [filters, grouped, columns, snapshotUrl, includeDone, phonebook],
  );

  const htmlBody = useMemo(
    () => buildHtmlBody({ filters, grouped, columns, snapshotUrl, includeDone, phonebook }),
    [filters, grouped, columns, snapshotUrl, includeDone, phonebook],
  );

  const mailto = useMemo(() => {
    const safeBody = truncateBodyForMailto(body);
    return buildMailto({ to: ownerInfo.resolved, cc, subject, body: safeBody });
  }, [ownerInfo.resolved, cc, subject, body]);

  // Empty-body mailto used when we successfully copied HTML to the
  // clipboard — the user pastes once and the formatted snapshot lands
  // in the compose window without duplicating the plain body.
  const mailtoEmpty = useMemo(
    () => buildMailto({ to: ownerInfo.resolved, cc, subject, body: "" }),
    [ownerInfo.resolved, cc, subject],
  );

  const cardCount = visibleTasks.length;
  const ownerCount = ownerInfo.resolved.length;

  const sendDisabled = ownerCount === 0 && cc.length === 0;

  // ClipboardItem is only available in secure contexts and on a recent
  // browser. Detect once so we can fall back gracefully.
  const canRichCopy = typeof window !== "undefined"
    && typeof window.ClipboardItem !== "undefined"
    && !!navigator.clipboard?.write;

  const [toast, setToast] = useState<string | null>(null);
  const flashToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3500);
  };

  const writeRichClipboard = async (): Promise<boolean> => {
    if (!canRichCopy) return false;
    try {
      const item = new ClipboardItem({
        "text/html": new Blob([htmlBody], { type: "text/html" }),
        "text/plain": new Blob([body], { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
      return true;
    } catch {
      return false;
    }
  };

  const onSend = async () => {
    const ok = await writeRichClipboard();
    if (ok) {
      flashToast("Snapshot copied — paste into the email body (Ctrl/Cmd+V)");
      window.location.href = mailtoEmpty.url;
    } else {
      // Plain-text fallback: prefilled body in the mailto, no clipboard.
      window.location.href = mailto.url;
    }
  };

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(body);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // fall through silently — clipboard not available
    }
  };

  const [copiedHtml, setCopiedHtml] = useState(false);
  const onCopyHtml = async () => {
    const ok = await writeRichClipboard();
    if (ok) {
      setCopiedHtml(true);
      setTimeout(() => setCopiedHtml(false), 1500);
    } else {
      // Last-resort: copy raw HTML markup as plain text.
      try {
        await navigator.clipboard.writeText(htmlBody);
        setCopiedHtml(true);
        setTimeout(() => setCopiedHtml(false), 1500);
      } catch {
        // ignore
      }
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Send Kanban Email"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-[820px] max-w-[95vw] max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-3 border-b flex items-center justify-between">
          <h2 className="text-lg font-medium">Send Kanban email</h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700 text-xl leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="px-5 py-3 border-b grid grid-cols-2 gap-3 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-slate-500 text-xs">Subject</span>
            <input
              className="border rounded px-2 py-1 text-sm"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-slate-500 text-xs">CC (names or emails — comma/space separated)</span>
            <input
              className="border rounded px-2 py-1 text-sm font-mono"
              placeholder="@niharika, manager@intel.com, Pavel"
              value={ccRaw}
              onChange={(e) => setCcRaw(e.target.value)}
            />
          </label>
          <label className="col-span-2 flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={includeDone}
              onChange={(e) => setIncludeDone(e.target.checked)}
            />
            Include Done column ({grouped["done"]?.length ?? 0} cards)
          </label>
          <label className="col-span-2 flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={ccManagers}
              onChange={(e) => setCcManagers(e.target.checked)}
            />
            CC managers of resolved owners
            {ccManagers && ownerManagers.length > 0 && (
              <span className="text-slate-500">
                ({ownerManagers.length} added: {ownerManagers.join(", ")})
              </span>
            )}
          </label>
        </div>

        <div className="px-5 py-3 border-b text-sm">
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            <span><strong>{cardCount}</strong> card{cardCount === 1 ? "" : "s"}</span>
            <span><strong>{ownerCount}</strong> owner email{ownerCount === 1 ? "" : "s"} resolved</span>
            {cc.length > 0 && <span><strong>{cc.length}</strong> CC</span>}
            <span className="text-slate-400">mailto length: {mailto.length}</span>
          </div>
          {ownerInfo.unresolved.length > 0 && (
            <div className="mt-2 text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 text-xs">
              <strong>{ownerInfo.unresolved.length} owner token{ownerInfo.unresolved.length === 1 ? "" : "s"} not in phonebook</strong>
              {": "}
              {ownerInfo.unresolved.map((o) => `@${o}`).join(", ")}
              {" "}— add them to <code>backend/data/phonebook.json</code> to auto-route.
            </div>
          )}
          {ccInfo.unresolved.length > 0 && (
            <div className="mt-2 text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 text-xs">
              <strong>{ccInfo.unresolved.length} CC name{ccInfo.unresolved.length === 1 ? "" : "s"} not in phonebook</strong>
              {": "}
              {ccInfo.unresolved.join(", ")}
              {" "}— excluded from CC. Use full email or add to phonebook.
            </div>
          )}
          {ambiguousCount > 0 && (
            <div className="mt-2 text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 text-xs">
              <strong>{ambiguousCount} owner token{ambiguousCount === 1 ? "" : "s"} ambiguous</strong>
              {" — "}
              {Object.entries(pbData?.ambiguous ?? {}).map(([tok, ents]) =>
                `${tok} → ${ents.map((e) => e.idsid).join(" | ")}`).join("; ")}
              {" "}— qualify with full IDSID to disambiguate.
            </div>
          )}
          {mailto.tooLong && (
            <div className="mt-2 text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 text-xs">
              mailto URL is large ({mailto.length} chars). Some mail clients truncate at ~2000.
              The body is auto-trimmed; recipients can click the snapshot link for the full view.
            </div>
          )}
        </div>

        <div className="flex-1 overflow-auto p-5 text-sm">
          <div className="flex items-center justify-between mb-1">
            <div className="text-xs text-slate-500">Preview (rich HTML — what your recipients will see after pasting)</div>
            {!canRichCopy && (
              <div className="text-xs text-amber-600">
                Browser doesn't support rich clipboard — will fall back to plain-text body in mailto.
              </div>
            )}
          </div>
          <div
            className="border rounded p-3 bg-white"
            // The HTML is built locally from typed Task data with full
            // escaping (escHtml on every interpolated string in
            // buildHtmlBody) — XSS-safe to render here for preview.
            dangerouslySetInnerHTML={{ __html: htmlBody }}
          />
          <details className="mt-3">
            <summary className="text-xs text-slate-500 cursor-pointer">Plain-text fallback (used if rich clipboard fails)</summary>
            <pre className="whitespace-pre-wrap font-mono text-xs bg-slate-50 border rounded p-3 mt-1">
              {body}
            </pre>
          </details>
        </div>

        {toast && (
          <div className="mx-5 mb-2 px-3 py-2 rounded bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs">
            {toast}
          </div>
        )}

        <div className="px-5 py-3 border-t flex items-center justify-between">
          <div className="text-xs text-slate-500">
            Opens in your mail client. Sender = your real account.
          </div>
          <div className="flex gap-2">
            <button
              onClick={onCopy}
              className="px-3 py-1.5 text-sm rounded border bg-white hover:bg-slate-50"
              title="Copy plain-text body to clipboard"
            >
              {copied ? "Copied!" : "Copy text"}
            </button>
            <button
              onClick={onCopyHtml}
              className="px-3 py-1.5 text-sm rounded border bg-white hover:bg-slate-50"
              title="Copy rich HTML to clipboard — paste into Outlook/Gmail for formatted view"
            >
              {copiedHtml ? "Copied!" : "Copy HTML"}
            </button>
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-sm rounded border bg-white hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              onClick={onSend}
              disabled={sendDisabled}
              className="px-3 py-1.5 text-sm rounded bg-sky-600 text-white hover:bg-sky-700 disabled:bg-slate-300"
              title={sendDisabled ? "No resolved recipients (To or CC)" : "Copy formatted snapshot + open mail client"}
            >
              Open in mail client
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
