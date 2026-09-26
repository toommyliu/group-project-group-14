import { errorMessage } from '@/lib/error-message';
import { useEffect, useRef, useState } from 'react';
import { useBlocker } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useGradingHotkeys } from '@/hooks/use-grading-hotkeys';
import { rubricActions } from '@/lib/grading-shortcuts';
import {
  answerQuery,
  saveAnswer,
  submissionsQuery,
  type Answer,
  type GradeEdit,
  type Question,
} from '@/api/queries';
import { rubricScore, selectRubric } from '@/lib/grading';

import {
  GradeDrafts,
  acknowledgeDraft,
  draftFor,
  sameDraft,
  type Draft,
  type Recovery,
} from '@/lib/grade-drafts';
import { ApiError } from '@/api/client';

/** Serialize saves, retaining edits made during a request and flushing before navigation. */
export function useGradeEditor({
  assignmentId,
  question,
  answer,
  navigating,
  onNext,
}: {
  assignmentId: string;
  question: Question;
  answer: Answer;
  navigating: boolean;
  onNext: (() => void) | undefined;
}) {
  const client = useQueryClient();
  const [saved, setSaved] = useState(answer);
  const savedRef = useRef(answer);
  const [draft, setDraft] = useState(() => draftFor(answer));
  const draftRef = useRef(draft);
  const [journal] = useState(
    () =>
      new GradeDrafts(
        answer.workspace_id,
        assignmentId,
        answer.submission_id,
        question.id
      )
  );
  const [recovered, setRecovered] = useState(() => {
    const current = draftFor(answer);
    const records = journal.candidates();
    const empty = records.filter(
      ({ value }) =>
        sameDraft(value.draft, current) ||
        (value.pending === null && sameDraft(value.draft, value.base))
    );
    journal.discard(empty.map(({ key }) => key));
    return records.filter((record) => !empty.includes(record));
  });
  const offered = useRef(recovered.map(({ key }) => key));
  const pending = useRef<GradeEdit | null>(null);
  const [conflict, setConflict] = useState<Answer | null>(null);
  const [saving, setSaving] = useState(false);
  const flight = useRef<Promise<boolean> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [reloading, setReloading] = useState(false);
  const [reloadOpen, setReloadOpen] = useState(false);
  const feedback = useRef<HTMLTextAreaElement>(null);
  const scoreInput = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState('');
  const [folderWarning, setFolderWarning] = useState('');
  const [error, setError] = useState('');
  const errorRef = useRef('');
  const [leaving, setLeaving] = useState(false);
  const dirty = !sameDraft(draft, draftFor(saved));
  const validScore =
    !draft.manual ||
    draft.score === '' ||
    (Number.isFinite(Number(draft.score)) && Number(draft.score) >= 0);
  const score = draft.manual
    ? draft.score === ''
      ? null
      : Number(draft.score)
    : rubricScore(question, draft.labels);
  const busy = navigating || reloading || leaving;

  function fail(text: string) {
    errorRef.current = text;
    setError(text);
  }
  function replaceDraft(value: Draft) {
    draftRef.current = value;
    setDraft(value);
  }
  function hasChanges() {
    return (
      pending.current !== null ||
      !sameDraft(draftRef.current, draftFor(savedRef.current))
    );
  }
  function cancelTimer() {
    clearTimeout(timer.current);
  }

  function storeDraft() {
    journal.save({
      base: draftFor(savedRef.current),
      revision: savedRef.current.revision,
      context: savedRef.current.context_revision,
      draft: draftRef.current,
      pending: pending.current,
    });
  }

  const receivedRevision = useRef(answer.revision);
  useEffect(() => {
    if (receivedRevision.current === answer.revision) return;
    receivedRevision.current = answer.revision;
    if (!flight.current && !hasChanges()) {
      savedRef.current = answer;
      setSaved(answer);
      replaceDraft(draftFor(answer));
    }
  }, [answer]);
  useEffect(() => () => clearTimeout(timer.current), []);

  function cache(result: Answer) {
    const key = answerQuery(
      assignmentId,
      answer.submission_id,
      question.id
    ).queryKey;
    const current = client.getQueryData(key);
    if (current && Number(current.revision) > Number(result.revision)) return;
    client.setQueryData(
      answerQuery(assignmentId, answer.submission_id, question.id).queryKey,
      result
    );
    client.setQueryData(submissionsQuery(assignmentId).queryKey, (items) =>
      items?.map((item) =>
        item.id === answer.submission_id
          ? {
              ...item,
              question_scores: {
                ...item.question_scores,
                [question.id]: result.points_awarded,
              },
            }
          : item
      )
    );
  }

  function flush(): Promise<boolean> {
    cancelTimer();
    if (flight.current) return flight.current;
    if (errorRef.current) return Promise.resolve(false);
    if (!hasChanges()) {
      journal.clear();
      return Promise.resolve(true);
    }
    setSaving(true);
    let lastFolderStatus: 'written' | 'unchanged' | 'failed' | null = null;
    const request = (async () => {
      while (hasChanges()) {
        const value = draftRef.current;
        if (
          value.manual &&
          (scoreInput.current?.validity.badInput ||
            (value.score !== '' &&
              (!Number.isFinite(Number(value.score)) ||
                Number(value.score) < 0)))
        ) {
          fail('Enter a score of zero or more.');
          return false;
        }
        const previous = savedRef.current;
        const body: GradeEdit = pending.current ?? {
          request_id: crypto.randomUUID(),
          expected_context_revision: previous.context_revision,
          expected_revision: previous.revision,
          rubric_labels: value.manual ? [] : value.labels,
          analysis: value.analysis,
          score_mode: value.manual ? 'manual' : 'rubric',
          points_awarded:
            value.manual && value.score !== '' ? Number(value.score) : null,
        };
        try {
          pending.current = body;
          storeDraft();
          const result = await saveAnswer(
            assignmentId,
            answer.submission_id,
            question.id,
            body
          );
          pending.current = null;
          savedRef.current = result.answer;
          setSaved(result.answer);
          replaceDraft(acknowledgeDraft(draftRef.current, body, result.answer));
          cache(result.answer);
          lastFolderStatus = result.folder?.status ?? null;
          setFolderWarning(
            result.folder?.status === 'failed'
              ? result.folder.message || 'Saved here, but not in the file.'
              : ''
          );
          if (hasChanges()) storeDraft();
          else journal.clear();
          if (offered.current.length) {
            journal.discard(offered.current);
            offered.current = [];
            setRecovered([]);
          }
        } catch (cause) {
          if (cause instanceof ApiError && cause.status === 422)
            pending.current = null;
          if (cause instanceof ApiError && cause.status === 409) {
            try {
              const current = await client.fetchQuery({
                ...answerQuery(assignmentId, answer.submission_id, question.id),
                staleTime: 0,
              });
              setConflict(current);
            } catch {
              /* Keep the durable request for a later retry. */
            }
          }
          fail(errorMessage(cause, 'Could not save. Try again.'));
          return false;
        }
      }
      setMessage(lastFolderStatus === 'written' ? 'Saved to file' : 'Saved');
      return true;
    })();
    flight.current = request;
    void request.finally(() => {
      flight.current = null;
      setSaving(false);
    });
    return request;
  }

  function change(next: Draft) {
    replaceDraft(next);
    try {
      storeDraft();
    } catch (cause) {
      fail(errorMessage(cause, 'Could not preserve this draft.'));
      return;
    }
    setMessage('');
    cancelTimer();
    if (errorRef.current === 'Enter a score of zero or more.') fail('');
    if (!errorRef.current) {
      timer.current = setTimeout(() => void flush(), 500);
    }
  }
  function choose(index: number) {
    change({
      ...draftRef.current,
      manual: false,
      labels: selectRubric(question, draftRef.current.labels, index),
    });
  }
  function clearScore() {
    change({ ...draftRef.current, labels: [], manual: false, score: '' });
  }

  const blocker = useBlocker({
    withResolver: true,
    shouldBlockFn: async () => {
      if (!hasChanges() && !flight.current) return false;
      setLeaving(true);
      const ok = await flush();
      setLeaving(false);
      return !ok && hasChanges();
    },
    enableBeforeUnload: () => hasChanges() || flight.current !== null,
  });

  async function persist(advance = false) {
    if (leaving || reloading) return;
    fail('');
    if (advance) setLeaving(true);
    const ok = await flush();
    setLeaving(false);
    if (advance && ok) onNext?.();
  }

  async function reload() {
    cancelTimer();
    setReloadOpen(false);
    setReloading(true);
    try {
      const fresh = await client.fetchQuery({
        ...answerQuery(assignmentId, answer.submission_id, question.id),
        staleTime: 0,
      });
      pending.current = null;
      journal.clear();
      savedRef.current = fresh;
      setSaved(fresh);
      replaceDraft(draftFor(fresh));
      cache(fresh);
      fail('');
      setFolderWarning('');
      setMessage('Saved answer reloaded.');
    } catch (cause) {
      fail(errorMessage(cause, 'Could not reload the answer. Try again.'));
    } finally {
      setReloading(false);
    }
  }

  function requestReload() {
    cancelTimer();
    if (hasChanges()) setReloadOpen(true);
    else void reload();
  }
  function discardAndLeave() {
    cancelTimer();
    pending.current = null;
    journal.clear();
    replaceDraft(draftFor(savedRef.current));
    fail('');
    blocker.proceed?.();
  }
  async function retryAndLeave() {
    fail('');
    setLeaving(true);
    if (await flush()) blocker.proceed?.();
    setLeaving(false);
  }

  function recover(value: Recovery) {
    cancelTimer();
    const base: Answer = {
      ...answer,
      revision: value.revision,
      context_revision: value.context,
      score_mode: value.base.manual ? 'manual' : 'rubric',
      rubric_labels: value.base.labels,
      analysis: value.base.analysis,
      points_awarded: value.base.score === '' ? null : Number(value.base.score),
    };
    savedRef.current = base;
    setSaved(base);
    replaceDraft(value.draft);
    pending.current = value.pending;
    setRecovered([]);
    try {
      storeDraft();
    } catch (cause) {
      fail(errorMessage(cause, 'Could not preserve this draft.'));
      return;
    }
    setFolderWarning('');
    fail('Recovered your draft. Review it, then retry saving.');
  }

  async function resolveConflict(keepDraft: boolean) {
    if (!conflict) return;
    savedRef.current = conflict;
    setSaved(conflict);
    pending.current = null;
    if (!keepDraft) replaceDraft(draftFor(conflict));
    try {
      storeDraft();
    } catch (cause) {
      fail(errorMessage(cause, 'Could not preserve this draft.'));
      return;
    }
    setConflict(null);
    fail('');
    setFolderWarning('');
    if (keepDraft) await flush();
    else {
      journal.clear();
      cache(savedRef.current);
    }
  }

  useGradingHotkeys(
    [
      ...rubricActions
        .slice(0, question.rubric.length)
        .map((action, index) => ({
          action,
          callback: () => choose(index),
        })),
      {
        action: 'focusScore',
        callback: () => scoreInput.current?.focus(),
        options: { requireReset: false },
      },
      {
        action: 'clearGrade',
        callback: clearScore,
      },
      {
        action: 'focusFeedback',
        callback: () => feedback.current?.focus(),
        // Focusing the textarea makes TanStack ignore the keyup needed to reset.
        options: { requireReset: false },
      },
      {
        action: 'save',
        callback: () => void persist(),
      },
      {
        action: 'saveAndNext',
        callback: () => void persist(true),
      },
    ],
    {
      enabled:
        !busy &&
        !reloadOpen &&
        conflict === null &&
        blocker.status !== 'blocked',
      ignoreInputs: true,
      requireReset: true,
    }
  );

  return {
    saved,
    draft,
    recovered,
    recover,
    conflict,
    resolveConflict,
    dismissConflict: () => setConflict(null),
    persist,
    saving,
    feedback,
    scoreInput,
    message,
    folderWarning,
    error,
    dirty,
    validScore,
    score,
    busy,
    change,
    choose,
    clearScore,
    requestReload,
    reload,
    reloadOpen,
    setReloadOpen,
    blocker,
    discardAndLeave,
    retryAndLeave,
  };
}
