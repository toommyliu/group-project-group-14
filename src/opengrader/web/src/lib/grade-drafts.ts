import type { Answer, GradeEdit } from '@/api/queries';

export function draftFor(answer: Answer) {
  return {
    labels: [...answer.rubric_labels],
    analysis: answer.analysis,
    manual: answer.score_mode === 'manual',
    score: answer.points_awarded?.toString() ?? '',
  };
}

export type Draft = ReturnType<typeof draftFor>;

export function sameDraft(a: Draft, b: Draft) {
  return (
    a.analysis === b.analysis &&
    a.manual === b.manual &&
    (a.manual ? a.score : '') === (b.manual ? b.score : '') &&
    JSON.stringify(a.labels) === JSON.stringify(b.labels)
  );
}

export type Recovery = {
  base: Draft;
  revision: string;
  context: string;
  draft: Draft;
  pending: GradeEdit | null;
};

/** Acknowledging an older request must not erase newer typing. */
export function acknowledgeDraft(
  current: Draft,
  submitted: GradeEdit,
  saved: Answer
): Draft {
  const sent: Draft = {
    labels: submitted.rubric_labels,
    analysis: submitted.analysis,
    manual: submitted.score_mode === 'manual',
    score: submitted.points_awarded?.toString() ?? '',
  };
  return sameDraft(current, sent)
    ? { ...draftFor(saved), manual: sent.manual }
    : current;
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isDraft(value: unknown): value is Draft {
  return (
    object(value) &&
    Array.isArray(value.labels) &&
    value.labels.every((label: unknown) => typeof label === 'string') &&
    typeof value.analysis === 'string' &&
    typeof value.manual === 'boolean' &&
    typeof value.score === 'string'
  );
}

function isEdit(value: unknown): value is GradeEdit {
  return (
    object(value) &&
    typeof value.request_id === 'string' &&
    typeof value.expected_revision === 'string' &&
    typeof value.expected_context_revision === 'string' &&
    Array.isArray(value.rubric_labels) &&
    value.rubric_labels.every((label: unknown) => typeof label === 'string') &&
    typeof value.analysis === 'string' &&
    (value.score_mode === 'manual' || value.score_mode === 'rubric') &&
    (value.points_awarded === null || typeof value.points_awarded === 'number')
  );
}

export function readRecovery(raw: string): Recovery {
  const value: unknown = JSON.parse(raw);
  if (
    !object(value) ||
    !isDraft(value.base) ||
    !isDraft(value.draft) ||
    typeof value.revision !== 'string' ||
    typeof value.context !== 'string' ||
    !(value.pending === null || isEdit(value.pending))
  ) {
    throw new Error(
      'A recovered draft could not be read. It has been kept in browser storage.'
    );
  }
  return {
    base: value.base,
    draft: value.draft,
    revision: value.revision,
    context: value.context,
    pending: value.pending,
  };
}

/** Each editor owns its record. Recovering another tab's draft never deletes it. */
export class GradeDrafts {
  readonly key: string;
  readonly prefix: string;
  private readonly storage: Storage;

  constructor(
    workspace: string,
    assignment: string,
    submission: number,
    question: string,
    storage: Storage = localStorage
  ) {
    this.storage = storage;
    this.prefix = `opengrader-draft:${JSON.stringify([workspace, assignment, submission, question])}:`;
    this.key = this.prefix + crypto.randomUUID();
  }

  candidates() {
    const records: { key: string; value: Recovery }[] = [];
    for (let i = 0; i < this.storage.length; i++) {
      const key = this.storage.key(i);
      if (key?.startsWith(this.prefix) && key !== this.key) {
        const raw = this.storage.getItem(key);
        if (raw) records.push({ key, value: readRecovery(raw) });
      }
    }
    return records;
  }

  save(value: Recovery) {
    try {
      this.storage.setItem(this.key, JSON.stringify(value));
    } catch {
      throw new Error(
        'Browser storage is full or unavailable. Keep this page open and free storage before retrying.'
      );
    }
  }

  clear() {
    this.storage.removeItem(this.key);
  }
  discard(keys: string[]) {
    for (const key of keys)
      if (key.startsWith(this.prefix)) this.storage.removeItem(key);
  }
}
