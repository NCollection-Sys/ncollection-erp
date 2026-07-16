/*
 * Chart.js wrappers themed to the NCollection palette. Chart.js is the same
 * engine Odoo uses internally, so these transfer conceptually to the real
 * OWL dashboard. Colors are read from CSS custom properties so charts follow
 * the active (light/dark) theme.
 */
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Tooltip,
  Legend,
} from "chart.js";
import { Line, Bar } from "react-chartjs-2";
import { useEffect, useState } from "react";
import { useTheme } from "../../theme/useTheme";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Filler,
  Tooltip,
  Legend,
);

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Re-read theme colors whenever the theme flips. */
function useChartColors() {
  const { theme } = useTheme();
  const [colors, setColors] = useState(() => readColors());
  useEffect(() => {
    // next tick so the [data-theme] attribute + tokens have applied
    const id = requestAnimationFrame(() => setColors(readColors()));
    return () => cancelAnimationFrame(id);
  }, [theme]);
  return colors;
}

function readColors() {
  return {
    primary: cssVar("--nc-primary") || "#1f5f8f",
    secondary: cssVar("--nc-secondary") || "#2d7ab7",
    grid: cssVar("--nc-chart-grid") || "rgba(15,23,42,0.07)",
    text: cssVar("--nc-text-muted") || "#5a6b83",
  };
}

const currencyTooltip = {
  backgroundColor: "#0f172a",
  padding: 10,
  cornerRadius: 8,
  titleFont: { weight: "bold" as const },
  displayColors: false,
  callbacks: {
    label: (i: { raw: unknown }) => `AED ${Number(i.raw).toLocaleString()}`,
  },
};

export function RevenueLineChart({
  labels,
  data,
}: {
  labels: string[];
  data: number[];
}) {
  const c = useChartColors();
  return (
    <div style={{ height: 260 }}>
      <Line
        data={{
          labels,
          datasets: [
            {
              data,
              borderColor: c.primary,
              backgroundColor: (ctx) => {
                const { ctx: canvas, chartArea } = ctx.chart;
                if (!chartArea) return "rgba(31,95,143,0.12)";
                const g = canvas.createLinearGradient(
                  0,
                  chartArea.top,
                  0,
                  chartArea.bottom,
                );
                g.addColorStop(0, "rgba(45,122,183,0.28)");
                g.addColorStop(1, "rgba(45,122,183,0.01)");
                return g;
              },
              fill: true,
              tension: 0.38,
              borderWidth: 2.5,
              pointRadius: 0,
              pointHoverRadius: 5,
              pointHoverBackgroundColor: c.primary,
              pointHoverBorderColor: "#fff",
              pointHoverBorderWidth: 2,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false }, tooltip: currencyTooltip },
          scales: {
            x: {
              grid: { display: false },
              ticks: { color: c.text, font: { size: 11 } },
              border: { display: false },
            },
            y: {
              grid: { color: c.grid },
              ticks: { color: c.text, font: { size: 11 } },
              border: { display: false },
            },
          },
        }}
      />
    </div>
  );
}

export function CustomersBarChart({
  labels,
  data,
}: {
  labels: string[];
  data: number[];
}) {
  const c = useChartColors();
  return (
    <div style={{ height: 260 }}>
      <Bar
        data={{
          labels,
          datasets: [
            {
              data,
              backgroundColor: c.secondary,
              hoverBackgroundColor: c.primary,
              borderRadius: 6,
              barPercentage: 0.62,
              categoryPercentage: 0.7,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          indexAxis: "y",
          plugins: { legend: { display: false }, tooltip: currencyTooltip },
          scales: {
            x: {
              grid: { color: c.grid },
              ticks: {
                color: c.text,
                font: { size: 11 },
                callback: (v) => `${Number(v) / 1000}k`,
              },
              border: { display: false },
            },
            y: {
              grid: { display: false },
              ticks: { color: c.text, font: { size: 11 } },
              border: { display: false },
            },
          },
        }}
      />
    </div>
  );
}
