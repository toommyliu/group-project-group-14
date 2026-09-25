import { createContext } from 'react';
import { lazyRouteComponent } from '@tanstack/react-router';
import type { overviewRoute, scansRoute, submissionsRoute } from '@/router';

export type WorkspacePage =
  | {
      path: '/assignments/$assignmentId';
      data: typeof overviewRoute.types.loaderData;
    }
  | {
      path: '/assignments/$assignmentId/scans';
      data: typeof scansRoute.types.loaderData;
    }
  | {
      path: '/assignments/$assignmentId/submissions';
      data: typeof submissionsRoute.types.loaderData;
    };

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
