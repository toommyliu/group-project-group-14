import type { QuestionEdit } from '@/api/queries';

type Item = QuestionEdit['rubric'][number];

/** Preserve relative credit when changing the maximum; bonus values stay fixed. */
export function rescaleRubric(
  rubric: Item[],
  previous: number,
  points: number,
  positive = rubric.some(
    (item) => item.label === 'Correct' && (item.points_delta ?? 0) > 0
  )
): Item[] {
  const scale = (value: number) =>
    previous > 0 ? Math.round(((value * points) / previous) * 100) / 100 : 0;
  return rubric.map((item) => {
    if (item.label === 'Bonus') return item;
    if (item.label === 'Correct')
      return { ...item, points_delta: positive ? points : 0 };
    if (item.label === 'Incorrect')
      return { ...item, points_delta: positive ? 0 : -points };
    if (item.subitems?.length) {
      const target = positive ? points : -points;
      let remaining = target;
      return {
        ...item,
        points_delta: null,
        subitems: item.subitems.map((child, index, children) => {
          const value =
            index === children.length - 1
              ? remaining
              : previous > 0
                ? scale(child.points_delta ?? 0)
                : Math.round((target / children.length) * 100) / 100;
          remaining = Math.round((remaining - value) * 100) / 100;
          return { ...child, points_delta: value };
        }),
      };
    }
    return {
      ...item,
      points_delta:
        item.points_delta == null
          ? null
          : previous > 0
            ? scale(item.points_delta)
            : -points / 2,
    };
  });
}
