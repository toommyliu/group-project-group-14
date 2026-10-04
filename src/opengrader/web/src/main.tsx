import { ApiError, workspaceBase } from '@/api/client';
import { observeWorkspace } from '@/api/events';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import './index.css';
import { RouterProvider } from '@tanstack/react-router';
import { createAppRouter } from './router';
import { syncTheme } from '@/lib/theme';
import { StartupError } from '@/components/startup-error';

function mayPassOnRetry(error: unknown) {
  if (!(error instanceof ApiError)) return true;
  return error.status >= 500 || error.status === 408 || error.status === 429;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      retry: (failures, error) => failures < 1 && mayPassOnRetry(error),
      refetchOnWindowFocus: false,
      networkMode: 'always',
    },
  },
});

const stopTheme = syncTheme();
import.meta.hot?.dispose(stopTheme);

async function start() {
  if (workspaceBase) {
    const stop = await observeWorkspace(queryClient);
    import.meta.hot?.dispose(stop);
  }
  const router = createAppRouter(queryClient);
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>
  );
}

void start().catch((error: unknown) => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <StartupError error={error} />
    </StrictMode>
  );
});
