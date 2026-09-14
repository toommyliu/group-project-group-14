import { useRef, useState } from 'react';
import { Popover } from '@base-ui/react/popover';
import { ListTree } from 'lucide-react';
import type { QAPreview } from '@/api/queries';
import { Button } from './ui/button';

type Question = QAPreview['document']['questions'][number];

function QuestionLinks({
  questions,
  disabled,
  editorId,
  onSelect,
}: {
  questions: Question[];
  disabled: boolean;
  editorId: string;
  onSelect: (line: number) => void;
}) {
  return (
    <ol className="min-w-0">
      {questions.map((question) => (
        <li key={question.id} className="min-w-0">
          <Button
            type="button"
            variant="ghost"
            disabled={disabled || question.source_line == null}
            aria-controls={editorId}
            className="flex w-full min-w-0 justify-start gap-3 text-start"
            title={question.prompt}
            onClick={() => {
              if (question.source_line != null) onSelect(question.source_line);
            }}
          >
            <span className="shrink-0 font-medium">{question.id}</span>
            <span className="min-w-0 flex-1 whitespace-normal wrap-break-word font-normal">
              {question.prompt.replace(/\s+/g, ' ')}
            </span>
          </Button>
          {!!question.subquestions?.length && (
            <div className="min-w-0 ps-4">
              <QuestionLinks
                questions={question.subquestions}
                disabled={disabled}
                editorId={editorId}
                onSelect={onSelect}
              />
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

export function QAOutline({
  preview,
  outdated,
  disabled,
  onSelect,
  editorId,
}: {
  preview: QAPreview | undefined;
  outdated: boolean;
  disabled: boolean;
  onSelect: (line: number) => void;
  editorId: string;
}) {
  const [open, setOpen] = useState(false);
  const selectedLine = useRef<number | null>(null);
  function selectQuestion(line: number) {
    selectedLine.current = line;
    setOpen(false);
  }
  const document = preview?.document;
  const versions = document?.versions ?? [];
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        render={
          <Button
            type="button"
            variant="outline"
            className="min-h-8 gap-2 px-2.5 py-1 text-xs"
          />
        }
        disabled={disabled || !document}
      >
        <ListTree className="size-4" aria-hidden="true" />
        Outline{document && outdated ? ' · Outdated' : ''}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner
          side="bottom"
          align="end"
          sideOffset={8}
          className="z-50"
        >
          <Popover.Popup
            aria-label="QA outline"
            className="w-80 max-w-[calc(100vw-2rem)] overflow-hidden border bg-popover p-1 text-popover-foreground shadow-md"
            finalFocus={() => {
              const line = selectedLine.current;
              selectedLine.current = null;
              if (line === null) return true;
              // Jump after dismissal so focus restoration cannot pull the cursor out of the editor.
              onSelect(line);
              return false;
            }}
          >
            {document && (
              <nav
                aria-label="QA outline"
                className="max-h-[min(24rem,var(--available-height))] min-w-0 overflow-y-auto overscroll-contain"
              >
                {versions.length ? (
                  versions.map((version) => (
                    <div key={version} className="min-w-0">
                      <h3 className="sticky top-0 z-10 bg-popover px-3 py-2 font-semibold">
                        Version {version}
                      </h3>
                      <QuestionLinks
                        questions={document.version_questions?.[version] ?? []}
                        disabled={disabled || outdated}
                        editorId={editorId}
                        onSelect={selectQuestion}
                      />
                    </div>
                  ))
                ) : (
                  <QuestionLinks
                    questions={document.questions}
                    disabled={disabled || outdated}
                    editorId={editorId}
                    onSelect={selectQuestion}
                  />
                )}
              </nav>
            )}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
