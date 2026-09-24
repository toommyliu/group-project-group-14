import { renderToStaticMarkup } from 'react-dom/server';
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterContextProvider,
} from '@tanstack/react-router';
import { expect, it } from 'vitest';
import type {
  AssignmentVersionStatistics,
  ScoreStatistics,
} from '@/api/queries';
import { AssignmentStatistics } from './assignment-statistics';

const graded: ScoreStatistics = {
  question_id: 'total',
  possible_points: 10,
  total_submissions: 3,
  graded_count: 1,
  ungraded_count: 2,
  average: 8,
  median: 8,
  std_dev: 0,
  minimum: 8,
  maximum: 8,
  q1: 8,
  q3: 8,
  average_percent: 80,
  full_credit_count: 0,
  zero_score_count: 0,
  buckets: [{ label: '80-100%', count: 1 }],
};
const ungraded: ScoreStatistics = {
  ...graded,
  graded_count: 0,
  ungraded_count: 3,
  average: null,
  median: null,
  std_dev: null,
  minimum: null,
  maximum: null,
  q1: null,
  q3: null,
  average_percent: null,
  buckets: [],
};

function render(report: AssignmentVersionStatistics) {
  const router = createRouter({
    routeTree: createRootRoute(),
    history: createMemoryHistory(),
  });
  return renderToStaticMarkup(
    <RouterContextProvider router={router}>
      <AssignmentStatistics assignmentId="exam" report={report} />
    </RouterContextProvider>
  );
}

it('renders summaries, real charts, partial progress, and links to the matching version submission', () => {
  const html = render({
    version: 'B',
    scores: graded,
    partial_count: 1,
    ungraded_count: 1,
    questions: [
      {
        prompt: 'Explain your answer',
        parent_id: null,
        parent_prompt: null,
        submission_id: 42,
        scores: { ...graded, question_id: 'q1' },
      },
    ],
  });
  expect(html).toContain('1 of 3 submissions fully graded');
  expect(html).toContain('1 partially graded');
  expect(html).toContain('Standard deviation');
  expect(html).toContain('80%');
  expect(html).toContain('Assignment total score distribution');
  expect(html).toContain('<svg');
  expect(html).toContain(
    '/submissions?assignment=exam&amp;submission=42&amp;question=q1'
  );
  expect(html).toContain('Question statistics');
  expect(html).toContain('10 points possible');
  const summary = html.split('</dl>')[0];
  expect(summary.match(/<dd\b/g)).toHaveLength(5);
  expect(summary).not.toContain('8 pts');
  expect(html).toContain('data-slot="select-trigger"');
});

it('shows ungraded states without manufacturing zero scores and offers the next action', () => {
  const html = render({
    version: null,
    scores: ungraded,
    partial_count: 0,
    ungraded_count: 3,
    questions: [
      {
        prompt: 'Not yet graded',
        parent_id: null,
        parent_prompt: null,
        submission_id: 1,
        scores: { ...ungraded, question_id: 'q1' },
      },
    ],
  });
  expect(html).toContain('No fully graded submissions yet.');
  expect(html).toContain('Continue grading');
  expect(html).toContain('Not graded');
  expect(html).toContain('Finish grading a submission');
  expect(html).not.toContain('Assignment total score distribution');
  const empty = render({
    version: 'A',
    scores: { ...ungraded, total_submissions: 0, ungraded_count: 0 },
    partial_count: 0,
    ungraded_count: 0,
    questions: [],
  });
  expect(empty).toContain('No submissions for this version yet.');
  expect(empty).toContain('/scans?assignment=exam');
});
