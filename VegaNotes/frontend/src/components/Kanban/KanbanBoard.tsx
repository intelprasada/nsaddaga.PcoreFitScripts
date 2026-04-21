import { useMemo, useState } from "react";
import {
  DndContext, DragEndEvent, PointerSensor, useDroppable, useSensor, useSensors,
} from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";
import { TaskCard } from "../Card/TaskCard";
import { TaskEditPopover } from "../Tasks/TaskEditPopover";
import { useUI } from "../../store/ui";

const COLUMNS = ["todo", "in-progress", "blocked", "done"];

function Column({ id, tasks, onOpen }: { id: string; tasks: Task[]; onOpen: (t: Task) => void }) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <div ref={setNodeRef}
      className={`flex-1 min-w-[260px] rounded-lg p-3 transition ${
        isOver ? "bg-sky-100" : "bg-slate-100"
      }`}>
      <div className="text-xs uppercase tracking-wide text-slate-500 mb-2 flex justify-between">
        <span>{id}</span><span>{tasks.length}</span>
      </div>
      <SortableContext items={tasks.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        <div className="space-y-2">
          {tasks.map((t) => <SortableCard key={t.id} task={t} onOpen={onOpen} />)}
        </div>
      </SortableContext>
    </div>
  );
}

function SortableCard({ task, onOpen }: { task: Task; onOpen: (t: Task) => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: task.id });
  const style = { transform: CSS.Transform.toString(transform), transition };
  // Drag listeners are bound to a small handle so the rest of the card
  // remains clickable for opening the edit popover.
  return (
    <div ref={setNodeRef} style={style} className="relative">
      <div
        {...attributes}
        {...listeners}
        title="drag to move between columns"
        className="absolute top-1 right-1 z-10 cursor-grab text-slate-300 hover:text-slate-600 text-xs select-none px-1"
      >⋮⋮</div>
      <div className={isDragging ? "opacity-60" : ""}>
        <TaskCard task={task} onOpen={onOpen} />
      </div>
    </div>
  );
}

/**
 * On drop, call PATCH /api/tasks/{id} with the new status. The backend
 * rewrites the underlying .md file and re-indexes — guaranteed round-trip.
 *
 * Click anywhere on a card (outside the drag handle) to open the inline
 * edit popover for the task.
 */
export function KanbanBoard() {
  const { filters } = useUI();
  const qc = useQueryClient();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));
  const [editing, setEditing] = useState<Task | null>(null);
  const { data } = useQuery({
    queryKey: ["tasks", filters, "kanban"],
    queryFn: () =>
      api.tasks({
        ...filters,
        hide_done: false,
        top_level_only: true,
        include_children: true,
      }),
  });
  const tasks = data?.tasks ?? [];
  const grouped = useMemo(() => {
    const g: Record<string, Task[]> = {};
    for (const c of COLUMNS) g[c] = [];
    for (const t of tasks) (g[t.status] ?? g.todo).push(t);
    return g;
  }, [tasks]);

  const move = useMutation({
    mutationFn: ({ task, newStatus }: { task: Task; newStatus: string }) =>
      api.updateTask(task.id, { status: newStatus }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["agenda"] });
      qc.invalidateQueries({ queryKey: ["note"] });
    },
  });

  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over) return;
    const task = tasks.find((t) => t.id === Number(e.active.id));
    if (!task) return;
    const newStatus = String(e.over.id);
    if (!COLUMNS.includes(newStatus) || newStatus === task.status) return;
    move.mutate({ task, newStatus });
  };

  return (
    <>
      <DndContext sensors={sensors} onDragEnd={onDragEnd}>
        <div className="flex gap-3 p-4 overflow-x-auto">
          {COLUMNS.map((c) => <Column key={c} id={c} tasks={grouped[c]} onOpen={setEditing} />)}
        </div>
      </DndContext>
      {editing && <TaskEditPopover task={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
