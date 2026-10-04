import { useParams, useRouterState } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { workspaceBase } from '@/api/client';
import { assignmentsQuery } from '@/api/queries';

export function useAssignmentNavigation() {
  const { assignmentId } = useParams({ strict: false });
  const pathname = useRouterState({
    select: (state) =>
      state.location.pathname.slice(workspaceBase.length) || '/',
  });
  const { data } = useQuery({
    ...assignmentsQuery,
    enabled: Boolean(workspaceBase),
  });
  // The router gives unmatched URLs no params.
  const segment = /^\/assignments\/([^/]+)\//.exec(pathname)?.[1];
  return {
    assignment: data?.find((item) =>
      assignmentId === undefined
        ? segment === encodeURIComponent(item.id)
        : item.id === assignmentId
    ),
    pathname,
  };
}
