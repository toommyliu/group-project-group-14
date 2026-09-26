import { useState } from 'react';
import { CircleAlert, LoaderCircle } from 'lucide-react';
import type { QAPreview } from '@/api/queries';
import { Button } from './ui/button';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';

export const validationMessageId = 'qa-error';

function summary(preview: QAPreview) {
  const { questions, versions } = preview.document;
  const points = questions.reduce(
    (total, question) =>
      total +
      (question.subquestions?.length
        ? question.subquestions.reduce((sum, part) => sum + part.points, 0)
        : question.points),
    0
  );
  return [
    `${questions.length} ${questions.length === 1 ? 'question' : 'questions'}`,
    `${points} ${points === 1 ? 'point' : 'points'}`,
    versions?.length ? `${versions.length} versions` : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

export function ValidationStatus({
  checking,
  error,
  place,
  preview,
  goTo,
  onRetry,
}: {
  checking: boolean;
  error?: string;
  place: string | null;
  preview?: QAPreview;
  goTo?: { label: string; onSelect: () => void };
  onRetry?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const icon = checking ? (
    <LoaderCircle
      className="size-4 shrink-0 text-muted-foreground motion-safe:animate-spin"
      aria-hidden="true"
    />
  ) : error ? (
    <CircleAlert className="size-4 shrink-0" aria-hidden="true" />
  ) : null;

  if (!error)
    return (
      <p
        role="status"
        className="flex min-w-0 flex-1 items-center gap-2 px-2 text-xs text-muted-foreground"
      >
        {icon}
        <span className="truncate">
          {preview ? summary(preview) : checking ? 'Checking…' : ''}
        </span>
      </p>
    );

  const location = place === null ? '' : `${place}: `;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            className="h-9 min-w-0 flex-1 justify-start gap-2 px-2 text-xs font-normal text-destructive hover:text-destructive"
          />
        }
      >
        {icon}
        <span id={validationMessageId} role="status" className="truncate">
          {location}
          {error}
        </span>
      </PopoverTrigger>
      <PopoverContent align="start" className="space-y-3 text-sm">
        <p className="break-words whitespace-pre-wrap">
          {location}
          {error}
        </p>
        <div className="flex flex-wrap gap-2">
          {goTo && (
            <Button
              type="button"
              size="sm"
              onClick={() => {
                setOpen(false);
                goTo.onSelect();
              }}
            >
              {goTo.label}
            </Button>
          )}
          {onRetry && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setOpen(false);
                onRetry();
              }}
            >
              Check again
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
