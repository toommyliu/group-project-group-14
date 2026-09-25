import { Activity, Suspense, useState } from 'react';
import { Outlet, useRouterState } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { assignmentsQuery } from '@/api/queries';
import { App } from '@/App';
import {
  WorkspacePages,
  OverviewPage,
  ScansPage,
  SubmissionsPage,
  pageKey,
  type WorkspacePage,
} from '@/lib/workspace-pages';
import { AssignmentLoading } from './loading';

/** Retain visited pages; fresh route loaders still supply data on every return. */
export default function Workspace() {
  const current = useRouterState({
    select: ({ matches }): WorkspacePage | null => {
      for (const match of matches) {
        if (match.status !== 'success' || !match.loaderData) continue;
        switch (match.routeId) {
          case '/assignments/$assignmentId':
            return { path: match.routeId, data: match.loaderData };
          case '/assignments/$assignmentId/scans':
            return { path: match.routeId, data: match.loaderData };
          case '/assignments/$assignmentId/submissions':
            return { path: match.routeId, data: match.loaderData };
        }
      }
      return null;
    },
  });
  const { data: assignments } = useQuery(assignmentsQuery);
  const [cache, setCache] = useState<{
    pages: WorkspacePage[];
    activeKey: string | null;
  }>({ pages: [], activeKey: null });
  const { pages } = cache;
  const available = (page: WorkspacePage) =>
    !assignments ||
    assignments.some((item) => item.id === page.data.assignment.id);
  const activeKey = current && available(current) ? pageKey(current) : null;
  const retained = pages.filter(available);
  const saved = retained.find((page) => pageKey(page) === activeKey);
  if (
    current &&
    activeKey &&
    (saved?.data !== current.data || cache.activeKey !== activeKey)
  ) {
    const updated = saved
      ? retained.map((page) => (page === saved ? current : page))
      : [...retained, current];
    const assignmentId = current.data.assignment.id;
    const recent = [
      ...updated.filter((page) => page.data.assignment.id !== assignmentId),
      ...updated.filter((page) => page.data.assignment.id === assignmentId),
    ];
    // Bound retained DOM while keeping every visited page of an assignment together.
    const keep = new Set(
      [...new Set(recent.map((page) => page.data.assignment.id))].slice(-3)
    );
    setCache({
      pages: recent.filter((page) => keep.has(page.data.assignment.id)),
      activeKey,
    });
  } else if (retained.length !== pages.length) {
    setCache({ pages: retained, activeKey });
  }

  return (
    <WorkspacePages value={pages}>
      <App>
        {pages.map((page) => (
          <Activity
            key={pageKey(page)}
            mode={pageKey(page) === activeKey ? 'visible' : 'hidden'}
          >
            <Suspense fallback={<AssignmentLoading />}>
              {page.path === '/assignments/$assignmentId' ? (
                <OverviewPage data={page.data} />
              ) : page.path === '/assignments/$assignmentId/scans' ? (
                <ScansPage data={page.data} />
              ) : (
                <SubmissionsPage data={page.data} />
              )}
            </Suspense>
          </Activity>
        ))}
        <Outlet />
      </App>
    </WorkspacePages>
  );
}
