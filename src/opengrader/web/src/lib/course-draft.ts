import type { Term } from '@/api/workspace';

export type CourseDraft = {
  number: string;
  name: string;
  term: Term | null;
  year: number | null;
};

export type CourseDraftErrors = Partial<Record<'number' | 'name', string>>;

export function courseDraftErrors(draft: CourseDraft): CourseDraftErrors {
  return {
    ...(!draft.number.trim() && { number: 'Enter a course number.' }),
    ...(!draft.name.trim() && { name: 'Enter a course name.' }),
  };
}
