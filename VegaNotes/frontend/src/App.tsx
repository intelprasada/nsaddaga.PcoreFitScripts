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
import { useEffect, useState } from "react";
import { api } from "./api/client";

const qc = new QueryClient();

function ViewSwitcher({ selectedPath, setSelectedPath }: {
  selectedPath: string; setSelectedPath: (p: string) => void;
}) {
  const view = useUI((s) => s.view);
  switch (view) {
    case "kanban":   return <KanbanBoard />;
    case "agenda":   return <AgendaView />;
    case "timeline": return <TimelineView />;
    case "calendar": return <CalendarView />;
    case "graph":    return <GraphView />;
    case "editor":   return <EditorPane selectedPath={selectedPath} setSelectedPath={setSelectedPath} />;
  }
}

function EditorPane({ selectedPath, setSelectedPath }: {
  selectedPath: string; setSelectedPath: (p: string) => void;
}) {
  const [body, setBody] = useState<string>("");
  const { data: notes = [] } = useQuery({ queryKey: ["notes"], queryFn: () => api.notes() });

  useEffect(() => {
    if (!selectedPath && notes[0]) setSelectedPath(notes[0].path);
  }, [notes, selectedPath, setSelectedPath]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedPath) { setBody(""); return; }
    (async () => {
      const meta = notes.find((n) => n.path === selectedPath);
      if (!meta) return;
      const n = await api.note(meta.id);
      if (!cancelled) setBody(n.body_md);
    })();
    return () => { cancelled = true; };
  }, [selectedPath, notes]);

  const onSave = async () => {
    if (!selectedPath) return;
    await api.saveNote(selectedPath, body);
    qc.invalidateQueries({ queryKey: ["notes"] });
    qc.invalidateQueries({ queryKey: ["tree"] });
    qc.invalidateQueries({ queryKey: ["tasks"] });
  };

  return (
    <div className="p-4 space-y-2 h-full flex flex-col">
      <div className="text-sm text-slate-500">{selectedPath || "(no notes — pick or create one)"}</div>
      <div className="flex-1 overflow-auto">
        <NoteEditor value={body} onChange={setBody} />
      </div>
      <div className="flex gap-2">
        <button className="rounded bg-sky-600 text-white px-3 py-1 text-sm disabled:opacity-50"
          disabled={!selectedPath} onClick={onSave}>Save</button>
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
  return (
    <QueryClientProvider client={qc}>
      <div className="min-h-screen flex flex-col">
        <NavBar />
        <FilterBar />
        <div className="flex-1 flex overflow-hidden">
          <Sidebar selectedPath={selectedPath} onSelect={(p) => { setSelectedPath(p); useUI.getState().set({ view: "editor" }); }} />
          <main className="flex-1 overflow-y-auto">
            <ViewSwitcher selectedPath={selectedPath} setSelectedPath={setSelectedPath} />
          </main>
        </div>
        <CommandPalette />
      </div>
    </QueryClientProvider>
  );
}
