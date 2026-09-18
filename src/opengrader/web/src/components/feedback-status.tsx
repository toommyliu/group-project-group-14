import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { feedbackJobsQuery, retryFeedback } from '@/api/queries';
import { Button } from './ui/button';
import { ErrorAlert } from './error-alert';

export function FeedbackStatus({
  assignmentId,
  submissionId,
}: {
  assignmentId: string;
  submissionId: number;
}) {
  const queries = useQueryClient();
  const jobs = useQuery(feedbackJobsQuery);
  const retry = useMutation({
    mutationFn: () => retryFeedback(assignmentId, submissionId),
    onSuccess: () => queries.invalidateQueries(feedbackJobsQuery),
  });
  const job = jobs.data?.find(
    (item) =>
      item.assignment_id === assignmentId && item.submission_id === submissionId
  );
  return (
    <div className="mt-4 text-sm">
      <p role="status">
        {job?.status === 'pending' || job?.status === 'running'
          ? 'Updating feedback PDF…'
          : ''}
      </p>
      {job?.status === 'failed' && (
        <div className="grid gap-2">
          <p>Grade saved. The feedback PDF could not be updated.</p>
          <p>{job.error}</p>
          <Button
            variant="outline"
            disabled={retry.isPending}
            onClick={() => retry.mutate()}
          >
            Retry feedback PDF
          </Button>
        </div>
      )}
      {job?.status === 'ready' && (
        <a
          className="underline"
          href={`/assignments/${encodeURIComponent(assignmentId)}/submissions/${submissionId}/feedback`}
        >
          Download feedback PDF
        </a>
      )}
      {retry.error && <ErrorAlert error={retry.error.message} />}
    </div>
  );
}
