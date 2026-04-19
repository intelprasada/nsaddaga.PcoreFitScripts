import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

export function GraphView() {
  const { data } = useQuery({ queryKey: ["tasks", "graph"], queryFn: () => api.tasks({}) });
  const tasks = data?.tasks ?? [];
  const nodes: Node[] = tasks.map((t, i) => ({
    id: String(t.id),
    position: { x: (i % 6) * 180, y: Math.floor(i / 6) * 110 },
    data: { label: t.title },
    style: { borderRadius: 8, padding: 8, background: "#fff", border: "1px solid #e2e8f0" },
  }));
  // Edges built lazily from /api/cards/{id}/links would be fetched per node;
  // for the scaffold we render nodes only.
  const edges: Edge[] = [];
  return (
    <div className="h-[80vh]">
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background /><Controls />
      </ReactFlow>
    </div>
  );
}
