import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  notFound,
  redirect,
} from '@tanstack/react-router';
import type { QueryClient } from '@tanstack/react-query';
import { OverviewPage, ScansPage, GradePage } from './lib/workspace-pages';
import {
  assignmentsQuery,
  assignmentSummariesQuery,
  assignmentSourceQuery,
  assignmentQAQuery,
  assignmentStatisticsQuery,
  submissionsQuery,
} from './api/queries';
import { authQuery } from './api/auth';
import { peopleQuery } from './api/people';
import { scansQuery } from './api/scans';
import { workspaceQuery } from './api/workspace';
import { workspaceBase } from './api/client';
import {
  loadAssignment,
  requireAssignment,
  validateGradingSearch,
} from './routes/assignment-data';
import {
  AssignmentLoading,
  AssignmentsLoading,
  GradeLoading,
} from './components/loading';
import { RouteError, RouteNotFound } from './components/route-error';

/** The single assignment of an active folder session, or null in a normal workspace. */
async function folderSessionAssignmentId(queryClient: QueryClient) {
  const workspace = await queryClient.ensureQueryData(workspaceQuery);
  return workspace.folder_session?.assignment_id ?? null;
}

const rootRoute = createRootRouteWithContext<{
  queryClient: QueryClient;
}>()({
  // Not-found pages under an assignment need it on first render; assignment pages report load errors.
  loader: async ({ context, location }) => {
    if (!workspaceBase && location.pathname.startsWith('/assignments'))
      throw redirect({ to: '/courses' });
    if (
      workspaceBase &&
      location.pathname.slice(workspaceBase.length).startsWith('/assignments/')
    )
      await context.queryClient
        .ensureQueryData(assignmentsQuery)
        .catch(() => undefined);
  },
  notFoundComponent: RouteNotFound,
  component: lazyRouteComponent(() => import('./components/root-layout')),
});

async function canOpenCourses(queryClient: QueryClient) {
  const auth = await queryClient
    .ensureQueryData(authQuery)
    .catch(() => undefined);
  return auth ? !auth.enabled || Boolean(auth.role) : false;
}

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: async ({ context }) => {
    if (!workspaceBase) return;
    const assignmentId = await folderSessionAssignmentId(context.queryClient);
    if (assignmentId)
      throw redirect({
        to: '/assignments/$assignmentId',
        params: { assignmentId },
      });
  },
  loader: ({ context }) =>
    workspaceBase
      ? context.queryClient.fetchQuery({
          ...assignmentSummariesQuery,
          staleTime: 0,
        })
      : null,
  pendingComponent: AssignmentsLoading,
  component: lazyRouteComponent(() => import('./routes/index-page')),
  errorComponent: RouteError,
});

export const coursesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/courses',
  beforeLoad: async ({ context }) => {
    if (!(await canOpenCourses(context.queryClient)))
      throw redirect({ to: '/' });
  },
  component: lazyRouteComponent(() => import('./routes/workspace-list')),
  errorComponent: RouteError,
});

export const assignmentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments',
  beforeLoad: async ({ context }) => {
    const assignmentId = await folderSessionAssignmentId(context.queryClient);
    if (assignmentId)
      throw redirect({
        to: '/assignments/$assignmentId',
        params: { assignmentId },
      });
  },
  loader: ({ context }) =>
    context.queryClient.fetchQuery({
      ...assignmentSummariesQuery,
      staleTime: 0,
    }),
  component: lazyRouteComponent(() => import('./routes/assignments-page')),
  pendingComponent: AssignmentsLoading,
  errorComponent: RouteError,
});

export const newAssignmentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/new',
  beforeLoad: async ({ context }) => {
    // A folder session grades one existing assignment; it cannot create another.
    const assignmentId = await folderSessionAssignmentId(context.queryClient);
    if (assignmentId)
      throw redirect({
        to: '/assignments/$assignmentId',
        params: { assignmentId },
      });
  },
  component: lazyRouteComponent(() => import('./routes/new-assignment-page')),
  errorComponent: RouteError,
});

