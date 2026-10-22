import type { Submission } from '@/api/queries';

export function submissionIdentity(submission: Submission, anonymous: boolean) {
  const name = anonymous ? '' : submission.student_name.trim();
  return {
    name: name || `Submission ${submission.id}`,
    sid: anonymous ? '' : submission.student_id,
    detail: [
      name && `#${submission.id}`,
      submission.version && `Version ${submission.version}`,
    ]
      .filter(Boolean)
      .join(' · '),
  };
}
