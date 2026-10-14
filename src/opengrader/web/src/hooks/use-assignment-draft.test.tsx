// @vitest-environment happy-dom
import { act, useLayoutEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  useAssignmentDraft,
  type AssignmentDraftOptions,
} from './use-assignment-draft';

vi.mock('@tanstack/react-router', () => ({
  useRouter: () => ({}),
  useBlocker: vi.fn(),
}));
vi.mock('@/api/queries', () => ({
  assignmentsQuery: { queryKey: ['assignments'] },
  createAssignment: vi.fn(),
  updateAssignment: vi.fn(),
  previewAssignment: vi.fn(),
}));
vi.mock('@/lib/debounced-preview', () => ({ schedulePreview: vi.fn() }));

let root: Root;
let container: HTMLDivElement;
let editor: ReturnType<typeof useAssignmentDraft>;
function Screen({ source }: { source: string }) {
  const draft = useAssignmentDraft({
    assignmentId: 'assignment',
    initialSource: source,
    overview: {
      assignment: { id: 'assignment' },
      qa: { document: { title: source } },
    },
  } as AssignmentDraftOptions);
  useLayoutEffect(() => {
    editor = draft;
  });
  return <div>{draft.source}</div>;
}
const render = (source: string) =>
  act(async () => root.render(<Screen source={source} />));

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  container = document.createElement('div');
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  vi.unstubAllGlobals();
});

it('adopts another tab’s save when the editable assignment has no local changes', async () => {
  await render('Original');
  await render('External save');
  expect(editor.source).toBe('External save');
  expect(editor.originalSource).toBe('External save');
  expect(editor.visualBaseline?.source).toBe('External save');
  expect(editor.dirty).toBe(false);
});

it('preserves local changes and their conflict baseline across an external save', async () => {
  await render('Original');
  await act(async () => editor.edit('Local draft'));
  await render('External save');
  expect(editor.source).toBe('Local draft');
  expect(editor.originalSource).toBe('Original');
  expect(editor.dirty).toBe(true);
});

it('does not overwrite a pending visual edit when an external save arrives', async () => {
  await render('Original');
  await act(async () => editor.markVisualPending());
  await render('External save');
  expect(editor.source).toBe('Original');
  expect(editor.visualPending).toBe(true);
});
