import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  notFound,
  redirect,
} from '@tanstack/react-router';
import type { QueryClient } from '@tanstack/react-query';
import {
  OverviewPage,
  ScansPage,
  SubmissionsPage,
} from './lib/workspace-pages';
import { validateAssignmentSearch } from './routes/assignment-search';
import {
  assignmentsQuery,
  assignmentSourceQuery,
  assignmentQAQuery,
  assignmentStatisticsQuery,
  submissionsQuery,
} from './api/queries';
import { scansQuery } from './api/scans';
import {
  loadAssignment,
  validateGradingSearch,
} from './routes/assignment-data';
import { AssignmentLoading, AssignmentsLoading } from './components/loading';
import { RouteError, RouteNotFound } from './components/route-error';

const rootRoute = createRootRouteWithContext<{
  queryClient: QueryClient;
}>()({
  notFoundComponent: RouteNotFound,
  component: lazyRouteComponent(() => import('./components/workspace')),
});

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  loader: ({ context }) =>
    context.queryClient.ensureQueryData(assignmentsQuery),
  component: lazyRouteComponent(() => import('./routes/assignments-page')),
  pendingComponent: AssignmentsLoading,
  errorComponent: RouteError,
});

export const newAssignmentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/new-assignment',
  component: lazyRouteComponent(() => import('./routes/new-assignment-page')),
  errorComponent: RouteError,
});

export const editAssignmentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/edit-assignment',
  validateSearch: validateAssignmentSearch,
  beforeLoad: ({ search }) => {
    throw redirect({
      to: '/assignment',
      search: { assignment: search.assignment },
      replace: true,
    });
  },
});

export const assignmentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/submissions',
  validateSearch: (search) => ({
    ...validateAssignmentSearch(search),
    ...validateGradingSearch(search),
  }),
  loaderDeps: ({ search }) => search,
  remountDeps: ({ search }) => search.assignment,
  loader: async ({ context, deps }) => {
    const [data] = await Promise.all([
      loadAssignment(context.queryClient, deps.assignment, deps),
      SubmissionsPage.preload?.(),
    ]);
    if (!data) throw notFound();
    return data;
  },
  pendingComponent: AssignmentLoading,
  // Keep the current response and controls usable until navigation finishes.
  pendingMs: Infinity,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const scansRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/scans',
  validateSearch: validateAssignmentSearch,
  loaderDeps: ({ search }) => search,
  remountDeps: ({ search }) => search.assignment,
  loader: async ({ context, deps }) => {
    const [assignments, scans, qa] = await Promise.all([
      context.queryClient.ensureQueryData(assignmentsQuery),
      context.queryClient.fetchQuery(scansQuery(deps.assignment)),
      context.queryClient.ensureQueryData(assignmentQAQuery(deps.assignment)),
      ScansPage.preload?.(),
    ]);
    const assignment = assignments.find((item) => item.id === deps.assignment);
    if (!assignment) throw notFound();
    return {
      assignment,
      scans,
      versions: Object.keys(qa.document.version_questions ?? {}),
    };
  },
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
});

export const overviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignment',
  validateSearch: validateAssignmentSearch,
  loaderDeps: ({ search }) => search,
  remountDeps: ({ search }) => search.assignment,
  loader: async ({ context, deps }) => {
    const [assignments, qa, scans, submissions, source] = await Promise.all([
      context.queryClient.ensureQueryData(assignmentsQuery),
      context.queryClient.fetchQuery({
        ...assignmentQAQuery(deps.assignment),
        staleTime: 0,
      }),
      context.queryClient.fetchQuery(scansQuery(deps.assignment)),
      context.queryClient.fetchQuery({
        ...submissionsQuery(deps.assignment),
        staleTime: 0,
      }),
      context.queryClient.fetchQuery({
        ...assignmentSourceQuery(deps.assignment),
        staleTime: 0,
      }),
      OverviewPage.preload?.(),
    ]);
    const assignment = assignments.find((item) => item.id === deps.assignment);
    if (!assignment) throw notFound();
    return { assignment, qa, scans, submissions, source };
  },
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
});

export const terminalsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/terminals',
  validateSearch: validateAssignmentSearch,
  loaderDeps: ({ search }) => search,
  remountDeps: ({ search }) => search.assignment,
  loader: async ({ context, deps }) => {
    const assignments =
      await context.queryClient.ensureQueryData(assignmentsQuery);
    const assignment = assignments.find((item) => item.id === deps.assignment);
    if (!assignment) throw notFound();
    return { assignment };
  },
  component: lazyRouteComponent(() => import('./routes/terminals-page')),
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
});

export const statisticsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/statistics',
  validateSearch: validateAssignmentSearch,
  loaderDeps: ({ search }) => search,
  remountDeps: ({ search }) => search.assignment,
  loader: async ({ context, deps }) => {
    const [assignments, statistics] = await Promise.all([
      context.queryClient.ensureQueryData(assignmentsQuery),
      context.queryClient.fetchQuery({
        ...assignmentStatisticsQuery(deps.assignment),
        staleTime: 0,
      }),
    ]);
    const assignment = assignments.find((item) => item.id === deps.assignment);
    if (!assignment) throw notFound();
    return { assignment, statistics };
  },
  component: lazyRouteComponent(
    () => import('./routes/assignment-statistics-page')
  ),
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
});

export function createAppRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree: rootRoute.addChildren([
      indexRoute,
      newAssignmentRoute,
      editAssignmentRoute,
      assignmentRoute,
      scansRoute,
      overviewRoute,
      terminalsRoute,
      statisticsRoute,
    ]),
    context: { queryClient },
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0,
    defaultPendingMs: 200,
    defaultPendingMinMs: 200,
    scrollRestoration: true,
    getScrollRestorationKey: (location) =>
      JSON.stringify([
        location.pathname,
        new URLSearchParams(location.searchStr).get('assignment'),
      ]),
  });
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
