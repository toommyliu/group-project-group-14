import { overviewRoute } from '@/router';
import { AssignmentActions } from '@/components/assignment-actions';
import { AssignmentPreview } from '@/components/assignment-preview';
import { PageHeading } from '@/components/page-heading';

export default function AssignmentOverviewPage() {
  const { assignment, qa, scans, submissions } = overviewRoute.useLoaderData();
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
