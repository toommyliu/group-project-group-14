import type { QAPreview } from '@/api/queries';
import {
  previewOutlineGroups,
  questionOutlineEntries,
} from './assignment-outline';

export type LineHint = {
  line: number;
  text: string;
  tone: 'info' | 'warning' | 'error';
};

export function markdownLineHints(preview: QAPreview): LineHint[] {
  const questions = questionOutlineEntries(
    previewOutlineGroups(preview.document)
  ).flatMap((entry): LineHint[] => {
    if (entry.line === null) return [];
    const name = entry.depth
      ? `Part ${entry.number}`
      : `Question ${entry.number}`;
    const graded = entry.kind !== 'Question group';
    const unscored = graded && entry.points === 0;
    const points = unscored
      ? 'no points'
      : `${entry.points} ${entry.points === 1 ? 'point' : 'points'}`;
    return [
      {
        line: entry.line,
        text: `${name} · ${points} · ${entry.kind}`,
        tone: unscored ? 'warning' : 'info',
      },
    ];
  });
  const notes = preview.notes.map((note): LineHint => ({
    line: note.line,
    text: note.message,
    tone: note.kind === 'ignored' ? 'warning' : 'info',
  }));
  return [...questions, ...notes].toSorted((a, b) => a.line - b.line);
}
