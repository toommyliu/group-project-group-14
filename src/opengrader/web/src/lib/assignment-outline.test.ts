import { expect, it } from 'vitest';
import { versionHeadingLineNumber } from './assignment-outline';

const source = [
  '---',
  'title: Quiz',
  '---',
  '## Version A',
  '- (1 point) First',
  '## B',
  '- (1 point) Second',
].join('\n');

it('finds the nearest heading for a version above its first question', () => {
  expect(versionHeadingLineNumber(source, 'A', 5)).toBe(4);
  expect(versionHeadingLineNumber(source, 'B', 7)).toBe(6);
});

it('ignores headings at or below the first question', () => {
  expect(versionHeadingLineNumber(source, 'B', 6)).toBeNull();
  expect(versionHeadingLineNumber(source, 'C', 7)).toBeNull();
});
