import { describe, expect, it } from 'vitest';
import {
  GradeDrafts,
  acknowledgeDraft,
  draftFor,
  readRecovery,
  sameDraft,
  type Recovery,
} from './grade-drafts';
import type { Answer } from '@/api/queries';

function storage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value);
    },
    removeItem: (key) => {
      values.delete(key);
    },
    clear: () => values.clear(),
  };
}

const base = { labels: [], manual: true, score: '2', analysis: 'Before' };
const value: Recovery = {
  base,
  revision: '3',
  context: 'rubric-1',
  draft: { ...base, analysis: 'Typed during the request' },
  pending: {
    request_id: '46b94b12-4e9e-4836-8e5d-2e60f9b889c2',
    expected_revision: '3',
    expected_context_revision: 'rubric-1',
    rubric_labels: [],
    score_mode: 'manual',
    points_awarded: 2,
    analysis: 'Sent before disconnect',
  },
};

const answer: Answer = {
  workspace_id: 'workspace',
  context_revision: 'rubric-1',
  revision: '4',
  submission_id: 1,
  version: null,
  question: {
    id: 'q1',
    prompt: '',
    possible_points: 5,
    question_type: 'free_response',
    expected_answers: [],
    rubric: [],
  },
  response_text: '',
  response_image_url: null,
  response_image_width: null,
  response_image_height: null,
  score_mode: 'manual',
  points_awarded: 2,
  rubric_labels: [],
  analysis: 'Sent before disconnect',
};

describe('grade recovery', () => {
  it.each(['1.50', '01.5', '1.5e0'])(
    'acknowledges formatted score %s without another save',
    (score) => {
      const saved = { ...answer, points_awarded: 1.5 };
      const submitted = { ...value.pending!, points_awarded: 1.5 };
      const current = { ...draftFor(saved), score };
      expect(
        sameDraft(acknowledgeDraft(current, submitted, saved), draftFor(saved))
      ).toBe(true);
    }
  );

  it('retains a newer score or feedback while acknowledging a formatted score', () => {
    const saved = { ...answer, points_awarded: 1.5 };
    const submitted = { ...value.pending!, points_awarded: 1.5 };
    for (const current of [
      { ...draftFor(saved), score: '2.50' },
      { ...draftFor(saved), score: '1.50', analysis: 'New feedback' },
      { ...draftFor(saved), score: '' },
    ])
      expect(acknowledgeDraft(current, submitted, saved)).toBe(current);
  });

  it('retains the exact unacknowledged request and newer typing across a page restart', () => {
    const disk = storage();
    const first = new GradeDrafts('workspace', 'assignment', 1, 'q1', disk);
    first.save(value);
    const restarted = new GradeDrafts('workspace', 'assignment', 1, 'q1', disk);
    const recovered = restarted.candidates()[0]!.value;
    expect(recovered.pending).toEqual(value.pending);
    expect(
      acknowledgeDraft(recovered.draft, recovered.pending!, answer)
    ).toEqual(value.draft);
    expect(
      acknowledgeDraft(
        { ...base, analysis: answer.analysis },
        recovered.pending!,
        answer
      )
    ).toEqual({ ...base, analysis: answer.analysis });
  });

  it('never overwrites or clears a different editor or workspace', () => {
    const disk = storage();
    const first = new GradeDrafts('workspace', 'assignment', 1, 'q1', disk);
    const second = new GradeDrafts('workspace', 'assignment', 1, 'q1', disk);
    first.save(value);
    second.save({ ...value, draft: { ...base, analysis: 'Another grader' } });
    second.clear();
    expect(readRecovery(disk.getItem(first.key)!)).toEqual(value);
    expect(
      new GradeDrafts(
        'other-workspace',
        'assignment',
        1,
        'q1',
        disk
      ).candidates()
    ).toEqual([]);
  });

  it('makes a storage failure visible instead of pretending a draft is recoverable', () => {
    const disk = storage();
    disk.setItem = () => {
      throw new DOMException('Quota exceeded');
    };
    expect(() => new GradeDrafts('w', 'a', 1, 'q1', disk).save(value)).toThrow(
      'Browser storage is full'
    );
  });
});
