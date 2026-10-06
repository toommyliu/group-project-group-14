import type { overviewRoute } from '@/router';
import { AssignmentEditor } from '@/components/assignment-editor';
import { useFolderSession } from '@/hooks/use-folder-session';

export default function AssignmentOverviewPage({
  data,
}: {
  data: typeof overviewRoute.types.loaderData;
}) {
  const { assignment, qa, scans, submissions, source, outline } = data;
  const folderSession = useFolderSession();
  // TODO: allow with confirmation
  const editUnavailableReason = folderSession
    ? 'In a folder session the answer key is the file in the class folder, so it cannot be edited here.'
    : scans.batches.length > 0
      ? 'Assignments with uploaded scans cannot be edited.'
      : submissions.length > 0
        ? 'Assignments with submissions cannot be edited.'
        : undefined;
  return (
    <AssignmentEditor
      key={assignment.id}
      assignmentId={assignment.id}
      initialSource={source.qa_markdown}
      overview={{
        assignment,
        qa,
        editUnavailableReason,
        hideDelete: !!folderSession,
        structureLocked: outline.versions_with_template.length > 0,
      }}
    />
  );
}
