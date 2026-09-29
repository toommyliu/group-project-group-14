import type { ScanBatch, ScanPage } from '@/api/scans';

export type PageGroup = {
  id: string;
  pages: ScanPage[];
  version: string | null;
};

const rotations = [0, 90, 180, 270] as const;

export function groupsFrom(batch: ScanBatch): PageGroup[] {
  return batch.groups.map((group) => ({
    id: group.key,
    pages: group.pages,
    version: group.assignment_version,
  }));
}

/** Identifies a group by its pages so server keys can be matched after a save. */
export function pageSignature(pages: ScanPage[]) {
  return pages.map((page) => page.number).join(',');
}

export function nonempty(groups: PageGroup[]) {
  return groups.filter((group) => group.pages.length);
}

export function splitBefore(
  groups: PageGroup[],
  groupIndex: number,
  pageIndex: number
): PageGroup[] {
  const group = groups[groupIndex];
  if (!group || pageIndex <= 0 || pageIndex >= group.pages.length)
    return groups;
  return [
    ...groups.slice(0, groupIndex),
    { ...group, pages: group.pages.slice(0, pageIndex) },
    {
      id: crypto.randomUUID(),
      pages: group.pages.slice(pageIndex),
      version: group.version,
    },
    ...groups.slice(groupIndex + 1),
  ];
}

export function mergeWithPrevious(
  groups: PageGroup[],
  groupIndex: number
): PageGroup[] {
  if (groupIndex <= 0 || groupIndex >= groups.length) return groups;
  const previous = groups[groupIndex - 1];
  return [
    ...groups.slice(0, groupIndex - 1),
    { ...previous, pages: [...previous.pages, ...groups[groupIndex].pages] },
    ...groups.slice(groupIndex + 1),
  ];
}

/**
 * Moves a page to `index` within `groupIndex`; `groupIndex === groups.length`
 * starts a new group. `keepEmpty` holds emptied groups in place mid-drag.
 */
export function movePage(
  groups: PageGroup[],
  number: number,
  groupIndex: number,
  index: number,
  keepEmpty = false
): PageGroup[] {
  const page = groups
    .flatMap((group) => group.pages)
    .find((item) => item.number === number);
  if (!page) return groups;
  const next = groups.map((group) => ({
    ...group,
    pages: group.pages.filter((item) => item.number !== number),
  }));
  if (groupIndex >= next.length) {
    next.push({ id: crypto.randomUUID(), pages: [page], version: null });
  } else {
    const pages = [...next[groupIndex].pages];
    pages.splice(Math.min(index, pages.length), 0, page);
    next[groupIndex] = { ...next[groupIndex], pages };
  }
  return keepEmpty ? next : nonempty(next);
}

export function rotatePages(
  groups: PageGroup[],
  numbers: ReadonlySet<number>
): PageGroup[] {
  return groups.map((group) => ({
    ...group,
    pages: group.pages.map((page) =>
      numbers.has(page.number)
        ? {
            ...page,
            rotation:
              rotations[(rotations.indexOf(page.rotation ?? 0) + 1) % 4],
          }
        : page
    ),
  }));
}

export function excludePage(groups: PageGroup[], number: number): PageGroup[] {
  return nonempty(
    groups.map((group) => ({
      ...group,
      pages: group.pages.filter((page) => page.number !== number),
    }))
  );
}

/** Returns an excluded page to the group holding the nearest earlier page. */
export function restorePage(groups: PageGroup[], number: number): PageGroup[] {
  const page: ScanPage = { number, rotation: 0 };
  if (!groups.length)
    return [{ id: crypto.randomUUID(), pages: [page], version: null }];
  let target = 0;
  let best = -Infinity;
  groups.forEach((group, index) => {
    for (const item of group.pages)
      if (item.number < number && item.number > best) {
        best = item.number;
        target = index;
      }
  });
  return groups.map((group, index) => {
    if (index !== target) return group;
    const pages = [...group.pages];
    const at = pages.findIndex((item) => item.number > number);
    pages.splice(at === -1 ? pages.length : at, 0, page);
    return { ...group, pages };
  });
}

/** Formats page numbers as compact ranges, e.g. "1–3, 7". */
export function pageRanges(numbers: number[]) {
  const parts: string[] = [];
  let start = numbers[0];
  let end = start;
  for (const number of [...numbers.slice(1), NaN]) {
    if (number === end + 1) {
      end = number;
      continue;
    }
    if (start !== undefined)
      parts.push(start === end ? `${start}` : `${start}–${end}`);
    start = end = number;
  }
  return parts.join(', ');
}

/** Rotates a page thumbnail and shrinks it when sideways so it stays inside its portrait (17:22) frame. */
export function thumbnailTransform(rotation: number) {
  return rotation % 180 === 0
    ? `rotate(${rotation}deg)`
    : `rotate(${rotation}deg) scale(${17 / 22})`;
}
