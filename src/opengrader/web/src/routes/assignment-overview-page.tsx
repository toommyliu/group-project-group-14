import type { overviewRoute } from '@/router';
import { AssignmentActions } from '@/components/assignment-actions';
import { AssignmentPreview } from '@/components/assignment-preview';
import { PageHeading } from '@/components/page-heading';

export default function AssignmentOverviewPage({
  data,
}: {
  data: typeof overviewRoute.types.loaderData;
}) {
  const { assignment, qa, scans, submissions } = data;
  return (
    <>
      <PageHeading
        title="Overview"
        description={assignment.title}
        actions={
          <AssignmentActions
            assignment={assignment}
            editUnavailableReason={
              scans.batches.length > 0
                ? 'Assignments with uploaded scans cannot be edited.'
                : submissions.length > 0
                  ? 'Assignments with submissions cannot be edited.'
                  : undefined
            }
          />
        }
      />
      <AssignmentPreview assignmentId={assignment.id} preview={qa} />
    </>
  );
}
