import {
  startTransition,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { useBlocker, useRouter } from '@tanstack/react-router';
import {
  assignmentsQuery,
  createAssignment,
  previewAssignment,
  updateAssignment,
  type Assignment,
  type QAPreview,
  type VisualEditResult,
} from '@/api/queries';
import { ApiError } from '@/api/client';
import { schedulePreview } from '@/lib/debounced-preview';
import { usePreferences } from '@/stores/preferences';
import { errorMessage } from '@/lib/error-message';
import {
  validationSourceLine,
  type ValidationLocation,
} from '@/lib/validation-source-line';

type PreviewResult = { source: string } & (
  | { data: QAPreview; error?: never; location?: never }
  | { error: string; location?: ValidationLocation | null; data?: never }
);

export type EditorMode = 'markdown' | 'visual';

export type AssignmentDraftOptions =
  | { assignmentId?: never; initialSource?: never; overview?: never }
  | {
      assignmentId: string;
      initialSource: string;
      overview: {
        assignment: Assignment;
        qa: QAPreview;
        editUnavailableReason?: string;
        /** Rubrics can be edited question by question while the rest is read-only. */
        rubricEditable?: boolean;
        hideDelete?: boolean;
        structureLocked?: boolean;
      };
    };

export function useAssignmentDraft({
  assignmentId,
  initialSource = '',
  overview,
}: AssignmentDraftOptions) {
  const router = useRouter();
  const [originalSource, setOriginalSource] = useState(initialSource);
  const [source, setSource] = useState(initialSource);
  const [result, setResult] = useState<PreviewResult | undefined>(() =>
    overview ? { source: initialSource, data: overview.qa } : undefined
  );
  const [outline, setOutline] = useState<QAPreview | undefined>(overview?.qa);
  const [visualBaseline, setVisualBaseline] = useState<
    { source: string; preview: QAPreview } | undefined
  >(() =>
    overview ? { source: initialSource, preview: overview.qa } : undefined
  );
  const [retry, setRetry] = useState(0);
  const [mode, setMode] = useState<EditorMode>(
    () => usePreferences.getState().editorMode
  );
  const [openedModes, setOpenedModes] = useState(() => ({
    markdown: mode === 'markdown',
    visual: mode === 'visual',
  }));
  const [visualPending, setVisualPending] = useState(false);
  const [visualChecking, setVisualChecking] = useState(false);
  const [visualFailure, setVisualFailure] = useState('');
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [reading, setReading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const fileRead = useRef(0);
  const [error, setError] = useState('');
  const [saveMessage, setSaveMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<Assignment>();
  const savingRef = useRef(false);
  const leaveAllowed = useRef(false);

  const readOnly = !!overview?.editUnavailableReason;
  const current = result?.source === source;
  const valid = current && !!result.data && !!source.trim() && !visualPending;
  const latestResult = source.trim() ? result : undefined;
  const checking = visualChecking || (!!source.trim() && !current);
  const markdownErrorLine = visualFailure
    ? null
    : validationSourceLine(
        latestResult?.error,
        latestResult?.error ? latestResult.location : null
      );
  const dirty =
    !saved && (source !== originalSource || reading || visualPending);
  const busy = reading || saving || !!saved;

  // Follow external saves only when there is no local work to overwrite.
  // Keep the old base for a dirty draft so optimistic concurrency still
  // prevents replacing another editor's work without review.
  const [receivedSource, setReceivedSource] = useState(initialSource);
  if (
    overview &&
    initialSource !== receivedSource &&
    !reading &&
    !saving &&
    !visualPending &&
    (readOnly || source === originalSource)
  ) {
    setReceivedSource(initialSource);
    setOriginalSource(initialSource);
    setSource(initialSource);
    setResult({ source: initialSource, data: overview.qa });
    setOutline(overview.qa);
    setVisualBaseline({ source: initialSource, preview: overview.qa });
  }

  const openMode = useCallback((nextMode: EditorMode) => {
    setOpenedModes((opened) =>
      opened[nextMode] ? opened : { ...opened, [nextMode]: true }
    );
    setMode(nextMode);
  }, []);
  const markVisualPending = useCallback(() => {
    setVisualPending(true);
    setVisualChecking(true);
  }, []);
  const failVisualEdit = useCallback((message: string) => {
    setVisualFailure(message);
    setVisualChecking(false);
  }, []);

  function edit(value: string) {
    setSource(value);
    setError('');
    setSaveMessage('');
  }

  const acceptVisualEdit = useCallback((change: VisualEditResult) => {
    setSaveMessage('');
    setSource(change.qa_markdown);
    if (change.preview) setOutline(change.preview);
    if (change.validation_error) {
      setResult({
        source: change.qa_markdown,
        error: change.validation_error,
        location: change.validation_location,
      });
    } else if (change.preview) {
      setResult({ source: change.qa_markdown, data: change.preview });
    } else {
      setResult({
        source: change.qa_markdown,
        error: 'Check the rubric values.',
      });
    }
    setVisualPending(false);
    setVisualChecking(false);
    setVisualFailure('');
  }, []);

  function retryValidation() {
    setResult(undefined);
    setRetry((value) => value + 1);
  }

  useBlocker({
    shouldBlockFn: () => {
      if (leaveAllowed.current) return false;
      if (savingRef.current) return true;
      if (!dirty) return false;
      if (!window.confirm('Discard unsaved changes?')) return true;
      if (overview) {
        setSource(originalSource);
        setResult(undefined);
        setOutline(undefined);
        openMode('markdown');
        setVisualPending(false);
        setVisualChecking(false);
        setVisualFailure('');
        setUploadedFileName('');
        setError('');
        setUploadError('');
      }
      return false;
    },
    enableBeforeUnload: () =>
      !leaveAllowed.current && (dirty || savingRef.current),
  });

  useEffect(() => {
    if (!source.trim() || current) return;
    return schedulePreview(
      (signal) => previewAssignment(source, signal),
      (response) => {
        if ('data' in response) {
          setOutline(response.data);
          startTransition(() => {
            setVisualBaseline({ source, preview: response.data });
          });
        } else if (outline) {
          startTransition(() => {
            setVisualBaseline({ source, preview: outline });
          });
        }
        setResult(
          'data' in response
            ? { source, data: response.data }
            : {
                source,
                error: errorMessage(response.error),
                location:
                  response.error instanceof ApiError
                    ? response.error.location
                    : null,
              }
        );
      },
      400
    );
  }, [source, retry, current, outline]);

  useEffect(
    () => () => {
      fileRead.current += 1;
    },
    []
  );

  async function upload(file: File | undefined) {
    if (!file) return;
    const id = ++fileRead.current;
    setReading(true);
    setUploadError('');
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(
        await file.arrayBuffer()
      );
      if (id === fileRead.current) {
        setResult(undefined);
        setOutline(undefined);
        setVisualBaseline(undefined);
        openMode('markdown');
        setSource(text);
        setUploadedFileName(file.name);
        setRetry((value) => value + 1);
      }
    } catch {
      if (id === fileRead.current)
        setUploadError(
          'Could not read this file. Choose a Markdown file or paste its content into the editor.'
        );
    } finally {
      if (id === fileRead.current) setReading(false);
    }
  }

  async function save() {
    if (
      !valid ||
      readOnly ||
      (overview && !dirty) ||
      reading ||
      savingRef.current ||
      leaveAllowed.current
    )
      return;
    savingRef.current = true;
    setSaving(true);
    setError('');
    setSaveMessage('');
    try {
      let assignment: Assignment;
      try {
        assignment = assignmentId
          ? await updateAssignment(assignmentId, source, originalSource)
          : await createAssignment(source);
      } catch (cause) {
        setError(errorMessage(cause));
        return;
      }
      leaveAllowed.current = true;
      if (overview) setOriginalSource(source);
      else setSaved(assignment);
      try {
        const queryClient = router.options.context.queryClient;
        await queryClient.cancelQueries({
          queryKey: assignmentsQuery.queryKey,
          exact: true,
        });
        queryClient.setQueryData(assignmentsQuery.queryKey, (previous) => [
          ...(previous ?? []).filter((item) => item.id !== assignment.id),
          assignment,
        ]);
        await queryClient.cancelQueries({
          queryKey: ['assignments', assignment.id],
        });
        queryClient.removeQueries({ queryKey: ['assignments', assignment.id] });
        await queryClient.invalidateQueries({
          queryKey: assignmentsQuery.queryKey,
          exact: true,
        });
        if (overview) {
          await router.invalidate();
          setSaveMessage('Changes saved.');
        } else {
          await router.navigate({
            to: '/assignments/$assignmentId/outline',
            params: { assignmentId: assignment.id },
          });
        }
      } catch {
        setError(
          overview
            ? 'Assignment saved, but the overview could not refresh. Reload this page.'
            : 'Assignment saved, but its page could not open. Use the link below to try again.'
        );
      } finally {
        if (overview) leaveAllowed.current = false;
      }
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  return {
    source,
    originalSource,
    result: current ? result : undefined,
    latestResult,
    checking,
    outline,
    visualBaseline,
    valid,
    dirty,
    readOnly,
    markdownErrorLine,
    visualFailure,
    mode,
    openedModes,
    openMode,
    visualPending,
    markVisualPending,
    acceptVisualEdit,
    failVisualEdit,
    edit,
    retryValidation,
    reading,
    uploadError,
    uploadedFileName,
    upload,
    busy,
    saving,
    saved,
    saveMessage,
    error,
    save,
    allowLeave: () => {
      leaveAllowed.current = true;
    },
  };
}
