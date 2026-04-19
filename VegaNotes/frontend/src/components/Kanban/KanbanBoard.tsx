import { useMemo } from "react";
import {
  DndContext, DragEndEvent, PointerSensor, useDroppable, useSensor, useSensors,
} from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Task } from "../../api/client";
import { TaskCard } from "../Card/TaskCard";
import { useUI } from "../../store/ui";

const COLUMNS = ["todo", "in-progress", "blocked", "done"];

function Column({ id, tasks }: { id: string; tasks: Task[] }) {
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
          {tasks.map((t) => <SortableCard key={t.id} task={t} />)}
        </div>
      </SortableContext>
    </div>
  );
}

function SortableCard({ task }: { task: Task }) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: task.id });
  const style = { transform: CSS.Transform.toString(transform), transition };
  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <TaskCard task={task} />
    </div>
  );
}

/**
 * On drop, call PATCH /api/tasks/{id} with the new status. The backend
 * rewrites the underlying .md file and re-indexes — guaranteed round-trip.
 */
export function KanbanBoard() {
  const { filters } = useUI();
  const qc = useQueryClient();
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));
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
    <DndContext sensors={sensors} onDragEnd={onDragEnd}>
      <div className="flex gap-3 p-4 overflow-x-auto">
        {COLUMNS.map((c) => <Column key={c} id={c} tasks={grouped[c]} />)}
      </div>
    </DndContext>
  );
}
