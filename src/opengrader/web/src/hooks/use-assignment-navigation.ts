import { useParams, useRouterState } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { assignmentsQuery } from '@/api/queries';

export function useAssignmentNavigation() {
  const { assignmentId } = useParams({ strict: false });
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const { data } = useQuery(assignmentsQuery);
  return {
    assignment: data?.find((item) => item.id === assignmentId),
    pathname,
  };
}
