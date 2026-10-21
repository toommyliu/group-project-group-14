// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import AssistantPage from './assistant-page';
import { acceptFix } from '@/api/assistant';

const overview = {
  title: 'Quiz 1',
  submission_count: 1,
  unanswered: 0,
  rubric_gaps: 0,
  can_grade: true,
  can_draft_rubrics: false,
  key_file: '/course/quiz1/qa.md',
  output_dir: '/course-ai-assistant',
  copies: [
    {
      name: 'ai-assistant-1',
      path: '/course-ai-assistant/ai-assistant-1',
      purpose: 'grading',
      students: 1,
      ungraded: 1,
      graded_by_agent: 0,
      open_suggestions: 1,
      mcp_setup: 'mcp',
    },
  ],
  key_folders: [],
  prompts: { grading: 'Grade.', rubrics: 'Rubrics.', key: 'Key.' },
};
const suggestion = {
  id: 's1',
  kind: 'answer',
  question: 'q1',
  version: '',
  message: 'Also right',
  fix: 'accept: loops',
  fixable: true,
  decision: '',
};

vi.mock('@/router', () => ({
  assistantRoute: {
    useLoaderData: () => ({
      assignment: { id: 'a1', title: 'Quiz 1' },
      overview,
    }),
  },
}));
vi.mock('@/api/assistant', () => ({
  assistantQuery: () => ({
    queryKey: ['assistant'],
    queryFn: async () => overview,
  }),
  suggestionsQuery: () => ({
    queryKey: ['suggestions'],
    queryFn: async () => [suggestion],
  }),
  mergePreviewQuery: () => ({
    queryKey: ['merge'],
    queryFn: async () => ({ lines: [], grades: 0, requests: 0 }),
  }),
  acceptFix: vi.fn(async () => ({
    lines: ['q1 now accepts loops.'],
    grades: 0,
    requests: 0,
  })),
  dismissFix: vi.fn(),
  makeCopy: vi.fn(),
  mergeCopy: vi.fn(),
  deleteCopy: vi.fn(),
  makeKeyFolder: vi.fn(),
  storeKeyFolder: vi.fn(),
}));

let root: Root;
let container: HTMLDivElement;
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(['suggestions'], [suggestion]);
  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <AssistantPage />
      </QueryClientProvider>
    )
  );
  await act(async () => {});
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function button(label: string) {
  const found = [...container.querySelectorAll('button')].find(
    (item) => item.textContent?.trim() === label
  );
  if (!found) throw new Error(`no ${label} button`);
  return found;
}

it('asks whether qa.md follows before a fix is accepted, and sends the answer', async () => {
  expect(container.textContent).toContain('Also right');
  expect(button('Accept').disabled).toBe(true);

  const workspaceOnly = [...container.querySelectorAll('label')].find((label) =>
    label.textContent?.includes('Workspace only')
  );
  await act(async () => workspaceOnly?.querySelector('input')?.click());
  expect(button('Accept').disabled).toBe(false);
  await act(async () => button('Accept').click());

  expect(acceptFix).toHaveBeenCalledWith('a1', 'ai-assistant-1', 's1', 'leave');
  expect(container.textContent).toContain('q1 now accepts loops.');
});
