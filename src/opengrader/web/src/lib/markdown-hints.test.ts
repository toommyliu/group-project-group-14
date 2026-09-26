import { expect, it } from 'vitest';
import type { QAPreview } from '@/api/queries';
import { markdownLineHints } from './markdown-hints';

const preview = {
  warnings: [],
  notes: [
    { line: 4, kind: 'continuation', message: 'Joined to line 3.' },
    { line: 9, kind: 'ignored', message: 'Ignored: Expected.' },
  ],
  document: {
    title: 'Quiz',
    questions: [
      {
        id: 'q1',
        prompt: 'Explain',
        points: 1,
        source_line: 3,
        answers: ['x'],
      },
      {
        id: 'q2',
        prompt: 'Parts',
        points: 0,
        source_line: 6,
        subquestions: [
          { id: 'q2a', prompt: 'Part', points: 0, source_line: 7 },
        ],
      },
    ],
  },
} as unknown as QAPreview;

it('labels questions and parts with the numbering, points, and type the parser produced', () => {
  expect(markdownLineHints(preview)).toEqual([
    { line: 3, text: 'Question 1 · 1 point · Written answer', tone: 'info' },
    { line: 4, text: 'Joined to line 3.', tone: 'info' },
    { line: 6, text: 'Question 2 · 0 points · Question group', tone: 'info' },
    { line: 7, text: 'Part 2.1 · no points · Written answer', tone: 'warning' },
    { line: 9, text: 'Ignored: Expected.', tone: 'warning' },
  ]);
});
