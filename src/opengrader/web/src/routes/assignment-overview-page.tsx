import type { overviewRoute } from '@/router';
import { AssignmentEditor } from './new-assignment-page';

export default function AssignmentOverviewPage({
  data,
}: {
  data: typeof overviewRoute.types.loaderData;
}) {
  const { assignment, qa, scans, submissions, source } = data;
  // TODO: allow with confirmation
  const editUnavailableReason =
    scans.batches.length > 0
      ? 'Assignments with uploaded scans cannot be edited.'
      : submissions.length > 0
        ? 'Assignments with submissions cannot be edited.'
        : undefined;
  return (
    <AssignmentEditor
      key={assignment.id}
      assignmentId={assignment.id}
      initialSource={source.qa_markdown}
      overview={{ assignment, qa, editUnavailableReason }}
    />
  );
}
