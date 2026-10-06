import type { Outline, RegionBox } from '@/api/outline';

/** A rectangle on one template page, as fractions of the page size. */
export type Box = {
  page: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

/** A graded row: a question without parts, or one part of a question. */
export type Leaf = {
  key: string;
  id: string | null;
  title: string;
  points: number;
  boxes: Box[];
};

export type DraftQuestion = Leaf & { parts: Leaf[] };

export type Draft = {
  questions: DraftQuestion[];
  name: Box | null;
  studentId: Box | null;
};

export const NAME = 'name';
export const STUDENT_ID = 'student-id';
export const MIN_BOX = 0.01;

/** Whose box this is: a leaf's key, or one of the identity fields. */
export type Owner = string;

export type Row = {
  key: string;
  label: string;
  question: DraftQuestion;
  leaf: Leaf | null;
};

const newKey = () => crypto.randomUUID();

const fromRegion = (box: RegionBox): Box => ({
  page: box.page,
  x: box.bbox[0],
  y: box.bbox[1],
  width: box.bbox[2],
  height: box.bbox[3],
});

const toRegion = (box: Box): RegionBox => ({
  page: box.page,
  bbox: [box.x, box.y, box.width, box.height].map(
    (value) => Math.round(value * 10000) / 10000
  ),
});

export function draftFromOutline(outline: Outline): Draft {
  return {
    questions: (outline.questions ?? []).map((question) => ({
      key: newKey(),
      id: question.id ?? null,
      title: question.title,
      points: question.points,
      boxes: (question.boxes ?? []).map(fromRegion),
      parts: (question.parts ?? []).map((part) => ({
        key: newKey(),
        id: part.id ?? null,
        title: part.title,
        points: part.points,
        boxes: (part.boxes ?? []).map(fromRegion),
      })),
    })),
    name: outline.name_box ? fromRegion(outline.name_box) : null,
    studentId: outline.id_box ? fromRegion(outline.id_box) : null,
  };
}

export function outlineFromDraft(draft: Draft): Outline {
  const leaf = ({ id, title, points, boxes }: Leaf) => ({
    id,
    title,
    points,
    boxes: boxes.map(toRegion),
  });
  return {
    questions: draft.questions.map((question) => ({
      ...leaf(question),
      parts: question.parts.map(leaf),
    })),
    name_box: draft.name ? toRegion(draft.name) : null,
    id_box: draft.studentId ? toRegion(draft.studentId) : null,
  };
}

export function sameOutline(first: Draft, second: Draft) {
  return (
    JSON.stringify(outlineFromDraft(first)) ===
    JSON.stringify(outlineFromDraft(second))
  );
}

const sameBoxes = (first: Box[], second: Box[]) =>
  JSON.stringify(first.map(toRegion)) === JSON.stringify(second.map(toRegion));

const listed = (box: Box | null) => (box ? [box] : []);

export function boxChanges(before: Draft, after: Draft) {
  const saved = new Map(
    rows(before).map((row) => [row.key, row.leaf?.boxes ?? []])
  );
  return {
    rows: rows(after).flatMap(({ key, label, leaf }) =>
      leaf && !sameBoxes(saved.get(key) ?? [], leaf.boxes)
        ? [{ label, leaf }]
        : []
    ),
    name: !sameBoxes(listed(before.name), listed(after.name)),
    studentId: !sameBoxes(listed(before.studentId), listed(after.studentId)),
  };
}

export type BoxChanges = ReturnType<typeof boxChanges>;

/** Every outline row in display order: questions, then each question's parts. */
export function rows(draft: Draft): Row[] {
  return draft.questions.flatMap((question, index) => [
    {
      key: question.key,
      label: `${index + 1}`,
      question,
      leaf: question.parts.length ? null : question,
    },
    ...question.parts.map((part, partIndex) => ({
      key: part.key,
      label: `${index + 1}.${partIndex + 1}`,
      question,
      leaf: part,
    })),
  ]);
}

export function findRow(draft: Draft, key: string | null) {
  return rows(draft).find((row) => row.key === key) ?? null;
}

export function questionPoints(question: DraftQuestion) {
  return question.parts.length
    ? question.parts.reduce((total, part) => total + part.points, 0)
    : question.points;
}

export function totalPoints(draft: Draft) {
  return draft.questions.reduce(
    (total, question) => total + questionPoints(question),
    0
  );
}

export function ownerBoxes(draft: Draft, owner: Owner): Box[] {
  if (owner === NAME) return draft.name ? [draft.name] : [];
  if (owner === STUDENT_ID) return draft.studentId ? [draft.studentId] : [];
  return findRow(draft, owner)?.leaf?.boxes ?? [];
}

export function ownerLabel(draft: Draft, owner: Owner) {
  if (owner === NAME) return 'Name';
  if (owner === STUDENT_ID) return 'ID';
  return findRow(draft, owner)?.label ?? '';
}

export function clampBox(box: Box): Box {
  const width = Math.min(1, Math.max(MIN_BOX, box.width));
  const height = Math.min(1, Math.max(MIN_BOX, box.height));
  return {
    page: box.page,
    x: Math.min(1 - width, Math.max(0, box.x)),
    y: Math.min(1 - height, Math.max(0, box.y)),
    width,
    height,
  };
}

function mapLeaf(draft: Draft, key: string, change: (leaf: Leaf) => Leaf) {
  return {
    ...draft,
    questions: draft.questions.map((question) =>
      question.key === key
        ? { ...question, ...change(question) }
        : {
            ...question,
            parts: question.parts.map((part) =>
              part.key === key ? change(part) : part
            ),
          }
    ),
  };
}

export function setOwnerBoxes(draft: Draft, owner: Owner, boxes: Box[]): Draft {
  if (owner === NAME) return { ...draft, name: boxes[0] ?? null };
  if (owner === STUDENT_ID) return { ...draft, studentId: boxes[0] ?? null };
  return mapLeaf(draft, owner, (leaf) => ({ ...leaf, boxes }));
}

export function addQuestion(draft: Draft, boxes: Box[] = []): [Draft, string] {
  const key = newKey();
  return [
    {
      ...draft,
      questions: [
        ...draft.questions,
        { key, id: null, title: '', points: 1, boxes, parts: [] },
      ],
    },
    key,
  ];
}

/** The first part takes over the question's regions and answer key. */
export function addPart(draft: Draft, questionKey: string): [Draft, string] {
  const key = newKey();
  return [
    {
      ...draft,
      questions: draft.questions.map((question) => {
        if (question.key !== questionKey) return question;
        const first: Leaf[] = question.parts.length
          ? []
          : [
              {
                key: newKey(),
                id: question.id,
                title: '',
                points: question.points,
                boxes: question.boxes,
              },
            ];
        return {
          ...question,
          boxes: [],
          parts: [
            ...question.parts,
            ...first,
            { key, id: null, title: '', points: 1, boxes: [] },
          ],
        };
      }),
    },
    key,
  ];
}

export function updateRow(
  draft: Draft,
  key: string,
  change: Partial<Pick<Leaf, 'title' | 'points'>>
): Draft {
  return mapLeaf(draft, key, (leaf) => ({ ...leaf, ...change }));
}

export function removeRow(draft: Draft, key: string): Draft {
  return {
    ...draft,
    questions: draft.questions
      .filter((question) => question.key !== key)
      .map((question) => ({
        ...question,
        parts: question.parts.filter((part) => part.key !== key),
      })),
  };
}

export function moveQuestion(draft: Draft, key: string, offset: number): Draft {
  const index = draft.questions.findIndex((question) => question.key === key);
  const target = index + offset;
  if (index < 0 || target < 0 || target >= draft.questions.length) return draft;
  const questions = [...draft.questions];
  const [moved] = questions.splice(index, 1);
  questions.splice(target, 0, moved);
  return { ...draft, questions };
}

export function movePart(draft: Draft, key: string, offset: number): Draft {
  return {
    ...draft,
    questions: draft.questions.map((question) => {
      const index = question.parts.findIndex((part) => part.key === key);
      const target = index + offset;
      if (index < 0 || target < 0 || target >= question.parts.length)
        return question;
      const parts = [...question.parts];
      const [moved] = parts.splice(index, 1);
      parts.splice(target, 0, moved);
      return { ...question, parts };
    }),
  };
}

/** Number questions in reading order: by page, then top to bottom. */
export function sortByPosition(draft: Draft): Draft {
  const first = (boxes: Box[]) =>
    boxes.reduce<Box | null>(
      (best, box) =>
        !best ||
        box.page < best.page ||
        (box.page === best.page && box.y < best.y)
          ? box
          : best,
      null
    );
  const position = (question: DraftQuestion) => {
    const box = first([
      ...question.boxes,
      ...question.parts.flatMap((part) => part.boxes),
    ]);
    return box ? box.page + box.y : Number.POSITIVE_INFINITY;
  };
  return {
    ...draft,
    questions: draft.questions
      .map((question, index) => ({ question, index }))
      .sort(
        (a, b) =>
          position(a.question) - position(b.question) || a.index - b.index
      )
      .map(({ question }) => question),
  };
}
