import { useEffect, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { api, type TreeNode } from "../../api/client";
import { ManageMembersModal } from "./ManageMembersModal";

interface Props {
  selectedPath: string;
  onSelect: (path: string) => void;
  /**
   * Called after a note or project is deleted. Receives a predicate that
   * matches deleted note paths. App uses this to drop in-memory draft entries
   * so unsaved buffers can't resurrect the file via autosave.
   */
  onAfterDelete?: (matches: (path: string) => boolean) => void;
}

type MenuKind =
  | { kind: "project"; project: string; role: "manager" | "member" }
  | { kind: "note"; path: string; id: number | null; project: string | null };

interface MenuState {
  x: number;
  y: number;
  target: MenuKind;
}

/**
 * Project/notes tree with right-click context menu:
 *   - On a project: "New note", "Delete project" (managers only).
 *   - On a note:    "Delete note" (managers only).
 * Top-level entries are projects (folders under notes/). Loose root-level
 * files appear under a "(no project)" group at the bottom.
 */
export function Sidebar({ selectedPath, onSelect, onAfterDelete }: Props) {
  const qc = useQueryClient();
  const { data: tree = [] } = useQuery<TreeNode[]>({
    queryKey: ["tree"],
    queryFn: () => api.tree(),
  });
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [newNoteFor, setNewNoteFor] = useState<string | null>(null);
  const [newNoteName, setNewNoteName] = useState("");
  const [manageMembersFor, setManageMembersFor] = useState<string | null>(null);

  // Dismiss the context menu on any outside click / Escape.
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ["tree"] });
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["notes"] });
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["agenda"] });
  };

  const create = useMutation({
    mutationFn: (name: string) => api.createProject(name),
    onSuccess: () => {
      setCreating(false);
      setNewName("");
      refreshAll();
    },
  });

  const delProject = useMutation({
    mutationFn: (name: string) => api.deleteProject(name),
    onSuccess: (_d, name) => {
      onAfterDelete?.((p) => p === name || p.startsWith(`${name}/`));
      refreshAll();
      if (selectedPath.startsWith(`${name}/`)) onSelect("");
    },
    onError: (e: any) => alert(`Delete failed: ${e?.message ?? e}`),
  });

  const delNote = useMutation({
    mutationFn: ({ id }: { id: number; path: string }) => api.deleteNote(id),
    onSuccess: (_d, vars) => {
      onAfterDelete?.((p) => p === vars.path);
      refreshAll();
      if (selectedPath === vars.path) onSelect("");
    },
    onError: (e: any) => alert(`Delete failed: ${e?.message ?? e}`),
  });

  const createNote = useMutation({
    mutationFn: ({ project, name }: { project: string; name: string }) => {
      let n = name.trim();
      if (!n) throw new Error("name required");
      if (!n.endsWith(".md")) n += ".md";
      const path = `${project}/${n}`;
      const seed = `# ${n.replace(/\.md$/, "")}\n\n`;
      return api.saveNote(path, seed, "").then(() => path);
    },
    onSuccess: (path) => {
      setNewNoteFor(null);
      setNewNoteName("");
      refreshAll();
      onSelect(path);
    },
    onError: (e: any) => alert(`Create note failed: ${e?.message ?? e}`),
  });

  return (
    <aside className="w-64 shrink-0 border-r bg-slate-50 overflow-y-auto p-3 text-sm relative">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs uppercase tracking-wide text-slate-500">Projects</h3>
        <button
          onClick={() => setCreating((c) => !c)}
          title="New project"
          className="text-sky-600 hover:text-sky-800 text-xs"
        >
          + new
        </button>
      </div>
      {creating && (
        <form
          className="mb-3 flex gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            if (newName.trim()) create.mutate(newName.trim());
          }}
        >
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="project name"
            className="flex-1 rounded border px-2 py-1 text-xs"
          />
          <button className="rounded bg-sky-600 text-white px-2 text-xs">add</button>
        </form>
      )}
      <ul className="space-y-3">
        {tree.map((node) => (
          <li key={node.project ?? "__root__"}>
            <div
              className="flex items-center justify-between text-slate-700 font-semibold cursor-default"
              onContextMenu={(e) => {
                if (!node.project) return;
                e.preventDefault();
                setMenu({
                  x: e.clientX,
                  y: e.clientY,
                  target: { kind: "project", project: node.project, role: node.role },
                });
              }}
              title={node.project ? "Right-click for actions" : undefined}
            >
              <span>{node.project ?? "(no project)"}</span>
              {node.project && (
                <span
                  className={`text-[10px] uppercase rounded px-1.5 py-0.5 ${
                    node.role === "manager"
                      ? "bg-violet-100 text-violet-700"
                      : "bg-slate-200 text-slate-600"
                  }`}
                  title={`Your role: ${node.role}`}
                >
                  {node.role}
                </span>
              )}
            </div>
            {newNoteFor === node.project && (
              <form
                className="mt-1 ml-2 flex gap-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (node.project)
                    createNote.mutate({ project: node.project, name: newNoteName });
                }}
              >
                <input
                  autoFocus
                  value={newNoteName}
                  onChange={(e) => setNewNoteName(e.target.value)}
                  placeholder="note-name.md"
                  className="flex-1 rounded border px-2 py-1 text-xs"
                />
                <button className="rounded bg-sky-600 text-white px-2 text-xs">
                  add
                </button>
              </form>
            )}
            <ul className="mt-1 ml-2 border-l border-slate-200">
              {node.notes.length === 0 && (
                <li className="pl-2 text-xs italic text-slate-400">empty</li>
              )}
              {node.notes.map((n) => {
                const active = n.path === selectedPath;
                return (
                  <li key={n.path}>
                    <button
                      onClick={() => onSelect(n.path)}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        setMenu({
                          x: e.clientX,
                          y: e.clientY,
                          target: {
                            kind: "note",
                            path: n.path,
                            id: n.id,
                            project: node.project,
                          },
                        });
                      }}
                      className={`block w-full text-left pl-2 py-1 text-xs truncate ${
                        active
                          ? "bg-sky-100 text-sky-900"
                          : "text-slate-600 hover:bg-slate-100"
                      }`}
                      title={`${n.path} — right-click for actions`}
                    >
                      {n.title || n.path}
                    </button>
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
        {tree.length === 0 && (
          <li className="text-xs italic text-slate-400">
            No projects. Click <em>+ new</em> to create one.
          </li>
        )}
      </ul>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={buildMenuItems(menu.target, {
            onNewNote: (project) => {
              setNewNoteFor(project);
              setNewNoteName("");
              setMenu(null);
            },
            onManageMembers: (project) => {
              setManageMembersFor(project);
              setMenu(null);
            },
            onDeleteProject: (project) => {
              if (
                window.confirm(
                  `Delete project "${project}" and ALL its notes? This cannot be undone.`,
                )
              )
                delProject.mutate(project);
              setMenu(null);
            },
            onDeleteNote: (id, path) => {
              if (id == null) {
                alert("Note isn't indexed yet — try again in a moment.");
                setMenu(null);
                return;
              }
              if (window.confirm(`Delete note "${path}"?`)) delNote.mutate({ id, path });
              setMenu(null);
            },
          })}
        />
      )}

      {manageMembersFor && (
        <ManageMembersModal
          project={manageMembersFor}
          onClose={() => setManageMembersFor(null)}
        />
      )}
    </aside>
  );
}

