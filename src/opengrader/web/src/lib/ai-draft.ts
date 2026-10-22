import type { QuestionRun } from '@/api/queries';

export type AiDraft = {
  confidence: number | null;
  reviewed: boolean;
};

export function parseAiDraft(analysis: string): AiDraft | null {
  const match = /\[\s*AI\b([^\]]*)\]/i.exec(analysis);
  if (!match) return null;
  const fields = match[1]
    .split('·')
    .map((field) => field.trim())
    .filter(Boolean);
  const percent = fields.find((field) => /^\d{1,3}\s*%$/.test(field));
  const reviewed = fields.some((field) =>
    ['confirmed', 'overridden'].includes(field.toLowerCase())
  );
  return {
    confidence: percent ? parseInt(percent, 10) : null,
    reviewed,
  };
}

export function questionAiLabel(run: QuestionRun | undefined) {
  if (run?.status === 'queued') return 'AI queued';
  if (run?.status === 'reading' || run?.status === 'drafting')
    return 'AI working';
}
