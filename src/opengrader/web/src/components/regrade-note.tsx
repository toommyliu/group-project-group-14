import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { regradeWithAnswerKey } from '@/api/queries';
import { ErrorAlert } from './error-alert';
import { Button } from './ui/button';

type Step =
  | { at: 'idle' }
  | { at: 'asking' }
  | { at: 'running' }
  | { at: 'done'; updated: number; problems: string[] };

/**
 * Shown after answers are saved: changing an answer changes no grade, and
 * this is how the work gets graded with it.
 *
 * Grading again scores every answer the answer key settles outright, which
 * replaces a grade entered by hand on those questions. So it is never one
 * click: the button asks first and says so.
 */
export function RegradeNote({ assignmentId }: { assignmentId: string }) {
  const client = useQueryClient();
  const [step, setStep] = useState<Step>({ at: 'idle' });
  const [error, setError] = useState<unknown>(null);

  async function run() {
    setStep({ at: 'running' });
    setError(null);
    try {
      const result = await regradeWithAnswerKey(assignmentId);
      // The grading has happened; a refresh that fails is not a failure of it.
      await client
        .invalidateQueries({ queryKey: ['assignments', assignmentId] })
        .catch(() => undefined);
      setStep({
        at: 'done',
        updated: result.updated,
        problems: result.problems,
      });
    } catch (cause) {
      setError(cause);
      setStep({ at: 'idle' });
    }
  }

  if (step.at === 'done')
    return (
      <div className="grid gap-2" role="status">
        <p>
          {step.updated === 0
            ? 'Graded again: no grade needed to change.'
            : step.updated === 1
              ? 'Graded again: 1 grade was updated.'
              : `Graded again: ${step.updated} grades were updated.`}
        </p>
        {step.problems.length > 0 && (
          <ul className="list-disc space-y-1 ps-5 text-muted-foreground">
            {step.problems.map((problem, index) => (
              <li key={index} className="wrap-anywhere">
                {problem}
              </li>
            ))}
          </ul>
        )}
      </div>
    );

  return (
    <div className="grid gap-3">
      <p className="text-muted-foreground">
        Grades that are already saved stay as they are until the work is graded
        again.
      </p>
      {step.at === 'asking' ? (
        <div className="grid gap-3 border border-dashed p-3">
          <p>
            Every answer the answer key settles outright (multiple choice,
            numbers, an exact accepted answer) is scored from it. On those
            questions this replaces a grade you entered by hand. Everything else
            you graded is kept, the scans are not read again, and the run is
            recorded in the history.
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" onClick={() => setStep({ at: 'idle' })}>
              Not now
            </Button>
            <Button onClick={() => void run()}>Yes, grade again</Button>
          </div>
        </div>
      ) : (
        <div>
          <Button
            variant="outline"
            disabled={step.at === 'running'}
            onClick={() => setStep({ at: 'asking' })}
          >
            {step.at === 'running'
              ? 'Grading again…'
              : 'Grade again with these answers'}
          </Button>
        </div>
      )}
      {error !== null && (
        <ErrorAlert error={error} fallback="Could not grade the work again." />
      )}
    </div>
  );
}
