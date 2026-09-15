import { createContext } from 'react';
import { lazyRouteComponent } from '@tanstack/react-router';
import type { assignmentRoute, overviewRoute, scansRoute } from '@/router';

export type WorkspacePage =
  | { path: '/assignment'; data: typeof overviewRoute.types.loaderData }
  | { path: '/scans'; data: typeof scansRoute.types.loaderData }
  | { path: '/submissions'; data: typeof assignmentRoute.types.loaderData };

export const WorkspacePages = createContext<WorkspacePage[]>([]);

export function pageKey(page: WorkspacePage) {
  return `${page.path}/${page.data.assignment.id}`;
}

export const OverviewPage = lazyRouteComponent(
  () => import('@/routes/assignment-overview-page')
);
export const ScansPage = lazyRouteComponent(
  () => import('@/routes/scans-page')
);
export const SubmissionsPage = lazyRouteComponent(
  () => import('@/components/assignment-view')
);
