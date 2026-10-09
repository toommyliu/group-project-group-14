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
  submissionAnswersQuery,
  submissionsQuery,
} from './api/queries';
import { authQuery, admitted } from './api/auth';
import { outlineQuery } from './api/outline';
import { rosterQuery } from './api/roster';
import { scansQuery } from './api/scans';
import { staffQuery } from './api/staff';
import { coursesQuery, workspaceQuery } from './api/workspace';
import { workspaceBase } from './api/client';
import {
  loadAssignment,
  requireAssignment,
  validateGradingSearch,
} from './routes/assignment-data';
import {
  AssignmentLoading,
  AssignmentsLoading,
  CoursesLoading,
  GradeLoading,
  PeopleLoading,
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

async function loadRoster(queryClient: QueryClient) {
  const auth = await queryClient.ensureQueryData(authQuery);
  return workspaceBase && (!auth.enabled || auth.role === 'instructor')
    ? queryClient.fetchQuery({ ...rosterQuery, staleTime: 0 })
    : null;
}

async function canOpenCourses(queryClient: QueryClient) {
  const auth = await queryClient
    .ensureQueryData(authQuery)
    .catch(() => undefined);
  return auth ? admitted(auth) : false;
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
  validateSearch: (search: Record<string, unknown>): { term?: string } => ({
    term:
      typeof search.term === 'string' && search.term ? search.term : undefined,
  }),
  beforeLoad: async ({ context }) => {
    if (!(await canOpenCourses(context.queryClient)))
      throw redirect({ to: '/' });
  },
  // The page shows its own error and retry for a failed load.
  loader: ({ context }) =>
    context.queryClient.ensureQueryData(coursesQuery).catch(() => undefined),
  pendingComponent: CoursesLoading,
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
  validateSearch: (search: Record<string, unknown>): { write?: true } => ({
    write: search.write === true || search.write === 'true' ? true : undefined,
  }),
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
    const [scans, qa, outline] = await Promise.all([
      context.queryClient.fetchQuery(scansQuery(assignmentId)),
      context.queryClient.ensureQueryData(assignmentQAQuery(assignmentId)),
      context.queryClient.fetchQuery({
        ...outlineQuery(assignmentId),
        staleTime: 0,
      }),
      page,
    ]);
    return {
      assignment,
      scans,
      versions: Object.keys(qa.document.version_questions ?? {}),
      templatePages: outline.template?.page_count ?? null,
      unoutlined: outline.versions.filter(
        (version) => !outline.versions_with_regions.includes(version)
      ),
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
    const [qa, scans, submissions, source, outline] = await Promise.all([
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
      context.queryClient.fetchQuery({
        ...outlineQuery(assignmentId),
        staleTime: 0,
      }),
      page,
    ]);
    return { assignment, qa, scans, submissions, source, outline };
  },
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const outlineRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/$assignmentId/outline',
  validateSearch: (search: Record<string, unknown>): { version?: string } => ({
    version:
      typeof search.version === 'string' && search.version
        ? search.version
        : undefined,
  }),
  loaderDeps: ({ search }) => search,
  remountDeps: ({ params }) => params.assignmentId,
  beforeLoad: async ({ context, params: { assignmentId } }) => {
    const workspace = await context.queryClient.ensureQueryData(workspaceQuery);
    if (workspace.folder_session)
      throw redirect({
        to: '/assignments/$assignmentId',
        params: { assignmentId },
      });
  },
  loader: async ({ context, params: { assignmentId }, deps }) => {
    const assignment = await requireAssignment(
      context.queryClient,
      assignmentId
    );
    const outline = await context.queryClient.fetchQuery({
      ...outlineQuery(assignmentId, deps.version),
      staleTime: 0,
    });
    return { assignment, outline };
  },
  component: lazyRouteComponent(() => import('./routes/outline-page')),
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const submissionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/$assignmentId/submissions',
  remountDeps: ({ params }) => params.assignmentId,
  loader: async ({ context, params: { assignmentId } }) => {
    const assignment = await requireAssignment(
      context.queryClient,
      assignmentId
    );
    const [submissions, students] = await Promise.all([
      context.queryClient.fetchQuery({
        ...submissionsQuery(assignmentId),
        staleTime: 0,
      }),
      loadRoster(context.queryClient),
    ]);
    return { assignment, submissions, students };
  },
  component: lazyRouteComponent(() => import('./routes/submissions-page')),
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const reviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/$assignmentId/review',
  remountDeps: ({ params }) => params.assignmentId,
  loader: async ({ context, params: { assignmentId } }) => {
    const assignment = await requireAssignment(
      context.queryClient,
      assignmentId
    );
    const [submissions, qa, students] = await Promise.all([
      context.queryClient.fetchQuery({
        ...submissionsQuery(assignmentId),
        staleTime: 0,
      }),
      context.queryClient.fetchQuery({
        ...assignmentQAQuery(assignmentId),
        staleTime: 0,
      }),
      loadRoster(context.queryClient),
    ]);
    return { assignment, submissions, qa, students };
  },
  component: lazyRouteComponent(() => import('./routes/review-page')),
  pendingComponent: AssignmentLoading,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});