interface MenuItem {
  label: string;
  onClick: () => void;
  danger?: boolean;
}

function buildMenuItems(
  target: MenuKind,
  cb: {
    onNewNote: (project: string) => void;
    onManageMembers: (project: string) => void;
    onDeleteProject: (project: string) => void;
    onDeleteNote: (id: number | null, path: string) => void;
  },
): MenuItem[] {
  if (target.kind === "project") {
    const items: MenuItem[] = [
      { label: "New note in project", onClick: () => cb.onNewNote(target.project) },
    ];
    if (target.role === "manager") {
      items.push({
        label: "Manage members…",
        onClick: () => cb.onManageMembers(target.project),
      });
      items.push({
        label: "Delete project…",
        danger: true,
        onClick: () => cb.onDeleteProject(target.project),
      });
    }
    return items;
  }
  return [
    {
      label: "Delete note…",
      danger: true,
      onClick: () => cb.onDeleteNote(target.id, target.path),
    },
  ];
}

function ContextMenu({
  x,
  y,
  items,
}: {
  x: number;
  y: number;
  items: MenuItem[];
}) {
  return (
    <ul
      className="fixed z-50 min-w-[10rem] rounded border bg-white shadow-lg py-1 text-xs"
      style={{ left: x, top: y }}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((it, i) => (
        <li key={i}>
          <button
            onClick={it.onClick}
            className={`w-full text-left px-3 py-1.5 hover:bg-slate-100 ${
              it.danger ? "text-rose-600" : "text-slate-700"
            }`}
          >
            {it.label}
          </button>
        </li>
      ))}
    </ul>
  );
}
