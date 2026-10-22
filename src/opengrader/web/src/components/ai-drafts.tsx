import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import {
  aiDraftJobQuery,
  cancelAiDrafts,
  dismissAiDrafts,
  planAiDrafts,
  startAiDrafts,
  type DraftJob,
  type QuestionRun,
} from '@/api/queries';
import { errorMessage } from '@/lib/error-message';
import { Button } from './ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from './ui/alert-dialog';

function plural(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

function active(job: DraftJob | null | undefined) {
  return job?.status === 'starting' || job?.status === 'running';
}

function working(run: QuestionRun) {
  return run.status === 'reading' || run.status === 'drafting';
}

function total(job: DraftJob, key: 'drafted' | 'low_confidence') {
  return job.questions.reduce((sum, run) => sum + run[key], 0);
}

function jobSummary(job: DraftJob) {
  if (job.status === 'starting') return 'Starting the AI…';
  if (job.status === 'failed') return job.message;
  if (job.status === 'running') {
    const queued = job.questions.filter((run) => run.status !== 'skipped');
    const current = queued.findIndex(working);
    return current < 0
      ? 'AI is drafting grades…'
      : `AI is on Question ${queued[current].question_id} (${current + 1} of ${queued.length})`;
  }
  const parts = [
    `${job.status === 'cancelled' ? 'Cancelled after drafting' : 'AI drafted'} ${plural(total(job, 'drafted'), 'grade')}`,
  ];
  const low = total(job, 'low_confidence');
  if (low) parts.push(`${low} left for you`);
  return parts.join(' · ');
}

export function AiDrafts({ assignmentId }: { assignmentId: string }) {
  const client = useQueryClient();
  const query = aiDraftJobQuery(assignmentId);
  const { data: job } = useQuery(query);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState<string | null>(null);

  async function attempt(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setConfirm(null);
    client.setQueryData(query.queryKey, await startAiDrafts(assignmentId));
  }

  const text = error || (job && jobSummary(job));
  const failed = Boolean(error) || job?.status === 'failed';

  return (
    <div className="flex max-w-full flex-wrap items-center justify-end gap-x-3 gap-y-1 text-sm">
      {text && (
        <p
          role={failed ? 'alert' : 'status'}
          className={`min-w-0 wrap-anywhere ${failed ? 'text-destructive' : 'text-muted-foreground'}`}
        >
          {text}
        </p>
      )}
      {active(job) ? (
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void attempt(async () => {
              await cancelAiDrafts(assignmentId);
              await client.invalidateQueries({ queryKey: query.queryKey });
            })
          }
        >
          Cancel AI
        </Button>
      ) : (
        <>
          {job && (
            <Button
              variant="ghost"
              className="size-11"
              aria-label="Dismiss AI results"
              title="Dismiss"
              disabled={busy}
              onClick={() =>
                void attempt(async () => {
                  await dismissAiDrafts(assignmentId);
                  client.setQueryData(query.queryKey, null);
                })
              }
            >
              <X aria-hidden="true" />
            </Button>
          )}
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              void attempt(async () => {
                const plan = await planAiDrafts(assignmentId);
                if (plan.confirm) setConfirm(plan.confirm);
                else await start();
              })
            }
          >
            Draft grades with AI
          </Button>
        </>
      )}
      <AlertDialog
        open={confirm !== null}
        onOpenChange={(open) => !open && setConfirm(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Send answers to a cloud AI?</AlertDialogTitle>
            <AlertDialogDescription className="whitespace-pre-wrap">
              {confirm}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Don’t send</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={() => void attempt(start)}
            >
              Send and draft
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function questionSummary(run: QuestionRun, job: DraftJob) {
  switch (run.status) {
    case 'queued': {
      const current = job.questions.find(working);
      return current
        ? `Waiting for the AI to finish Question ${current.question_id}.`
        : 'Waiting for the AI.';
    }
    case 'reading':
      return `AI is reading answers: ${Math.min(run.done + 1, run.total)} of ${run.total}`;
    case 'drafting':
      return `AI is drafting grades: ${Math.min(run.done + 1, run.total)} of ${run.total}`;
    case 'skipped':
      return 'AI skipped this question because it has no rubric.';
    case 'stopped':
      return 'AI stopped before finishing this question.';
    case 'done': {
      const parts = [];
      if (run.read) parts.push(`Read ${plural(run.read, 'answer')}`);
      parts.push(`AI drafted ${plural(run.drafted, 'grade')}`);
      if (run.low_confidence)
        parts.push(`${run.low_confidence} left for you (low confidence)`);
      if (run.unreadable) parts.push(`${run.unreadable} couldn’t be read`);
      if (run.errors) parts.push(`${run.errors} failed`);
      if (run.skipped) parts.push(`${run.skipped} skipped (changed meanwhile)`);
      return parts.join(' · ');
    }
  }
}

export function QuestionAiStatus({
  assignmentId,
  questionId,
}: {
  assignmentId: string;
  questionId: string;
}) {
  const { data: job } = useQuery(aiDraftJobQuery(assignmentId));
  const run = job?.questions.find((item) => item.question_id === questionId);
  if (!job || !run) return null;
  return (
    <p role="status" className="text-sm wrap-anywhere text-muted-foreground">
      {questionSummary(run, job)}
    </p>
  );
}
