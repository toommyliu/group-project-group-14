import { barY, defineChart } from '@tanstack/charts';
import { scaleBand } from '@tanstack/charts/scales/band';
import { scaleLinear } from '@tanstack/charts/scales/linear';
import { tooltip } from '@tanstack/charts/tooltip';

export type StatisticsBar = {
  label: string;
  value: number | null;
  detail: string;
};

export function statisticsChartDefinition(
  rows: StatisticsBar[],
  unit: 'percent' | 'submissions',
  histogram = false
) {
  const maxThickness = histogram ? undefined : 96;
  const maximum = rows.reduce(
    (largest, row) => Math.max(largest, row.value ?? 0),
    unit === 'percent' ? 100 : 1
  );
  return defineChart({
    marks: [
      barY(rows, {
        x: 'label',
        y: 'value',
        fill: 'var(--primary)',
        maxThickness,
        inset: 0,
        stroke: histogram ? 'var(--background)' : undefined,
        strokeWidth: histogram ? 1 : undefined,
      }),
    ],
    scales: {
      x: {
        scale: scaleBand()
          .domain(rows.map((row) => row.label))
          .padding(histogram ? 0 : 0.25),
        axis: { label: unit === 'percent' ? 'Question' : 'Score' },
      },
      y: {
        scale: scaleLinear().domain([0, maximum]),
        grid: true,
        axis: {
          label: unit === 'percent' ? 'Mean score' : 'Submissions',
          ticks: {
            count: Math.min(5, maximum),
            format: (value) => (unit === 'percent' ? `${value}%` : `${value}`),
          },
        },
      },
    },
    theme: {
      foreground: 'var(--foreground)',
      muted: 'var(--muted-foreground)',
      grid: 'var(--border)',
      background: 'var(--background)',
    },
    keyboard: false,
    spatialIndex: (points, { scene }) => {
      const baseline = scene.scales.y.map(0);
      const halfWidth =
        Math.min(scene.scales.x.bandwidth, maxThickness ?? Infinity) / 2;
      return {
        findNearest: (x, y) =>
          points.find(
            (point) =>
              point.y !== baseline &&
              Math.abs(point.x - x) <= halfWidth &&
              y >= Math.min(point.y, baseline) &&
              y <= Math.max(point.y, baseline)
          ) ?? null,
      };
    },
    tooltip: {
      use: tooltip,
      sticky: false,
      format: (point) => point.datum.detail,
    },
  });
}
