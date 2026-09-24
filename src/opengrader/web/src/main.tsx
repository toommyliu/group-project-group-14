import { observeWorkspace } from '@/api/events';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import './index.css';
import { RouterProvider } from '@tanstack/react-router';
import { createAppRouter } from './router';
import { syncTheme } from '@/lib/theme';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
      networkMode: 'always',
    },
  },
});

const stopTheme = syncTheme();
import.meta.hot?.dispose(stopTheme);

async function start() {
  const stop = await observeWorkspace(queryClient);
  import.meta.hot?.dispose(stop);
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
  const root = document.getElementById('root');
  if (root)
    root.textContent =
      error instanceof Error
        ? error.message
        : 'Could not connect. Reload this page to retry.';
});
