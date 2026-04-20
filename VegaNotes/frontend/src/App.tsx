import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { useUI } from "./store/ui";
import { FilterBar } from "./components/FilterBar/FilterBar";
import { KanbanBoard } from "./components/Kanban/KanbanBoard";
import { AgendaView } from "./components/Agenda/AgendaView";
import { TimelineView } from "./components/Timeline/TimelineView";
import { CalendarView } from "./components/Calendar/CalendarView";
import { GraphView } from "./components/Graph/GraphView";
import { CommandPalette } from "./components/CommandPalette/CommandPalette";
import { NoteEditor } from "./components/Editor/NoteEditor";
import { Sidebar } from "./components/Sidebar/Sidebar";
import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";

const qc = new QueryClient();

function ViewSwitcher({ selectedPath, setSelectedPath, draft, setDraft }: {
  selectedPath: string; setSelectedPath: (p: string) => void;
  draft: DraftMap; setDraft: (updater: (prev: DraftMap) => DraftMap) => void;
}) {
  const view = useUI((s) => s.view);
  switch (view) {
    case "kanban":   return <KanbanBoard />;
    case "agenda":   return <AgendaView />;
    case "timeline": return <TimelineView />;
    case "calendar": return <CalendarView />;
    case "graph":    return <GraphView />;
    case "editor":   return <EditorPane selectedPath={selectedPath} setSelectedPath={setSelectedPath} draft={draft} setDraft={setDraft} />;
  }
}

type DraftEntry = { body: string; saved: string; savedAt: number };
type DraftMap = Record<string, DraftEntry>;

function EditorPane({ selectedPath, setSelectedPath, draft, setDraft }: {
  selectedPath: string; setSelectedPath: (p: string) => void;
  draft: DraftMap; setDraft: (updater: (prev: DraftMap) => DraftMap) => void;
}) {
  const { data: notes = [] } = useQuery({ queryKey: ["notes"], queryFn: () => api.notes() });
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const saveTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

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

  const flushSave = async (path: string, text: string) => {
    setStatus("saving");
    try {
      await api.saveNote(path, text);
      setDraft((prev) => prev[path]
        ? { ...prev, [path]: { ...prev[path], saved: text, savedAt: Date.now() } }
        : prev);
      qc.invalidateQueries({ queryKey: ["notes"] });
      qc.invalidateQueries({ queryKey: ["tree"] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      setStatus("saved");
    } catch {
      setStatus("error");
    }
  };

  // Debounced autosave: schedules per-path timers for ANY dirty draft entry,
  // so switching between notes does not lose unsaved changes from the prior one.
  useEffect(() => {
    for (const [path, e] of Object.entries(draft)) {
      if (e.body === e.saved) continue;
      if (saveTimers.current[path]) continue;
      saveTimers.current[path] = setTimeout(() => {
        delete saveTimers.current[path];
        const cur = draft[path];
        if (cur && cur.body !== cur.saved) flushSave(path, cur.body);
      }, 800);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  // On EditorPane unmount (tab switch): immediately flush every dirty entry.
  useEffect(() => {
    return () => {
      for (const t of Object.values(saveTimers.current)) clearTimeout(t);
      saveTimers.current = {};
      // Use a fresh closure over current draft via setDraft trick.
      setDraft((prev) => {
        for (const [path, e] of Object.entries(prev)) {
          if (e.body !== e.saved) {
            api.saveNote(path, e.body).then(() => {
              setDraft((p2) => p2[path]
                ? { ...p2, [path]: { ...p2[path], saved: e.body, savedAt: Date.now() } }
                : p2);
              qc.invalidateQueries({ queryKey: ["notes"] });
              qc.invalidateQueries({ queryKey: ["tree"] });
              qc.invalidateQueries({ queryKey: ["tasks"] });
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
        if (e.body !== e.saved) {
          // Best-effort sync POST via sendBeacon if available.
          try { api.saveNote(path, e.body); } catch {}
        }
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [draft]);

  const onChange = (v: string) => {
    if (!selectedPath) return;
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
      </div>
      <div className="flex-1 overflow-auto">
        <NoteEditor value={body} onChange={onChange} />
      </div>
      <div className="flex gap-2">
        <button className="rounded bg-sky-600 text-white px-3 py-1 text-sm disabled:opacity-50"
          disabled={!selectedPath || !dirty} onClick={onSave}>Save</button>
        <NewNoteButton selectedPath={selectedPath} onCreated={setSelectedPath} />
      </div>
    </div>
  );
}

function NewNoteButton({ selectedPath, onCreated }: {
  selectedPath: string; onCreated: (p: string) => void;
}) {
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
      qc.invalidateQueries({ queryKey: ["notes"] });
      qc.invalidateQueries({ queryKey: ["tree"] });
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

function NavBar() {
  const { view, set } = useUI();
  return (
    <nav className="flex items-center gap-3 bg-white border-b px-4 py-2">
      <span className="font-bold text-lg text-sky-700">VegaNotes</span>
      {(["editor", "kanban", "agenda", "timeline", "calendar", "graph"] as const).map((v) => (
        <button key={v}
          className={`text-sm rounded px-2 py-1 ${view === v ? "bg-sky-100 text-sky-900" : "text-slate-600 hover:bg-slate-100"}`}
          onClick={() => set({ view: v })}>{v}</button>
      ))}
      <span className="ml-auto text-xs text-slate-400">⌘K</span>
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
          <Sidebar selectedPath={selectedPath} onSelect={(p) => { setSelectedPath(p); useUI.getState().set({ view: "editor" }); }} />
          <main className="flex-1 overflow-y-auto">
            <ViewSwitcher selectedPath={selectedPath} setSelectedPath={setSelectedPath} draft={draft} setDraft={setDraft} />
          </main>
        </div>
        <CommandPalette />
      </div>
    </QueryClientProvider>
  );
}
