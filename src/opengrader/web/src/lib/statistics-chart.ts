import { barY, defineChart, dot, ruleY } from '@tanstack/charts';
import { scaleBand } from '@tanstack/charts/scales/band';
import { scaleLinear } from '@tanstack/charts/scales/linear';
import { tooltip } from '@tanstack/charts/tooltip';

export type StatisticsBar = {
  label: string;
  value: number | null;
  detail: string;
};

export type StatisticsReference = { value: number; detail: string };

// Hovering within this many pixels of the reference line shows its detail.
const REFERENCE_HOVER_DISTANCE = 6;

function axisNumber(value: number) {
  return Math.abs(value) >= 1e6 ? value.toExponential(1) : String(value);
}

export function statisticsChartDefinition(
  rows: StatisticsBar[],
  unit: 'percent' | 'submissions',
  histogram = false,
  reference: StatisticsReference | null = null
) {
  const maxThickness = histogram ? undefined : 96;
  const maximum = rows.reduce(
    (largest, row) => Math.max(largest, row.value ?? 0),
    Math.max(unit === 'percent' ? 100 : 1, reference?.value ?? 0)
  );
  // Tooltips need a chart point, so invisible anchors sit on the line in
  // every column and the one nearest the pointer carries the detail.
  const anchors = reference
    ? rows.map((row) => ({ ...reference, label: row.label, reference: true }))
    : [];
  return defineChart({
    marks: [
      barY(rows, {
        x: 'label',
        y: 'value',
        fill: 'var(--chart-1)',
        maxThickness,
        inset: 0,
        stroke: histogram ? 'var(--background)' : undefined,
        strokeWidth: histogram ? 1 : undefined,
      }),
      // Drawn after the bars so the line stays visible across them.
      ruleY(reference ? [reference.value] : [], {
        stroke: 'var(--foreground)',
        strokeOpacity: 0.7,
        strokeWidth: 1.5,
        strokeDasharray: '4 4',
      }),
      dot(anchors, {
        x: 'label',
        y: 'value',
        r: 0,
        fillOpacity: 0,
        strokeOpacity: 0,
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
            format: (value) =>
              unit === 'percent' ? `${axisNumber(value)}%` : axisNumber(value),
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
      const isReference = (point: (typeof points)[number]) =>
        'reference' in point.datum;
      return {
        // The line is drawn over the bars, so it wins where they overlap.
        findNearest: (x, y) => {
          let nearest = null;
          for (const point of points) {
            if (
              isReference(point) &&
              Math.abs(point.y - y) <= REFERENCE_HOVER_DISTANCE &&
              (!nearest || Math.abs(point.x - x) < Math.abs(nearest.x - x))
            )
              nearest = point;
          }
          return (
            nearest ??
            points.find(
              (point) =>
                !isReference(point) &&
                point.y !== baseline &&
                Math.abs(point.x - x) <= halfWidth &&
                y >= Math.min(point.y, baseline) &&
                y <= Math.max(point.y, baseline)
            ) ??
            null
          );
        },
      };
    },
    tooltip: {
      use: tooltip,
      sticky: false,
      format: (point) => point.datum.detail,
    },
  });
}
