import { useMutation, useQueryClient } from '@tanstack/react-query';
import { FileDown, FilePlus, LoaderCircle, TriangleAlert } from 'lucide-react';
import { cn } from 'cn';
import { workspaceUrl } from '@/api/client';
import {
  feedbackJobsQuery,
  retryFeedback,
  type FeedbackJob,
} from '@/api/queries';
import { buttonVariants } from './ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

export function FeedbackPdfButton({
  assignmentId,
  submissionId,
  job,
  graded,
}: {
  assignmentId: string;
  submissionId: number;
  job: FeedbackJob | undefined;
  graded: boolean;
}) {
  const queries = useQueryClient();
  const retry = useMutation({
    mutationFn: () => retryFeedback(assignmentId, submissionId),
    onSettled: () => queries.invalidateQueries(feedbackJobsQuery),
  });
  const icon = cn(
    buttonVariants({ variant: 'ghost', size: 'icon' }),
    'size-11'
  );

  if (!job && !graded)
    return <span aria-hidden="true" className="size-11 shrink-0" />;

  if (!job)
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              disabled={retry.isPending}
              onClick={() => retry.mutate()}
              aria-label={`Make the feedback PDF of submission ${submissionId}`}
              className={icon}
            />
          }
        >
          <FilePlus aria-hidden="true" />
        </TooltipTrigger>
        <TooltipContent className="max-w-72">
          {retry.error
            ? `Could not start the feedback PDF: ${retry.error.message}`
            : 'This grade was imported, so it has no feedback PDF yet. Click to make one.'}
        </TooltipContent>
      </Tooltip>
    );

  if (job.status === 'ready')
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <a
              href={workspaceUrl(
                `/api/assignments/${encodeURIComponent(assignmentId)}/submissions/${submissionId}/feedback`
              )}
              download
              aria-label={`Download the feedback PDF of submission ${submissionId}`}
              className={icon}
            />
          }
        >
          <FileDown aria-hidden="true" />
        </TooltipTrigger>
        <TooltipContent>Download feedback PDF</TooltipContent>
      </Tooltip>
    );

  if (job.status === 'failed')
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              disabled={retry.isPending}
              onClick={() => retry.mutate()}
              aria-label={`Retry the feedback PDF of submission ${submissionId}`}
              className={cn(icon, 'text-destructive')}
            />
          }
        >
          <TriangleAlert aria-hidden="true" />
        </TooltipTrigger>
        <TooltipContent className="max-w-72">
          The grade is saved, but the feedback PDF could not be made:{' '}
          {retry.error?.message || job.error || 'unknown error'}. Click to
          retry.
        </TooltipContent>
      </Tooltip>
    );

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            role="status"
            tabIndex={0}
            aria-label={`Updating the feedback PDF of submission ${submissionId}`}
            className={cn(icon, 'text-muted-foreground')}
          />
        }
      >
        <LoaderCircle className="motion-safe:animate-spin" aria-hidden="true" />
      </TooltipTrigger>
      <TooltipContent>Updating feedback PDF…</TooltipContent>
    </Tooltip>
  );
}
