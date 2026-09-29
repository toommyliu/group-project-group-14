import { expect, it } from 'vitest';
import {
  excludePage,
  mergeWithPrevious,
  movePage,
  pageRanges,
  restorePage,
  rotatePages,
  splitBefore,
  type PageGroup,
} from './scan-groups';

function groups(...numbers: number[][]): PageGroup[] {
  return numbers.map((pages, index) => ({
    id: `g${index}`,
    pages: pages.map((number) => ({ number, rotation: 0 })),
    version: null,
  }));
}

function numbers(result: PageGroup[]) {
  return result.map((group) => group.pages.map((page) => page.number));
}

it('splits and merges page groups', () => {
  const split = splitBefore(groups([1, 2, 3, 4]), 0, 2);
  expect(numbers(split)).toEqual([
    [1, 2],
    [3, 4],
  ]);
  expect(split[0].id).toBe('g0');
  expect(numbers(mergeWithPrevious(split, 1))).toEqual([[1, 2, 3, 4]]);
  expect(splitBefore(groups([1, 2]), 0, 0)).toEqual(groups([1, 2]));
});

it('moves pages between groups and drops emptied groups', () => {
  expect(numbers(movePage(groups([1], [2, 3]), 1, 1, 1))).toEqual([[2, 1, 3]]);
  expect(numbers(movePage(groups([1, 2], [3]), 2, 2, 0))).toEqual([
    [1],
    [3],
    [2],
  ]);
});

it('rotates, excludes, and restores pages in scan order', () => {
  const rotated = rotatePages(groups([1, 2]), new Set([2]));
  expect(rotated[0].pages[1].rotation).toBe(90);
  const excluded = excludePage(groups([1, 2, 3], [4]), 4);
  expect(numbers(excluded)).toEqual([[1, 2, 3]]);
  expect(numbers(restorePage(groups([1, 3], [5, 6]), 2))).toEqual([
    [1, 2, 3],
    [5, 6],
  ]);
  expect(numbers(restorePage(groups([3], [5]), 1))).toEqual([[1, 3], [5]]);
});

it('formats page ranges', () => {
  expect(pageRanges([1, 2, 3, 7, 9, 10])).toBe('1–3, 7, 9–10');
  expect(pageRanges([4])).toBe('4');
  expect(pageRanges([])).toBe('');
});
