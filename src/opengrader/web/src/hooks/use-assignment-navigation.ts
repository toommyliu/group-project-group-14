import { useRouterState } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { assignmentsQuery } from '@/api/queries';

export function useAssignmentNavigation() {
  const location = useRouterState({
    select: (state) => {
      const match = state.matches.find((item) => 'assignmentId' in item.params);
      return {
        assignmentId:
          match && 'assignmentId' in match.params
            ? match.params.assignmentId
            : undefined,
        pathname: state.location.pathname,
      };
    },
  });
  const { data } = useQuery(assignmentsQuery);
  return {
    assignment: data?.find((item) => item.id === location.assignmentId),
    pathname: location.pathname,
  };
}
