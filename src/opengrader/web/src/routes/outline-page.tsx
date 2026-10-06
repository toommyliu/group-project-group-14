import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { outlineQuery } from '@/api/outline';
import { outlineRoute } from '@/router';
import { OutlineEditor } from '@/components/outline-editor';
import { OutlineVersions } from '@/components/outline-versions';
import { PageHeading } from '@/components/page-heading';

export default function OutlinePage() {
  const { assignment, outline } = outlineRoute.useLoaderData();
  const { version } = outlineRoute.useSearch();
  const navigate = outlineRoute.useNavigate();
  const [dirty, setDirty] = useState(false);
  const [changingVersions, setChangingVersions] = useState(false);
  const query = useQuery({
    ...outlineQuery(assignment.id, version),
    initialData: outline,
  });
  return (
    <>
      <PageHeading
        className="mb-4"
        title="Outline"
        actions={
          <OutlineVersions
            assignmentId={assignment.id}
            outline={query.data}
            dirty={dirty}
            onVersion={(next) => navigate({ search: { version: next } })}
            onBusyChange={setChangingVersions}
          />
        }
        description={
          query.data.lock && query.data.template
            ? query.data.lock.reason
            : query.data.template
              ? "Draw a box around each question. Graders see that part of every student's paper."
              : 'Start from the PDF students write on.'
        }
      />
      <OutlineEditor
        key={`${assignment.id}:${version ?? ''}`}
        assignmentId={assignment.id}
        outline={query.data}
        settled={!query.isFetching && !query.isError}
        paused={changingVersions}
        onDirtyChange={setDirty}
      />
    </>
  );
}
