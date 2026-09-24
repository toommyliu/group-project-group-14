import { createChartScene } from '@tanstack/charts';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { StatisticsChart } from './statistics-chart';
import { statisticsChartDefinition } from '@/lib/statistics-chart';

it('keeps zero grades, omits ungraded bars, and includes extra credit in the scale', () => {
  const scene = createChartScene(
    statisticsChartDefinition(
      [
        { label: '1a', value: 0, detail: 'Question 1a: 0% mean' },
        { label: '1b', value: null, detail: 'Question 1b: Not graded' },
        { label: '2', value: 120, detail: 'Question 2: 120% mean' },
      ],
      'percent'
    ),
    { width: 640, height: 280 }
  );
  expect(scene.points.map((point) => [point.xValue, point.yValue])).toEqual([
    ['1a', 0],
    ['2', 120],
  ]);
  expect(scene.scales.x.domain).toEqual(['1a', '1b', '2']);
  expect(scene.scales.y.domain).toEqual([0, 120]);
});

it('renders the actual TanStack SVG with accessible names, score ranges, and integer counts', () => {
  const rows = [
    { label: '0-20%', value: 1, detail: '0-20%: 1 submission' },
    { label: '20-40%', value: 0, detail: '20-40%: 0 submissions' },
    { label: '100%+', value: 3, detail: '100%+: 3 submissions' },
  ];
  const scene = createChartScene(
    statisticsChartDefinition(rows, 'submissions', true),
    {
      width: 640,
      height: 280,
    }
  );
  expect(scene.points.map((point) => point.yValue)).toEqual([1, 0, 3]);
  expect(scene.scales.y.ticks.map((tick) => tick.value)).toEqual([0, 1, 2, 3]);
  expect(
    scene.scales.x.map('20-40%') - scene.scales.x.map('0-20%')
  ).toBeCloseTo(scene.scales.x.bandwidth);
  const html = renderToStaticMarkup(
    <StatisticsChart
      rows={rows}
      unit="submissions"
      label="Assignment score distribution"
      histogram
    />
  );
  expect(html).toContain('<svg');
  expect(html).toContain('Assignment score distribution');
  expect(html).toContain('100%+');
  expect(html).toMatch(/<svg[^>]*tabindex="-1"/);
  expect(html).not.toMatch(/<svg[^>]*tabindex="0"/);
  expect(html).toContain('Image actions for assignment score distribution');
  expect(html).toContain('var(--primary)');
});

it('shows hover details only inside a painted bar, excluding gaps, zero bars, and empty column space', () => {
  const definition = statisticsChartDefinition(
    [
      { label: '1', value: 40, detail: '40%' },
      { label: '2', value: 0, detail: '0%' },
      { label: '3', value: null, detail: 'Not graded' },
    ],
    'percent'
  );
  const scene = createChartScene(definition, { width: 960, height: 280 });
  const createIndex = definition.spatialIndex;
  if (!createIndex) throw new Error('The chart must use bar hit testing.');
  const index = createIndex(scene.points, { scene });
  const bar = scene.points[0];
  const baseline = scene.scales.y.map(0);
  expect(index.findNearest(bar.x, (bar.y + baseline) / 2)?.xValue).toBe('1');
  expect(index.findNearest(bar.x, bar.y - 1)).toBeNull();
  expect(index.findNearest(bar.x + 49, (bar.y + baseline) / 2)).toBeNull();
  expect(index.findNearest(bar.x, baseline + 1)).toBeNull();
  expect(index.findNearest(scene.points[1].x, baseline)).toBeNull();
});
