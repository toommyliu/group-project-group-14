import { useRouterState } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { assignmentsQuery } from '@/api/queries';

export function useAssignmentNavigation() {
  const location = useRouterState({
    select: (state) => {
      const match = state.matches.find((item) => 'assignment' in item.search);
      return {
        assignmentId:
          match && 'assignment' in match.search
            ? match.search.assignment
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
