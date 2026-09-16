import { expect, it } from 'vitest';
import type { Question, Submission } from '@/api/queries';
import { nextUngraded, rubricScore, selectRubric } from './grading';

const question: Question = {
  id: 'q1',
  prompt: '',
  possible_points: 10,
  question_type: 'free_response',
  expected_answers: [],
  rubric: [
    {
      label: 'Correct',
      points_delta: 0,
      kind: 'correct',
      selection_mode: 'exclusive',
      default_comment: null,
    },
    {
      label: 'Method',
      points_delta: -3,
      kind: 'partial',
      selection_mode: 'checklist',
      default_comment: null,
    },
    {
      label: 'Units',
      points_delta: -2,
      kind: 'partial',
      selection_mode: 'checklist',
      default_comment: null,
    },
    {
      label: 'Bonus',
      points_delta: 2,
      kind: 'bonus',
      selection_mode: 'exclusive',
      default_comment: null,
    },
  ],
};

it('replaces exclusive items, accumulates checklist items, and preserves bonus credit like the TUI', () => {
  let labels = selectRubric(question, ['Correct', 'Bonus'], 1);
  expect(labels).toEqual(['Bonus', 'Method']);
  labels = selectRubric(question, labels, 2);
  expect(rubricScore(question, labels)).toBe(7);
  labels = selectRubric(question, labels, 0);
  expect(labels).toEqual(['Bonus', 'Correct']);
  expect(rubricScore(question, labels)).toBe(12);
  expect(selectRubric(question, labels, 0)).toEqual(labels);
  expect(selectRubric(question, labels, 3)).toEqual(['Correct']);
});

it('distinguishes ungraded from zero and adds bonuses after clamping', () => {
  expect(rubricScore(question, [])).toBeNull();
  const positive: Question = {
    ...question,
    rubric: question.rubric.map((option) => ({
      ...option,
      points_delta: Math.abs(option.points_delta),
    })),
  };
  expect(rubricScore(positive, ['Correct'])).toBe(0);
  expect(rubricScore(positive, ['Method', 'Units', 'Bonus'])).toBe(7);
});

it('wraps the ungraded queue, skipping zero scores, missing responses, and the current submission', () => {
  const submissions: Submission[] = [
    { id: 1, version: null, question_scores: { q1: null } },
    { id: 2, version: 'B', question_scores: {} },
    { id: 3, version: null, question_scores: { q1: 0 } },
    { id: 4, version: null, question_scores: { q1: null } },
  ];
  expect(nextUngraded(submissions, 1, 'q1')?.id).toBe(4);
  expect(nextUngraded(submissions, 4, 'q1')?.id).toBe(1);
  expect(nextUngraded(submissions.slice(0, 3), 1, 'q1')).toBeUndefined();
});
