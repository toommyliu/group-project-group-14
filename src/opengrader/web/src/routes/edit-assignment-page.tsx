import { editAssignmentRoute } from '@/router';
import { AssignmentEditor } from './new-assignment-page';

export default function EditAssignmentPage() {
  const { assignmentId } = editAssignmentRoute.useParams();
  const { qa_markdown } = editAssignmentRoute.useLoaderData();
  return (
    <AssignmentEditor
      key={assignmentId}
      assignmentId={assignmentId}
      initialSource={qa_markdown}
    />
  );
}
