import { notFound } from '@tanstack/react-router';

export type MissingResource = { kind: 'assignment' | 'submission' };

export function assignmentNotFound() {
  return notFound({ data: { kind: 'assignment' } satisfies MissingResource });
}

export function submissionNotFound() {
  return notFound({ data: { kind: 'submission' } satisfies MissingResource });
}

export function isMissingResource(data: unknown): data is MissingResource {
  return (
    typeof data === 'object' &&
    data !== null &&
    'kind' in data &&
    (data.kind === 'assignment' || data.kind === 'submission')
  );
}
