import { createContext } from 'react';
import { lazyRouteComponent } from '@tanstack/react-router';
import type { gradeRoute, overviewRoute, scansRoute } from '@/router';

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
      path: '/assignments/$assignmentId/grade';
      data: typeof gradeRoute.types.loaderData;
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
export const GradePage = lazyRouteComponent(
  () => import('@/routes/grade-page')
);