export const reviewSubmissionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/assignments/$assignmentId/review/$submissionId',
  remountDeps: ({ params }) => params.assignmentId,
  loader: async ({ context, params: { assignmentId, submissionId } }) => {
    const assignment = await requireAssignment(
      context.queryClient,
      assignmentId
    );
    const id = Number(submissionId);
    if (!Number.isInteger(id)) throw notFound();
    const [submissions, qa, answers] = await Promise.all([
      context.queryClient.fetchQuery({
        ...submissionsQuery(assignmentId),
        staleTime: 0,
      }),
      context.queryClient.fetchQuery({
        ...assignmentQAQuery(assignmentId),
        staleTime: 0,
      }),
      context.queryClient.fetchQuery({
        ...submissionAnswersQuery(assignmentId, id),
        staleTime: 0,
      }),
    ]);
    if (!submissions.some((submission) => submission.id === id))
      throw notFound();
    return { assignment, submissions, qa, answers, submissionId: id };
  },
  component: lazyRouteComponent(
    () => import('./routes/review-submission-page')
  ),
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
    if (!workspaceBase || (auth.enabled && auth.role !== 'instructor'))
      throw notFound();
    // A class folder without a roster file has no People page; send an old
    // bookmark back to the assignment.
    const workspace = await context.queryClient.ensureQueryData(workspaceQuery);
    if (workspace.folder_session && !workspace.folder_session.roster)
      throw redirect({
        to: '/assignments/$assignmentId',
        params: { assignmentId: workspace.folder_session.assignment_id },
      });
    return { staffAccess: auth.enabled };
  },
  loader: async ({ context }) => {
    const [students, staff] = await Promise.all([
      context.queryClient.fetchQuery({ ...rosterQuery, staleTime: 0 }),
      context.staffAccess
        ? context.queryClient.fetchQuery({ ...staffQuery, staleTime: 0 })
        : null,
    ]);
    return { students, staff };
  },
  pendingComponent: PeopleLoading,
  component: lazyRouteComponent(() => import('./routes/people-page')),
  errorComponent: RouteError,
});

export const courseSettingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  beforeLoad: async ({ context }) => {
    const auth = await context.queryClient.ensureQueryData(authQuery);
    if (auth.role !== 'instructor') throw notFound();
  },
  loader: ({ context }) =>
    context.queryClient.fetchQuery({ ...workspaceQuery, staleTime: 0 }),
  component: lazyRouteComponent(() => import('./routes/course-settings-page')),
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
      outlineRoute,
      submissionsRoute,
      reviewRoute,
      reviewSubmissionRoute,
      statisticsRoute,
      peopleRoute,
      courseSettingsRoute,
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