export const gradeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/$assignmentId/grade',
  validateSearch: validateGradingSearch,
  loaderDeps: ({ search }) => search,
  remountDeps: ({ params }) => params.assignmentId,
  loader: async ({ context, params, deps }) => {
    const [data] = await Promise.all([
      loadAssignment(context.queryClient, params.assignmentId, deps),
      GradePage.preload?.(),
    ]);
    return data;
  },
  pendingComponent: GradeLoading,
  // Keep the current response and controls usable until navigation finishes.
  pendingMs: Infinity,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const scansRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/$assignmentId/scans',
  remountDeps: ({ params }) => params.assignmentId,
  beforeLoad: async ({ context, params: { assignmentId } }) => {
    // Scans cannot be added to a folder session; send bookmarks back to the overview.
    const workspace = await context.queryClient.ensureQueryData(workspaceQuery);
    if (workspace.folder_session)
      throw redirect({
        to: '/assignments/$assignmentId',
        params: { assignmentId },
      });
  },
  loader: async ({ context, params: { assignmentId } }) => {
    const page = ScansPage.preload?.();
    const assignment = await requireAssignment(
      context.queryClient,
      assignmentId
    );
    const [scans, qa] = await Promise.all([
      context.queryClient.fetchQuery(scansQuery(assignmentId)),
      context.queryClient.ensureQueryData(assignmentQAQuery(assignmentId)),
      page,
    ]);
    return {
      assignment,
      scans,
      versions: Object.keys(qa.document.version_questions ?? {}),
    };
  },
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const overviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/$assignmentId',
  remountDeps: ({ params }) => params.assignmentId,
  loader: async ({ context, params: { assignmentId } }) => {
    const page = OverviewPage.preload?.();
    const assignment = await requireAssignment(
      context.queryClient,
      assignmentId
    );
    const [qa, scans, submissions, source] = await Promise.all([
      context.queryClient.fetchQuery({
        ...assignmentQAQuery(assignmentId),
        staleTime: 0,
      }),
      context.queryClient.fetchQuery(scansQuery(assignmentId)),
      context.queryClient.fetchQuery({
        ...submissionsQuery(assignmentId),
        staleTime: 0,
      }),
      context.queryClient.fetchQuery({
        ...assignmentSourceQuery(assignmentId),
        staleTime: 0,
      }),
      page,
    ]);
    return { assignment, qa, scans, submissions, source };
  },
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const statisticsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/$assignmentId/statistics',
  remountDeps: ({ params }) => params.assignmentId,
  loader: async ({ context, params: { assignmentId } }) => {
    const assignment = await requireAssignment(
      context.queryClient,
      assignmentId
    );
    const statistics = await context.queryClient.fetchQuery({
      ...assignmentStatisticsQuery(assignmentId),
      staleTime: 0,
    });
    return { assignment, statistics };
  },
  component: lazyRouteComponent(
    () => import('./routes/assignment-statistics-page')
  ),
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const peopleRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/people',
  beforeLoad: async ({ context }) => {
    const auth = await context.queryClient.ensureQueryData(authQuery);
    if (auth.role !== 'instructor') throw notFound();
  },
  loader: ({ context }) =>
    context.queryClient.fetchQuery({ ...peopleQuery, staleTime: 0 }),
  component: lazyRouteComponent(() => import('./routes/people-page')),
  errorComponent: RouteError,
});

export function createAppRouter(queryClient: QueryClient) {
  return createRouter({
    basepath: workspaceBase || '/',
    routeTree: rootRoute.addChildren([
      indexRoute,
      coursesRoute,
      assignmentsRoute,
      newAssignmentRoute,
      gradeRoute,
      scansRoute,
      overviewRoute,
      statisticsRoute,
      peopleRoute,
    ]),
    context: { queryClient },
    // Fuzzy mode runs the parent assignment route's loader, and Workspace renders that page.
    notFoundMode: 'root',
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0,
    defaultPendingMs: 200,
    defaultPendingMinMs: 200,
    scrollRestoration: true,
    // Search params select within a page, so they share its scroll position.
    getScrollRestorationKey: (location) => location.pathname,
  });
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
