import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

export function CalendarView() {
  const { data } = useQuery({ queryKey: ["tasks", "all"], queryFn: () => api.tasks({}) });
  const events = (data?.tasks ?? [])
    .filter((t) => t.eta)
    .map((t) => ({ id: String(t.id), title: t.title, date: t.eta!, color: t.status === "done" ? "#9ca3af" : "#0284c7" }));
  return (
    <div className="p-4">
      <FullCalendar plugins={[dayGridPlugin]} initialView="dayGridMonth" events={events} height="80vh" />
    </div>
  );
}
