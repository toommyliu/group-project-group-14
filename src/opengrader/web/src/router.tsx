import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  notFound,
} from '@tanstack/react-router';
import type { QueryClient } from '@tanstack/react-query';
import { App } from './App';
import {
  assignmentsQuery,
  assignmentSourceQuery,
  submissionsQuery,
} from './api/queries';
import {
  loadAssignment,
  validateGradingSearch,
} from './routes/assignment-data';
import { AssignmentLoading, AssignmentsLoading } from './components/loading';
import { RouteError, RouteNotFound } from './components/route-error';

const rootRoute = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  notFoundComponent: RouteNotFound,
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
  path: '/edit-assignment/$assignmentId',
  loader: async ({ context, params }) => {
    const submissions = await context.queryClient.fetchQuery(
      submissionsQuery(params.assignmentId)
    );
    if (submissions.length)
      throw new Error('Assignments with submissions cannot be edited.');
    return context.queryClient.fetchQuery(
      assignmentSourceQuery(params.assignmentId)
    );
  },
  component: lazyRouteComponent(() => import('./routes/edit-assignment-page')),
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
});

export const assignmentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/grade/$assignmentId',
  validateSearch: validateGradingSearch,
  loaderDeps: ({ search }) => search,
  loader: async ({ context, params, deps }) => {
    const data = await loadAssignment(
      context.queryClient,
      params.assignmentId,
      deps
    );
    if (!data) throw notFound();
    return data;
  },
  component: lazyRouteComponent(() => import('./components/assignment-view')),
  pendingComponent: AssignmentLoading,
  // Keep the current response and controls usable until navigation finishes.
  pendingMs: Infinity,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export function createAppRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree: rootRoute.addChildren([
      indexRoute,
      newAssignmentRoute,
      editAssignmentRoute,
      assignmentRoute,
    ]),
    context: { queryClient },
    InnerWrap: App,
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0,
    defaultPendingMs: 200,
    defaultPendingMinMs: 200,
    scrollRestoration: true,
  });
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
