import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { useUI } from "./store/ui";
import { FilterBar } from "./components/FilterBar/FilterBar";
import { KanbanBoard } from "./components/Kanban/KanbanBoard";
import { AgendaView } from "./components/Agenda/AgendaView";
import { TimelineView } from "./components/Timeline/TimelineView";
import { CalendarView } from "./components/Calendar/CalendarView";
import { GraphView } from "./components/Graph/GraphView";
import { MyTasksView } from "./components/Tasks/MyTasksView";
import { MeView } from "./components/Me/MeView";
import { UnlockToast } from "./components/Me/UnlockToast";
import { CommandPalette } from "./components/CommandPalette/CommandPalette";
import { NoteEditor } from "./components/Editor/NoteEditor";
import { Sidebar } from "./components/Sidebar/Sidebar";
import { AdminPanel } from "./components/Admin/AdminPanel";
import { ChangePasswordModal } from "./components/Auth/ChangePasswordModal";
import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import { copyToClipboard } from "./lib/clipboard";

const qc = new QueryClient();

function ViewSwitcher({ selectedPath, setSelectedPath, draft, setDraft }: {
  selectedPath: string; setSelectedPath: (p: string) => void;
  draft: DraftMap; setDraft: (updater: (prev: DraftMap) => DraftMap) => void;
}) {
  const view = useUI((s) => s.view);
  switch (view) {
    case "kanban":    return <KanbanBoard />;
    case "agenda":    return <AgendaView />;
    case "timeline":  return <TimelineView />;
    case "calendar":  return <CalendarView />;
    case "graph":     return <GraphView />;
    case "admin":     return <AdminPanel />;
    case "my-tasks":  return <MyTasksView />;
    case "me":        return <MeView />;
    case "editor":    return <EditorPane selectedPath={selectedPath} setSelectedPath={setSelectedPath} draft={draft} setDraft={setDraft} />;
  }
}

type DraftEntry = { body: string; saved: string; savedAt: number };
type DraftMap = Record<string, DraftEntry>;

