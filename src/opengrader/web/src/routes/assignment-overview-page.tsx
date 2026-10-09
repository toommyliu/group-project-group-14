import { useQuery } from '@tanstack/react-query';
import { assignmentQAQuery, assignmentSourceQuery } from '@/api/queries';
import type { overviewRoute } from '@/router';
import { AssignmentEditor } from '@/components/assignment-editor';
import { useFolderSession } from '@/hooks/use-folder-session';

export default function AssignmentOverviewPage({
  data,
}: {
  data: typeof overviewRoute.types.loaderData;
}) {
  const { assignment, scans, submissions, outline } = data;
  // Read through the queries, not only the route's first load, so an answer
  // key that changes while this page is open — a rubric edited here, or in
  // the class folder — is shown without leaving the page.
  const { data: qa } = useQuery({
    ...assignmentQAQuery(assignment.id),
    initialData: data.qa,
  });
  const { data: source } = useQuery({
    ...assignmentSourceQuery(assignment.id),
    initialData: data.source,
  });
  const folderSession = useFolderSession();
  // TODO: allow with confirmation
  const editUnavailableReason = folderSession
    ? 'In a folder session the answer key is the file in the class folder. Each rubric can be edited here and is saved straight into that file; edit anything else in the file itself.'
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
        rubricEditable: !!folderSession,
        hideDelete: !!folderSession,
        structureLocked: outline.versions_with_template.length > 0,
      }}
    />
  );
}
