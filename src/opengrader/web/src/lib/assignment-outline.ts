import type { QAPreview } from '@/api/queries';

type Question = QAPreview['document']['questions'][number];

export type OutlineGroup = {
  key: string;
  label: string;
  questions: Question[];
};

export type OutlineEntry = {
  key: string;
  version: string;
  versionLabel: string;
  number: string;
  depth: number;
  prompt: string;
  kind:
    | 'Question group'
    | 'Written answer'
    | 'Multiple choice'
    | 'Multiple answer';
  points: number;
  line: number | null;
};

export function previewOutlineGroups(
  document: QAPreview['document']
): OutlineGroup[] {
  return document.versions?.length
    ? document.versions.map((version) => ({
        key: version,
        label: version,
        questions: document.version_questions?.[version] ?? [],
      }))
    : [{ key: '', label: '', questions: document.questions }];
}

/** Use draft identities for navigation; question numbers change when items move. */
export function questionOutlineEntries(groups: OutlineGroup[]): OutlineEntry[] {
  return groups.flatMap((group) => {
    function entries(
      questions: Question[],
      prefix = '',
      depth = 0
    ): OutlineEntry[] {
      return questions.flatMap((question, index) => {
        const number = `${prefix}${index + 1}`;
        return [
          {
            key: JSON.stringify([
              group.key,
              question.source_line ?? question.id,
            ]),
            version: group.key,
            versionLabel: group.label,
            number,
            depth,
            prompt:
              question.prompt.replace(/\s+/g, ' ').trim() ||
              'Untitled question',
            kind: question.subquestions?.length
              ? 'Question group'
              : question.choices?.length
                ? (question.correct_choice_labels?.length ?? 0) > 1
                  ? 'Multiple answer'
                  : 'Multiple choice'
                : 'Written answer',
            points: question.points,
            line: question.source_line ?? null,
          } satisfies OutlineEntry,
          ...entries(question.subquestions ?? [], `${number}.`, depth + 1),
        ];
      });
    }
    return entries(group.questions);
  });
}