function EditorPane({ selectedPath, setSelectedPath, draft, setDraft }: {
  selectedPath: string; setSelectedPath: (p: string) => void;
  draft: DraftMap; setDraft: (updater: (prev: DraftMap) => DraftMap) => void;
}) {
  const qcLocal = useQueryClient();
  const { data: notes = [] } = useQuery({ queryKey: ["notes"], queryFn: () => api.notes() });
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.me() });
  const { data: projects = [] } = useQuery({ queryKey: ["projects"], queryFn: () => api.projects() });
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const saveTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  // Derive write permission for the currently-selected note.
  // - Admins can write anything.
  // - Root notes (no "/" in path) are writable by everyone.
  // - Project notes require manager role in that project.
  const projectName = selectedPath.includes("/") ? selectedPath.split("/")[0] : null;
  const projectRole = projectName
    ? (projects.find((p) => p.name === projectName)?.role ?? null)
    : null;
  const canWrite = !selectedPath
    ? false
    : (me?.is_admin || projectName === null || projectRole === "manager");

  // Timestamps for global-query throttling (tree + tasks refresh at most every 5s).
  const lastGlobalInvalidate = useRef<number>(0);
  const GLOBAL_THROTTLE_MS = 5_000;

  // Mirror of the live notes list so cleanup/beforeunload handlers can check
  // whether a draft path still exists on the server.
  const knownPathsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    knownPathsRef.current = new Set(notes.map((n) => n.path));
  }, [notes]);

  useEffect(() => {
    if (!selectedPath && notes[0]) setSelectedPath(notes[0].path);
  }, [notes, selectedPath, setSelectedPath]);

  // Load body from server only if we don't already have a draft cached for this path.
  useEffect(() => {
    let cancelled = false;
    if (!selectedPath) return;
    if (draft[selectedPath]) return;
    const meta = notes.find((n) => n.path === selectedPath);
    if (!meta) return;
    (async () => {
      const n = await api.note(meta.id);
      if (cancelled) return;
      setDraft((prev) => prev[selectedPath]
        ? prev
        : { ...prev, [selectedPath]: { body: n.body_md, saved: n.body_md, savedAt: Date.now() } });
    })();
    return () => { cancelled = true; };
  }, [selectedPath, notes, draft, setDraft]);

  const entry = selectedPath ? draft[selectedPath] : undefined;
  const body = entry?.body ?? "";
  const dirty = !!entry && entry.body !== entry.saved;

  // Scoped invalidations: always refresh only the current note.
  // Expensive global queries (tree, tasks) are throttled to once per 5s —
  // reduces backend API calls by ~73% during active editing sessions.
  const invalidateAfterSave = (path: string) => {
    const noteId = notes.find((n) => n.path === path)?.id;
    if (noteId != null) qcLocal.invalidateQueries({ queryKey: ["note", noteId] });
    qcLocal.invalidateQueries({ queryKey: ["notes"] }); // sidebar list
    const now = Date.now();
    if (now - lastGlobalInvalidate.current >= GLOBAL_THROTTLE_MS) {
      lastGlobalInvalidate.current = now;
      qcLocal.invalidateQueries({ queryKey: ["tree"] });
      qcLocal.invalidateQueries({ queryKey: ["tasks"] });
    }
  };

  const flushSave = async (path: string, text: string) => {
    setStatus("saving");
    try {
      await api.saveNote(path, text);
      setDraft((prev) => prev[path]
        ? { ...prev, [path]: { ...prev[path], saved: text, savedAt: Date.now() } }
        : prev);
      invalidateAfterSave(path);
      setStatus("saved");
    } catch {
      setStatus("error");
    }
  };

  // Debounced autosave: schedules per-path timers for ANY dirty draft entry.
  useEffect(() => {
    if (!canWrite) return; // members can't save — don't even schedule
    for (const [path, e] of Object.entries(draft)) {
      if (e.body === e.saved) continue;
      if (saveTimers.current[path]) continue;
      saveTimers.current[path] = setTimeout(() => {
        delete saveTimers.current[path];
        const cur = draft[path];
        if (!cur || cur.body === cur.saved) return;
        if (cur.savedAt > 0 && !knownPathsRef.current.has(path)) return;
        flushSave(path, cur.body);
      }, 800);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  // On EditorPane unmount (tab switch): immediately flush every dirty entry.
  useEffect(() => {
    return () => {
      for (const t of Object.values(saveTimers.current)) clearTimeout(t);
      saveTimers.current = {};
      setDraft((prev) => {
        for (const [path, e] of Object.entries(prev)) {
          if (e.body !== e.saved
              && (e.savedAt === 0 || knownPathsRef.current.has(path))) {
            api.saveNote(path, e.body).then(() => {
              setDraft((p2) => p2[path]
                ? { ...p2, [path]: { ...p2[path], saved: e.body, savedAt: Date.now() } }
                : p2);
              // Force a final global refresh on unmount (no throttle).
              qcLocal.invalidateQueries({ queryKey: ["notes"] });
              qcLocal.invalidateQueries({ queryKey: ["tree"] });
              qcLocal.invalidateQueries({ queryKey: ["tasks"] });
            }).catch(() => {});
          }
        }
        return prev;
      });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Save before page unload too.
  useEffect(() => {
    const handler = () => {
      for (const [path, e] of Object.entries(draft)) {
        if (e.body !== e.saved
            && (e.savedAt === 0 || knownPathsRef.current.has(path))) {
          // Best-effort sync POST via sendBeacon if available.
          try { api.saveNote(path, e.body); } catch {}
        }
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [draft]);

  const onChange = (v: string) => {
    if (!selectedPath || !canWrite) return;
    setDraft((prev) => ({
      ...prev,
      [selectedPath]: {
        body: v,
        saved: prev[selectedPath]?.saved ?? v,
        savedAt: prev[selectedPath]?.savedAt ?? 0,
      },
    }));
  };

  const onSave = () => { if (selectedPath && entry) flushSave(selectedPath, entry.body); };

  const onStampIds = async () => {
    if (!selectedPath || !entry) return;
    if (dirty) {
      const ok = window.confirm(
        `${selectedPath} has unsaved changes. Save them before stamping IDs?`,
      );
      if (!ok) return;
      await flushSave(selectedPath, entry.body);
    }
    setStatus("saving");
    try {
      const r = await api.stampTaskIds(selectedPath);
      setDraft((prev) => ({
        ...prev,
        [selectedPath]: { body: r.body_md, saved: r.body_md, savedAt: Date.now() },
      }));
      qcLocal.invalidateQueries({ queryKey: ["notes"] });
      qcLocal.invalidateQueries({ queryKey: ["tasks"] });
      setStatus("saved");
      if (r.injected === 0) {
        alert("All tasks already have IDs.");
      } else {
        alert(`Stamped ${r.injected} new task ID${r.injected === 1 ? "" : "s"}.`);
      }
    } catch (e: any) {
      setStatus("error");
      alert(`Stamp IDs failed: ${e?.message ?? e}`);
    }
  };

  const onRefresh = async () => {
    if (!selectedPath) return;
    const meta = notes.find((n) => n.path === selectedPath);
    if (!meta) return;
    if (dirty && !window.confirm(
      `${selectedPath} has unsaved changes. Reload from disk and discard them?`)) {
      return;
    }
    setStatus("saving");
    try {
      const n = await api.note(meta.id);
      setDraft((prev) => ({
        ...prev,
        [selectedPath]: { body: n.body_md, saved: n.body_md, savedAt: Date.now() },
      }));
      qcLocal.invalidateQueries({ queryKey: ["notes"] });
      qcLocal.invalidateQueries({ queryKey: ["tree"] });
      qcLocal.invalidateQueries({ queryKey: ["tasks"] });
      setStatus("saved");
    } catch {
      setStatus("error");
    }
  };

  const statusText = !selectedPath
    ? ""
    : status === "saving" ? "saving…"
    : status === "error" ? "save failed"
    : dirty ? "unsaved"
    : entry?.savedAt ? "saved" : "";

  return (
    <div className="p-4 space-y-2 h-full flex flex-col">
      <div className="text-sm text-slate-500 flex items-center gap-3">
        <span>{selectedPath || "(no notes — pick or create one)"}</span>
        {statusText && (
          <span className={`text-xs ${status === "error" ? "text-rose-600" : dirty ? "text-amber-600" : "text-emerald-600"}`}>
            {statusText}
          </span>
        )}
        {selectedPath && !canWrite && projectRole && (
          <span className="ml-auto text-xs bg-amber-100 text-amber-800 rounded px-2 py-0.5 font-medium">
            👁 read-only · member of {projectName}
          </span>
        )}
      </div>
      <div className="flex-1 overflow-auto">
        <NoteEditor value={body} onChange={onChange} readOnly={!canWrite} />
      </div>
      <div className="flex gap-2 flex-wrap">
        <button className="rounded bg-sky-600 text-white px-3 py-1 text-sm disabled:opacity-50"
          disabled={!canWrite || !dirty} onClick={onSave}>Save</button>
        <button className="rounded border px-3 py-1 text-sm disabled:opacity-50"
          disabled={!selectedPath} onClick={onRefresh}
          title="Reload this note from disk (e.g. after editing in Vim or rolling to next week)">
          ↻ Refresh
        </button>
        <button className="rounded border px-3 py-1 text-sm disabled:opacity-50"
          disabled={!canWrite || !selectedPath} onClick={onStampIds}
          title="Inject stable #id tokens into every !task / !AR line that doesn't have one. Required for cross-week deduplication.">
          # Stamp IDs
        </button>
        <NewNoteButton selectedPath={selectedPath} onCreated={setSelectedPath} />
        <NextWeekButton selectedPath={selectedPath} entry={entry}
          flushSave={flushSave} onCreated={setSelectedPath} />
        <EditInVimButton selectedPath={selectedPath} entry={entry} flushSave={flushSave} />
      </div>
    </div>
  );
}

function NewNoteButton({ selectedPath, onCreated }: {
  selectedPath: string; onCreated: (p: string) => void;
}) {
  const qcLocal = useQueryClient();
  const [showing, setShowing] = useState(false);
  const [name, setName] = useState("");
  const project = selectedPath.includes("/") ? selectedPath.split("/")[0] : "";
  if (!showing) {
    return (
      <button onClick={() => setShowing(true)}
        className="rounded border px-3 py-1 text-sm">+ new note</button>
    );
  }
  return (
    <form className="flex gap-1" onSubmit={async (e) => {
      e.preventDefault();
      if (!name.trim()) return;
      const path = (project ? `${project}/` : "") + name.replace(/\.md$/, "") + ".md";
      await api.saveNote(path, `# ${name}\n`);
      qcLocal.invalidateQueries({ queryKey: ["notes"] });
      qcLocal.invalidateQueries({ queryKey: ["tree"] });
      onCreated(path);
      setShowing(false); setName("");
    }}>
      <input autoFocus className="rounded border px-2 py-1 text-sm" value={name}
        placeholder={`${project ? project + "/" : ""}filename`}
        onChange={(e) => setName(e.target.value)} />
      <button className="rounded bg-sky-600 text-white px-2 text-sm">create</button>
    </form>
  );
}

function NextWeekButton({ selectedPath, entry, flushSave, onCreated }: {
  selectedPath: string;
  entry: { body: string; saved: string; savedAt: number } | undefined;
  flushSave: (path: string, text: string) => Promise<void>;
  onCreated: (p: string) => void;
}) {
  const qcLocal = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const enabled = !!selectedPath && /(?:^|[^a-z])ww\d+/i.test(selectedPath);
  const onClick = async () => {
    if (!selectedPath) return;
    setErr(null);
    setBusy(true);
    try {
      if (entry && entry.body !== entry.saved) {
        await flushSave(selectedPath, entry.body);
      }
      let res;
      try {
        res = await api.rollNoteNextWeek(selectedPath, false);
      } catch (e: any) {
        const msg = String(e?.message ?? e);
        if (msg.includes("409")) {
          if (!confirm("Next-week note already exists. Overwrite?")) {
            setBusy(false); return;
          }
          res = await api.rollNoteNextWeek(selectedPath, true);
        } else if (msg.includes("400")) {
          setErr("Filename must contain a 'wwN' token (e.g. 'ww16').");
          setBusy(false); return;
        } else {
          throw e;
        }
      }
      qcLocal.invalidateQueries({ queryKey: ["notes"] });
      qcLocal.invalidateQueries({ queryKey: ["tree"] });
      qcLocal.invalidateQueries({ queryKey: ["tasks"] });
      onCreated(res!.path);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex items-center gap-2">
      <button onClick={onClick} disabled={!enabled || busy}
        title={enabled
          ? "Create next-week copy of this note (drops done items, bumps wwN→wwN+1)"
          : "Select a note whose filename contains 'wwN' (e.g. ww16)"}
        className="rounded border border-emerald-600 text-emerald-700 px-3 py-1 text-sm disabled:opacity-40 hover:bg-emerald-50">
        {busy ? "rolling…" : "→ Next Week"}
      </button>
      {err && <span className="text-xs text-rose-600">{err}</span>}
    </div>
  );
}

function EditInVimButton({ selectedPath, entry, flushSave }: {
  selectedPath: string;
  entry: { body: string; saved: string; savedAt: number } | undefined;
  flushSave: (path: string, text: string) => Promise<void>;
}) {
  const [info, setInfo] = useState<{ abs_path: string; vim_cmd: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const open = async () => {
    if (!selectedPath) return;
    setErr(null); setBusy(true);
    try {
      // Flush any pending edits so vim opens the latest content.
      if (entry && entry.body !== entry.saved) {
        await flushSave(selectedPath, entry.body);
      }
      const r = await api.noteAbsPath(selectedPath);
      setInfo(r);
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const copyCmd = async () => {
    if (!info) return;
    const ok = await copyToClipboard(info.vim_cmd);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } else {
      setErr("Could not copy — select the command above and copy manually.");
      setTimeout(() => setErr(null), 3000);
    }
  };

  return (
    <>
      <button onClick={open} disabled={!selectedPath || busy}
        title="Show absolute path so you can edit this note in your local vim with your own ftplugin/colorscheme"
        className="rounded border border-violet-600 text-violet-700 px-3 py-1 text-sm disabled:opacity-40 hover:bg-violet-50">
        Edit in Vim
      </button>
      {err && <span className="text-xs text-rose-600 self-center">{err}</span>}
      {info && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
          onClick={() => setInfo(null)}>
          <div className="bg-white rounded shadow-lg p-4 max-w-2xl w-full mx-4 space-y-3"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Edit in Vim</h3>
              <button onClick={() => setInfo(null)} className="text-slate-500 hover:text-slate-700">✕</button>
            </div>
            <p className="text-sm text-slate-600">
              Run this in any terminal on this host. Changes are auto-detected
              and reindexed. Your <code>vim</code> ftplugin/colorscheme applies.
            </p>
            <pre className="bg-slate-900 text-slate-100 rounded p-3 text-xs overflow-x-auto select-all">
{info.vim_cmd}
            </pre>
            <div className="flex gap-2 justify-end">
              <button onClick={copyCmd}
                className="rounded bg-violet-600 text-white px-3 py-1 text-sm hover:bg-violet-700">
                {copied ? "Copied!" : "Copy command"}
              </button>
              <button onClick={() => setInfo(null)}
                className="rounded border px-3 py-1 text-sm">Close</button>
            </div>
            <p className="text-xs text-slate-500">
              Absolute path: <code className="break-all">{info.abs_path}</code>
            </p>
          </div>
        </div>
      )}
    </>
  );
}

function NavBar() {
  const { view, set } = useUI();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.me() });
  const [changingPw, setChangingPw] = useState(false);
  const tabs: ("editor" | "kanban" | "agenda" | "timeline" | "calendar" | "graph" | "admin" | "my-tasks" | "me")[] = [
    "my-tasks", "editor", "kanban", "agenda", "timeline", "calendar", "graph", "me",
  ];
  if (me?.is_admin) tabs.push("admin");

  const logout = async () => {
    // HTTP Basic has no real "logout" — there's no portable, JS-driven way
    // to clear the browser's cached credentials. We do the best we can:
    //
    //   1. Drop our React Query cache so no stale identity lingers in the UI.
    //   2. Fire two requests with bogus Authorization headers so the
    //      browser's credential cache for this origin is overwritten with
    //      garbage. (One request isn't always enough on Chrome.)
    //   3. Hard-replace the URL with a cache-buster so bfcache can't restore
    //      the previous session.
    //
    // This is reliable on Firefox; usually-works on Chrome/Edge/Safari.
    // For deterministic multi-user testing, an incognito/private window per
    // identity is still the gold-standard approach.
    qc.clear();
    for (let i = 0; i < 2; i++) {
      try {
        await fetch("/api/me", {
          headers: { Authorization: `Basic ${btoa(`logout-${Date.now()}-${i}:x`)}` },
          cache: "no-store",
        });
      } catch { /* expected 401 */ }
    }
    window.location.replace("/?_logout=" + Date.now());
  };

  return (
    <nav className="flex items-center gap-3 bg-white border-b px-4 py-2">
      <span className="font-bold text-lg text-sky-700">VegaNotes</span>
      {tabs.map((v) => (
        <button key={v}
          className={`text-sm rounded px-2 py-1 ${view === v ? "bg-sky-100 text-sky-900" : "text-slate-600 hover:bg-slate-100"}`}
          onClick={() => set({ view: v })}>{v}</button>
      ))}
      {me && (
        <span className="ml-auto text-xs text-slate-500">
          {me.name}{me.is_admin ? " · admin" : ""}
        </span>
      )}
      {me && (
        <button
          onClick={() => setChangingPw(true)}
          className="text-xs text-slate-600 hover:bg-slate-100 rounded px-2 py-1 border"
          title="Change your password"
        >
          change password
        </button>
      )}
      <button
        onClick={logout}
        className="text-xs text-slate-600 hover:bg-slate-100 rounded px-2 py-1 border"
        title="Sign out. Note: HTTP Basic credentials are cached by the browser; if the prompt re-appears with the old user already accepted, hard-refresh (Ctrl+Shift+R) or use a private/incognito window for the cleanest switch."
      >
        logout
      </button>
      <span className="text-xs text-slate-400">⌘K</span>
      {changingPw && <ChangePasswordModal onClose={() => setChangingPw(false)} />}
    </nav>
  );
}

export default function App() {
  const [selectedPath, setSelectedPath] = useState<string>("");
  const [draft, setDraft] = useState<DraftMap>({});
  return (
    <QueryClientProvider client={qc}>
      <div className="min-h-screen flex flex-col">
        <NavBar />
        <FilterBar />
        <div className="flex-1 flex overflow-hidden">
          <Sidebar
            selectedPath={selectedPath}
            onSelect={(p) => { setSelectedPath(p); useUI.getState().set({ view: "editor" }); }}
            onAfterDelete={(matches) => {
              setDraft((prev) => {
                const next: DraftMap = {};
                for (const [path, e] of Object.entries(prev)) {
                  if (!matches(path)) next[path] = e;
                }
                return next;
              });
              if (matches(selectedPath)) setSelectedPath("");
            }}
          />
          <main className="flex-1 overflow-y-auto">
            <ViewSwitcher selectedPath={selectedPath} setSelectedPath={setSelectedPath} draft={draft} setDraft={setDraft} />
          </main>
        </div>
        <CommandPalette />
        <UnlockToast />
      </div>
    </QueryClientProvider>
  );
}
