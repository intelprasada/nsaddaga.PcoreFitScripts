import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { useUI } from "../../store/ui";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const setView = useUI((s) => s.set);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); setOpen((o) => !o); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/30 flex items-start justify-center pt-[15vh] z-50"
         onClick={() => setOpen(false)}>
      <Command className="bg-white rounded-lg shadow-xl w-[520px]" onClick={(e) => e.stopPropagation()}>
        <Command.Input className="w-full p-3 outline-none border-b" placeholder="Jump to view, task…" />
        <Command.List className="max-h-[300px] overflow-y-auto p-2">
          <Command.Group heading="Views">
            {(["editor", "kanban", "agenda", "timeline", "calendar", "graph"] as const).map((v) => (
              <Command.Item key={v} onSelect={() => { setView({ view: v }); setOpen(false); }}>
                Switch to {v}
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}
