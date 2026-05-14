import { useEffect, useMemo, useState } from "react";
import type { Task } from "../../api/client";
import type { FilterState } from "../../store/ui";
import {
  buildMailto,
  buildPlainBody,
  countOpen,
  defaultSubject,
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

  const ownerInfo = useMemo(() => {
    const all = visibleTasks.flatMap((t) => t.owners ?? []);
    return partitionOwners(all);
  }, [visibleTasks]);

  const cc = useMemo(() => parseCcList(ccRaw), [ccRaw]);

  const snapshotUrl = typeof window !== "undefined" ? window.location.href : "";

  const body = useMemo(
    () => buildPlainBody({ filters, grouped, columns, snapshotUrl, includeDone }),
    [filters, grouped, columns, snapshotUrl, includeDone],
  );

  const mailto = useMemo(() => {
    const safeBody = truncateBodyForMailto(body);
    return buildMailto({ to: ownerInfo.resolved, cc, subject, body: safeBody });
  }, [ownerInfo.resolved, cc, subject, body]);

  const cardCount = visibleTasks.length;
  const ownerCount = ownerInfo.resolved.length;

  const sendDisabled = ownerCount === 0 && cc.length === 0;

  const onSend = () => {
    window.location.href = mailto.url;
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
            <span className="text-slate-500 text-xs">CC (comma/space separated)</span>
            <input
              className="border rounded px-2 py-1 text-sm font-mono"
              placeholder="manager@intel.com, team-dl@intel.com"
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
              <strong>{ownerInfo.unresolved.length} owner token{ownerInfo.unresolved.length === 1 ? "" : "s"} not resolvable as email</strong>
              {": "}
              {ownerInfo.unresolved.map((o) => `@${o}`).join(", ")}
              {" "}— will appear in body only. Phonebook resolution lands in Phase 2 (#210).
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
          <div className="text-xs text-slate-500 mb-1">Preview (plain text)</div>
          <pre className="whitespace-pre-wrap font-mono text-xs bg-slate-50 border rounded p-3">
            {body}
          </pre>
        </div>

        <div className="px-5 py-3 border-t flex items-center justify-between">
          <div className="text-xs text-slate-500">
            Opens in your mail client. Sender = your real account.
          </div>
          <div className="flex gap-2">
            <button
              onClick={onCopy}
              className="px-3 py-1.5 text-sm rounded border bg-white hover:bg-slate-50"
            >
              {copied ? "Copied!" : "Copy body"}
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
              title={sendDisabled ? "No resolved recipients (To or CC)" : "Open in mail client"}
            >
              Open in mail client
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
