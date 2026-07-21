/**
 * useChartJs.ts — registers Chart.js 4.x tree-shaken components once at
 * module load and exports `Chart` plus a `destroyChart` utility.
 */
import {
  Chart,
  CategoryScale,
  LinearScale,
  BarElement,
  ArcElement,
  DoughnutController,
  BarController,
  Legend,
  Tooltip,
  Title,
} from "chart.js";

Chart.register(
  CategoryScale,
  LinearScale,
  BarElement,
  ArcElement,
  DoughnutController,
  BarController,
  Legend,
  Tooltip,
  Title,
);

// Chart.js 4.x defaults for the dark dashboard theme
Chart.defaults.color = "#d7deef";
Chart.defaults.borderColor = "rgba(162,179,217,0.16)";
Chart.defaults.font.family =
  '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
Chart.defaults.font.size = 13;
Chart.defaults.plugins.legend.labels.color = "#e6ebff";
Chart.defaults.plugins.legend.labels.boxWidth = 14;
Chart.defaults.plugins.legend.labels.boxHeight = 14;
Chart.defaults.plugins.legend.labels.padding = 16;
Chart.defaults.plugins.tooltip.backgroundColor = "rgba(10,15,31,0.96)";
Chart.defaults.plugins.tooltip.borderColor = "#32467a";
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.padding = 12;
Chart.defaults.plugins.tooltip.cornerRadius = 8;

export { Chart };

export const DASH_COLORS = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
  "#f97316",
  "#ec4899",
  "#84cc16",
  "#14b8a6",
  "#eab308",
  "#6366f1",
];

export const PROJECT_COLORS: Record<string, string> = {
  GFC: "#22d3ee",
  JNC: "#f59e0b",
};

/** Destroy a Chart instance held in a ref, then null the ref. */
export function destroyChart(ref: { current: Chart | null }) {
  if (ref.current) {
    ref.current.destroy();
    ref.current = null;
  }
}
