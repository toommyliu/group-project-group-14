import { errorMessage } from '@/lib/error-message';
import { useEffect, useRef, useState } from 'react';
import { previewAssignmentPdf, savedAssignmentPdf } from '@/api/queries';

type Snapshot = { source: string; version: string };

/** Retain the last PDF, but cancel delivery when its source or version changes. */
export function useAssignmentPdf(
  source: string,
  version: string,
  assignmentId?: string
) {
  const [pdf, setPdf] = useState<Snapshot & { blob: Blob }>();
  const [pending, setPending] = useState<Snapshot>();
  const [failure, setFailure] = useState<Snapshot & { message: string }>();
  const request = useRef<AbortController | null>(null);
  const [download, setDownload] = useState<{ blob: Blob; url: string }>();

  useEffect(() => {
    setPending(undefined);
    return () => {
      request.current?.abort();
      request.current = null;
    };
  }, [source, version, assignmentId]);

  useEffect(() => {
    if (!pdf) return;
    const next = URL.createObjectURL(pdf.blob);
    setDownload({ blob: pdf.blob, url: next });
    return () => URL.revokeObjectURL(next);
  }, [pdf]);

  const matches = (snapshot: Snapshot | undefined) =>
    snapshot?.source === source && snapshot.version === version;

  async function update() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setPending({ source, version });
    setFailure(undefined);
    try {
      const blob = assignmentId
        ? await savedAssignmentPdf(assignmentId, version, controller.signal)
        : await previewAssignmentPdf(source, version, controller.signal);
      if (!controller.signal.aborted) setPdf({ source, version, blob });
    } catch (error) {
      if (!controller.signal.aborted)
        setFailure({
          source,
          version,
          message: errorMessage(
            error,
            'Could not generate the PDF. Try again.'
          ),
        });
    } finally {
      if (request.current === controller) {
        request.current = null;
        setPending(undefined);
      }
    }
  }

  return {
    pdf,
    url: download?.blob === pdf?.blob ? download?.url : undefined,
    update,
    busy: matches(pending),
    current: matches(pdf),
    error: matches(failure) ? failure?.message : undefined,
  };
}
