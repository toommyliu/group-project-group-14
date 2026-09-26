import type { components } from '@/api/schema';
import type { OutlineGroup } from './assignment-outline';

export type ValidationLocation = components['schemas']['QAErrorLocation'];

export function parseValidationLocation(
  value: unknown
): ValidationLocation | null {
  if (!value || typeof value !== 'object') return null;
  const path = (value: unknown): value is number[] =>
    Array.isArray(value) &&
    value.every((index) => Number.isSafeInteger(index) && index >= 0);
  if (
    !('line' in value) ||
    !(
      value.line === null ||
      (typeof value.line === 'number' &&
        Number.isSafeInteger(value.line) &&
        value.line > 0)
    ) ||
    !('version' in value) ||
    typeof value.version !== 'string' ||
    !('question_path' in value) ||
    !path(value.question_path) ||
    !('rubric_path' in value) ||
    !(value.rubric_path === null || path(value.rubric_path))
  )
    return null;
  return {
    line: value.line,
    version: value.version,
    question_path: value.question_path,
    rubric_path: value.rubric_path,
  };
}

export function validationQuestionTarget(
  location: ValidationLocation,
  groups: OutlineGroup[]
) {
  const group = groups.find(
    (group) => group.label.trim() === (location.version ?? '')
  );
  if (!group || !location.question_path?.length) return null;
  let questions = group.questions;
  let question;
  for (const index of location.question_path) {
    question = questions[index];
    if (!question) return null;
    questions = question.subquestions ?? [];
  }
  if (question?.source_line == null) return null;
  return {
    version: group.key,
    line: question.source_line,
    rubricPath: location.rubric_path,
  };
}

/** Prefer explicit parser lines to the containing question's fallback location. */
export function validationSourceLine(
  message: string | undefined,
  location?: ValidationLocation | null
) {
  const match = message?.match(/\bline\s+(\d+)\b/i);
  if (!match) return location?.line ?? null;
  const line = Number.parseInt(match[1], 10);
  return Number.isSafeInteger(line) && line > 0 ? line : null;
}

export function withoutSourceLines(message: string) {
  const text = message
    .replace(/\s*\(line \d+\)/gi, '')
    .replace(/\s+(?:at|on) line \d+/gi, '');
  return text.charAt(0).toUpperCase() + text.slice(1);
}
