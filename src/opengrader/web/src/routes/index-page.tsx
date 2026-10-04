import { workspaceBase } from '@/api/client';
import { indexRoute } from '@/router';
import { AssignmentsList } from './assignments-page';
import HomePage from './home-page';

function CourseAssignments() {
  return <AssignmentsList loaded={indexRoute.useLoaderData() ?? []} />;
}

export default function IndexPage() {
  return workspaceBase ? <CourseAssignments /> : <HomePage />;
}
