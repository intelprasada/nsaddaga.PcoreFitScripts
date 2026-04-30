import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { api, type TreeNode } from "../../api/client";
import { ManageMembersModal } from "./ManageMembersModal";
import {
  useSidebarPrefs,
  applySidebarPrefs,
  projectKey,
  projectLabel,
} from "../../store/sidebarPrefs";

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
  | { kind: "project"; project: string; role: "manager" | "member"; key: string }
  | { kind: "note"; path: string; id: number | null; project: string | null };

interface MenuState {
  x: number;
  y: number;
  target: MenuKind;
}

/**
 * Project/notes tree with right-click context menu and per-user view
 * preferences (collapse, hide).  See issue #157 for the full UX spec.
 *
 * Right-click menu:
 *   - Project: "New note", "Hide from sidebar", "Manage members…" (manager),
 *     "Delete project…" (manager).
 *   - Note:    "Delete note" (managers only).
 *
 * Top-level entries are projects (folders under notes/).  Loose root-level
 * files appear under a "(no project)" group.
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
  const [search, setSearch] = useState("");
  const [showHiddenPopover, setShowHiddenPopover] = useState(false);

  const collapsed = useSidebarPrefs((s) => s.collapsed);
  const hidden = useSidebarPrefs((s) => s.hidden);
  const toggleCollapse = useSidebarPrefs((s) => s.toggleCollapse);
  const collapseAll = useSidebarPrefs((s) => s.collapseAll);
  const expandAll = useSidebarPrefs((s) => s.expandAll);
  const hideProject = useSidebarPrefs((s) => s.hideProject);
  const unhideProject = useSidebarPrefs((s) => s.unhideProject);

  const applied = useMemo(
    () => applySidebarPrefs(tree, { collapsed, hidden }, search),
    [tree, collapsed, hidden, search],
  );

  // Dismiss the context menu / hidden popover on any outside click / Escape.
  useEffect(() => {
    if (!menu && !showHiddenPopover) return;
    const close = () => {
      setMenu(null);
      setShowHiddenPopover(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu, showHiddenPopover]);

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

  const allKeys = useMemo(() => tree.map(projectKey), [tree]);
  const hiddenCount = applied.hiddenProjects.length;

  return (
    <aside className="w-64 shrink-0 border-r bg-slate-50 overflow-y-auto p-3 text-sm relative">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs uppercase tracking-wide text-slate-500">Projects</h3>
        <div className="flex items-center gap-1">
          <button
            onClick={() => collapseAll(allKeys)}
            title="Collapse all projects"
            aria-label="Collapse all projects"
            className="text-slate-500 hover:text-slate-800 text-xs px-1"
          >
            ⊟
          </button>
          <button
            onClick={() => expandAll()}
            title="Expand all projects"
            aria-label="Expand all projects"
            className="text-slate-500 hover:text-slate-800 text-xs px-1"
          >
            ⊞
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setShowHiddenPopover((v) => !v);
            }}
            title={
              hiddenCount
                ? `${hiddenCount} hidden project${hiddenCount === 1 ? "" : "s"}`
                : "No hidden projects"
            }
            aria-label="Hidden projects"
            aria-expanded={showHiddenPopover}
            className={`text-xs px-1 ${
              hiddenCount ? "text-amber-600 hover:text-amber-800" : "text-slate-400"
            }`}
          >
            👁{hiddenCount ? ` ${hiddenCount}` : ""}
          </button>
          <button
            onClick={() => setCreating((c) => !c)}
            title="New project"
            aria-label="New project"
            className="text-sky-600 hover:text-sky-800 text-xs ml-1"
          >
            + new
          </button>
        </div>
      </div>

      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Filter projects / notes…"
        aria-label="Filter projects and notes"
        className="w-full rounded border px-2 py-1 text-xs mb-2"
      />

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

      {showHiddenPopover && (
        <HiddenProjectsPopover
          hiddenProjects={
            // Show every project the user has hidden, regardless of search
            // status, so they can always be unhidden from one place.
            tree.filter((n) => hidden[projectKey(n)])
          }
          onUnhide={(key) => unhideProject(key)}
          onClose={() => setShowHiddenPopover(false)}
        />
      )}

      <ul role="tree" className="space-y-3">
        {applied.visibleProjects.map((view) => {
          const node = view.node;
          const key = projectKey(node);
          const label = projectLabel(node);
          return (
            <li key={key} role="treeitem" aria-expanded={view.expanded}>
              <div
                className="flex items-center justify-between text-slate-700 font-semibold cursor-pointer select-none"
                onClick={(e) => {
                  // Don't toggle on right-click or when clicking inside the
                  // role/hidden badge (they're decorative).
                  if (e.button !== 0) return;
                  toggleCollapse(key);
                }}
                onContextMenu={(e) => {
                  if (!node.project) return;
                  e.preventDefault();
                  setMenu({
                    x: e.clientX,
                    y: e.clientY,
                    target: {
                      kind: "project",
                      project: node.project,
                      role: node.role,
                      key,
                    },
                  });
                }}
                title={node.project ? "Click to collapse · Right-click for actions" : "Click to collapse"}
              >
                <span className="flex items-center gap-1 min-w-0">
                  <span
                    aria-hidden="true"
                    className="inline-block w-3 text-slate-400 text-[10px]"
                  >
                    {view.expanded ? "▾" : "▸"}
                  </span>
                  <span className="truncate">{label}</span>
                  {!view.expanded && view.notes.length > 0 && (
                    <span className="text-[10px] text-slate-400 ml-1">
                      · {view.notes.length}
                    </span>
                  )}
                  {view.hiddenByPref && (
                    <span
                      className="text-[10px] uppercase rounded px-1 bg-amber-100 text-amber-700 ml-1"
                      title="Hidden — surfaced because the search matched"
                    >
                      hidden
                    </span>
                  )}
                </span>
                {node.project && (
                  <span
                    className={`text-[10px] uppercase rounded px-1.5 py-0.5 ml-1 shrink-0 ${
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

              {view.expanded && (
                <ul role="group" className="mt-1 ml-2 border-l border-slate-200">
                  {view.notes.length === 0 && (
                    <li className="pl-2 text-xs italic text-slate-400">
                      {applied.searchActive ? "no matches" : "empty"}
                    </li>
                  )}
                  {view.notes.map((n) => {
                    const active = n.path === selectedPath;
                    return (
                      <li key={n.path} role="treeitem">
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
              )}
            </li>
          );
        })}
        {tree.length === 0 && (
          <li className="text-xs italic text-slate-400">
            No projects. Click <em>+ new</em> to create one.
          </li>
        )}
        {tree.length > 0 && applied.visibleProjects.length === 0 && (
          <li className="text-xs italic text-slate-400">
            {applied.searchActive
              ? "No projects match your filter."
              : `All projects hidden. Click 👁 ${hiddenCount} to restore.`}
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
            onHide: (key) => {
              hideProject(key);
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
    onHide: (key: string) => void;
    onManageMembers: (project: string) => void;
    onDeleteProject: (project: string) => void;
    onDeleteNote: (id: number | null, path: string) => void;
  },
): MenuItem[] {
  if (target.kind === "project") {
    const items: MenuItem[] = [
      { label: "New note in project", onClick: () => cb.onNewNote(target.project) },
      { label: "Hide from sidebar", onClick: () => cb.onHide(target.key) },
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
      role="menu"
      className="fixed z-50 min-w-[10rem] rounded border bg-white shadow-lg py-1 text-xs"
      style={{ left: x, top: y }}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((it, i) => (
        <li key={i}>
          <button
            role="menuitem"
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

function HiddenProjectsPopover({
  hiddenProjects,
  onUnhide,
  onClose,
}: {
  hiddenProjects: TreeNode[];
  onUnhide: (key: string) => void;
  onClose: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-label="Hidden projects"
      className="absolute right-3 top-10 z-40 w-56 rounded border bg-white shadow-lg p-2 text-xs"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="font-semibold text-slate-700">Hidden projects</span>
        <button
          onClick={onClose}
          aria-label="Close hidden projects popover"
          className="text-slate-400 hover:text-slate-700"
        >
          ✕
        </button>
      </div>
      {hiddenProjects.length === 0 ? (
        <p className="text-slate-400 italic">No hidden projects.</p>
      ) : (
        <ul className="space-y-1 max-h-64 overflow-y-auto">
          {hiddenProjects.map((n) => {
            const key = projectKey(n);
            return (
              <li key={key} className="flex items-center justify-between gap-2">
                <span className="truncate text-slate-700">{projectLabel(n)}</span>
                <button
                  onClick={() => onUnhide(key)}
                  className="text-sky-600 hover:text-sky-800 shrink-0"
                  title="Unhide this project"
                >
                  unhide
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {hiddenProjects.length > 0 && (
        <div className="mt-2 pt-2 border-t flex justify-end">
          <button
            onClick={() => {
              for (const n of hiddenProjects) onUnhide(projectKey(n));
            }}
            className="text-sky-600 hover:text-sky-800 text-xs"
          >
            Unhide all
          </button>
        </div>
      )}
    </div>
  );
}
