// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { RubricEditSheet } from './rubric-edit-sheet';
import { saveRubric } from '@/api/queries';
import { ApiError } from '@/api/client';

const oldRubric = [{ label: 'Original', points_delta: 0, default_comment: '' }];
const newRubric = [
  { label: 'My draft', points_delta: -1, default_comment: '' },
];
vi.mock('@/api/queries', () => ({
  assignmentQAQuery: () => ({ queryKey: ['qa'], queryFn: async () => qa() }),
  answerFieldsQuery: () => ({ queryKey: ['fields'], queryFn: async () => [] }),
  saveRubric: vi.fn(async () => ({ problems: [] })),
}));
vi.mock('@/hooks/use-fresh-since-open', () => ({
  useFreshSinceOpen: () => true,
}));
vi.mock('./regrade-note', () => ({ RegradeNote: () => null }));
vi.mock('./ui/sheet', () => {
  const Box = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  return {
    Sheet: Box,
    SheetContent: Box,
    SheetHeader: Box,
    SheetTitle: Box,
    SheetDescription: Box,
    SheetFooter: Box,
  };
});
vi.mock('./rubric-editor', () => ({
  RubricEditor: ({
    rubric,
    onChange,
  }: {
    rubric: typeof oldRubric;
    onChange: (value: typeof oldRubric) => void;
  }) => <button onClick={() => onChange(newRubric)}>{rubric[0]?.label}</button>,
}));

let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
function qa(rubric = oldRubric) {
  return { document: { questions: [{ id: 'q1', points: 5, rubric }] } };
}
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.mocked(saveRubric).mockClear();
  container = document.createElement('div');
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['qa'], qa());
  client.setQueryData(['fields'], []);
  await act(async () =>
    root.render(
      <QueryClientProvider client={client}>
        <RubricEditSheet
          assignmentId="assignment"
          target={{ questionId: 'q1', version: null }}
          onClose={vi.fn()}
        />
      </QueryClientProvider>
    )
  );
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
});
afterEach(async () => {
  await act(async () => root.unmount());
  client.clear();
  vi.unstubAllGlobals();
});

it('retains a rubric draft and its original conflict baseline after a background refresh', async () => {
  await act(async () => {
    container.querySelector('button')!.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  await act(async () => {
    const updatedAt = Date.now() + 1000;
    client.setQueryData(
      ['qa'],
      qa([{ ...oldRubric[0]!, label: 'Another grader' }]),
      { updatedAt }
    );
    client.setQueryData(['fields'], [], { updatedAt });
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  expect(container.textContent).toContain('My draft');
  const save = [...container.querySelectorAll('button')].find(
    (button) => button.textContent === 'Save'
  )!;
  await act(async () => save.click());
  expect(saveRubric).toHaveBeenCalledWith('assignment', 'q1', {
    version: null,
    rubric: newRubric,
    expected_rubric: oldRubric,
  });
});

it('replaces the draft only when the grader explicitly reloads after a conflict', async () => {
  vi.mocked(saveRubric).mockRejectedValueOnce(
    new ApiError(409, { detail: 'Changed' })
  );
  await act(async () => {
    container.querySelector('button')!.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  const save = [...container.querySelectorAll('button')].find(
    (button) => button.textContent === 'Save'
  )!;
  await act(async () => save.click());
  expect(container.textContent).toContain('My draft');
  const reload = [...container.querySelectorAll('button')].find(
    (button) => button.textContent === 'Load the current question'
  )!;
  await act(async () => reload.click());
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  expect(container.textContent).not.toContain('My draft');
  expect(container.textContent).toContain('Original');
});
