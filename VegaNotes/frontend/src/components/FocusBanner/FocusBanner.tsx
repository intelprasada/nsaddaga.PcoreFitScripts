/**
 * FocusBanner (#266) — displays the team / project Focus of the Week
 * as free-form markdown sourced from ``_meta/focus.md`` on the backend.
 *
 * Visibility:
 *  - Hidden entirely when the backend returns 404 (file missing) AND
 *    the current user is not an admin (nothing to show, nothing to do).
 *  - Admins see an empty-state "Set focus…" affordance so they can
 *    create the banner without hand-editing the file.
 *  - All authenticated users can read; only admins can edit (RBAC
 *    enforced in the backend ``PUT /api/focus-week`` handler).
 *
 * Persistence:
 *  - Collapsed state lives in ``localStorage`` under
 *    ``veganotes.focusBanner.collapsed`` so it survives reloads but is
 *    per-browser (i.e. one teammate hiding the banner doesn't hide it
 *    for everyone).
 *  - The banner refetches on window focus and on a 60s interval so a
 *    teammate's edit shows up without a hard reload.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type FocusWeek } from "../../api/client";
import { renderFocusMarkdown } from "./focusMarkdown";

const STORAGE_KEY = "veganotes.focusBanner.collapsed";

function loadCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveCollapsed(v: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, v ? "1" : "0");
  } catch {
    /* private mode / disabled storage — collapse simply won't persist */
  }
}

export function FocusBanner() {
  const qc = useQueryClient();
  const [collapsed, setCollapsed] = useState<boolean>(loadCollapsed);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>("");

  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api.me(),
    staleTime: 60_000,
  });

  const focus = useQuery<FocusWeek | null>({
    queryKey: ["focus-week"],
    queryFn: () => api.getFocusWeek(),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });

  const save = useMutation({
    mutationFn: (markdown: string) => api.setFocusWeek(markdown),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["focus-week"] });
      setEditing(false);
    },
  });

  useEffect(() => {
    saveCollapsed(collapsed);
  }, [collapsed]);

  const isAdmin = !!me.data?.is_admin;
  const md = focus.data?.markdown?.trim() ?? "";
  const hasFocus = md.length > 0;

  if (!hasFocus && !isAdmin) return null;
  if (focus.isLoading) return null;

  const startEdit = () => {
    setDraft(focus.data?.markdown ?? "");
    setEditing(true);
  };

  return (
    <div className="vega-focus-banner border-y border-amber-200 bg-amber-50/60">
      <div className="mx-auto max-w-7xl px-4 py-2 flex items-start gap-3">
        <div className="shrink-0 text-amber-700 select-none pt-0.5" aria-hidden>📌</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <div className="text-xs font-semibold tracking-wide uppercase text-amber-800">
              Focus of the Week
            </div>
            <div className="ml-auto flex items-center gap-1">
              {isAdmin && !editing && (
                <button
                  type="button"
                  onClick={startEdit}
                  title="Edit focus of the week"
                  aria-label="Edit focus of the week"
                  className="text-xs text-amber-800 hover:text-amber-900 px-1.5 py-0.5 rounded hover:bg-amber-100"
                >
                  ✏️
                </button>
              )}
              <button
                type="button"
                onClick={() => setCollapsed((c) => !c)}
                title={collapsed ? "Expand" : "Collapse"}
                aria-label={collapsed ? "Expand focus banner" : "Collapse focus banner"}
                className="text-xs text-amber-800 hover:text-amber-900 px-1.5 py-0.5 rounded hover:bg-amber-100"
              >
                {collapsed ? "▸" : "▾"}
              </button>
            </div>
          </div>
          {!collapsed && (
            <div className="mt-1">
              {editing ? (
                <div>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={3}
                    placeholder="What is the team focusing on this week?"
                    className="w-full rounded border border-amber-300 bg-white px-2 py-1 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-amber-400"
                    autoFocus
                  />
                  <div className="mt-1.5 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => save.mutate(draft)}
                      disabled={save.isPending}
                      className="rounded bg-amber-600 hover:bg-amber-700 text-white text-xs px-2.5 py-1 disabled:opacity-50"
                    >
                      {save.isPending ? "Saving…" : "Save"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(false)}
                      className="rounded border border-slate-300 text-xs px-2.5 py-1 hover:bg-slate-50"
                    >
                      Cancel
                    </button>
                    {save.isError && (
                      <span className="text-xs text-rose-700">Save failed.</span>
                    )}
                  </div>
                </div>
              ) : hasFocus ? (
                <div
                  className="vega-focus-content text-sm text-slate-800 leading-snug"
                  dangerouslySetInnerHTML={{ __html: renderFocusMarkdown(md) }}
                />
              ) : (
                <button
                  type="button"
                  onClick={startEdit}
                  className="text-sm text-amber-800 hover:text-amber-900 italic"
                >
                  Set the team focus for this week…
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
