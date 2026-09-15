import type { ReactNode } from 'react';

export const compactQuestionRowClassName =
  'rounded-lg border bg-background px-3 py-2';

export function QuestionRowHeading({
  label,
  prompt,
  handle,
}: {
  label: string;
  prompt?: string;
  handle: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      {handle}
      <h3 className="shrink-0 font-semibold">{label}</h3>
      {prompt !== undefined && (
        <span
          className="min-w-0 truncate text-sm text-muted-foreground"
          title={prompt}
        >
          {prompt}
        </span>
      )}
    </div>
  );
}
