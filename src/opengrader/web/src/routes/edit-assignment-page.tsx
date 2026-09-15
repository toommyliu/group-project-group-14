import { editAssignmentRoute } from '@/router';
import { AssignmentEditor } from './new-assignment-page';

export default function EditAssignmentPage() {
  const { assignment: assignmentId } = editAssignmentRoute.useSearch();
  const { qa_markdown } = editAssignmentRoute.useLoaderData();
  return (
    <AssignmentEditor
      key={assignmentId}
      assignmentId={assignmentId}
      initialSource={qa_markdown}
    />
  );
}
