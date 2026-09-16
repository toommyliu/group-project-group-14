import { errorMessage } from '@/lib/error-message';
import { usePreference } from '@/hooks/use-preference';
import { useEffect, useRef, useState } from 'react';
import { useBlocker } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useGradingHotkeys as useHotkeys } from '@/hooks/use-grading-hotkeys';
import {
  answerQuery,
  saveAnswer,
  submissionsQuery,
  type Answer,
  type GradeEdit,
  type Question,
} from '@/api/queries';
import { rubricScore, selectRubric } from '@/lib/grading';

function draftFor(answer: Answer) {
  return {
    labels: [...answer.rubric_labels],
    analysis: answer.analysis,
    manual: answer.points_awarded !== null && !answer.rubric_labels.length,
    score: answer.points_awarded?.toString() ?? '',
  };
}

type Draft = ReturnType<typeof draftFor>;
function sameDraft(a: Draft, b: Draft) {
  return (
    a.analysis === b.analysis &&
    (a.manual ? a.score : '') === (b.manual ? b.score : '') &&
    JSON.stringify(a.labels) === JSON.stringify(b.labels)
  );
}

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
  const [shortcutsEnabled] = usePreference('gradingShortcuts');
  const client = useQueryClient();
  const [saved, setSaved] = useState(answer);
  const savedRef = useRef(answer);
  const [draft, setDraft] = useState(() => draftFor(answer));
  const draftRef = useRef(draft);
  const [saving, setSaving] = useState(false);
  const flight = useRef<Promise<boolean> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [reloading, setReloading] = useState(false);
  const [reloadOpen, setReloadOpen] = useState(false);
  const feedback = useRef<HTMLTextAreaElement>(null);
  const scoreInput = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState('');
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
    return !sameDraft(draftRef.current, draftFor(savedRef.current));
  }
  function cancelTimer() {
    clearTimeout(timer.current);
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
    if (!hasChanges()) return Promise.resolve(true);
    setSaving(true);
    const request = (async () => {
      let warning: string | null | undefined;
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
        const body: GradeEdit = {
          expected_revision: previous.revision,
          rubric_labels: value.manual ? [] : value.labels,
          analysis: value.analysis,
          score_mode: value.manual ? 'manual' : 'rubric',
          points_awarded:
            value.manual && value.score !== '' ? Number(value.score) : null,
        };
        try {
          const result = await saveAnswer(
            assignmentId,
            answer.submission_id,
            question.id,
            body
          );
          savedRef.current = result.answer;
          setSaved(result.answer);
          // Only replace the draft if nothing was typed while this request was running.
          if (sameDraft(value, draftRef.current))
            replaceDraft({ ...draftFor(result.answer), manual: value.manual });
          cache(result.answer);
          warning = result.warning ?? warning;
        } catch (cause) {
          fail(errorMessage(cause, 'Could not save. Try again.'));
          return false;
        }
      }
      setMessage(warning ?? 'Saved');
      return !warning;
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
      savedRef.current = fresh;
      setSaved(fresh);
      replaceDraft(draftFor(fresh));
      cache(fresh);
      fail('');
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

  useHotkeys(
    [
      ...question.rubric.slice(0, 9).map((_, index) => ({
        hotkey: { key: String(index + 1) },
        callback: () => choose(index),
      })),
      {
        hotkey: 'C',
        callback: () => feedback.current?.focus(),
        // Focusing the textarea makes TanStack ignore the keyup needed to reset.
        options: { requireReset: false },
      },
      {
        hotkey: 'Mod+S',
        callback: () => void persist(),
        options: { ignoreInputs: false },
      },
      {
        hotkey: 'Mod+Enter',
        callback: () => void persist(true),
        options: { ignoreInputs: false },
      },
    ],
    {
      enabled:
        shortcutsEnabled &&
        !busy &&
        !reloadOpen &&
        blocker.status !== 'blocked',
      ignoreInputs: true,
      requireReset: true,
    }
  );

  return {
    saved,
    draft,
    saving,
    feedback,
    scoreInput,
    message,
    error,
    dirty,
    validScore,
    score,
    busy,
    change,
    choose,
    requestReload,
    reload,
    reloadOpen,
    setReloadOpen,
    blocker,
    discardAndLeave,
    retryAndLeave,
  };
}
