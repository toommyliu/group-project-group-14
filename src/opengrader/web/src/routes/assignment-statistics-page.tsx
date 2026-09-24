import { useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { assignmentStatisticsQuery } from '@/api/queries';
import { statisticsRoute } from '@/router';
import { PageHeading } from '@/components/page-heading';
import { ErrorAlert } from '@/components/error-alert';
import { Button } from '@/components/ui/button';
import { AssignmentStatistics } from '@/components/assignment-statistics';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

export default function AssignmentStatisticsPage() {
  const { assignment, statistics } = statisticsRoute.useLoaderData();
  const query = useQuery({
    ...assignmentStatisticsQuery(assignment.id),
    initialData: statistics,
  });
  const [selectedVersion, setSelectedVersion] = useState('');
  const versionId = useId();
  const reports = query.data;
  const report =
    reports.find((item) => item.version === selectedVersion) ?? reports[0];
  return (
    <>
      <PageHeading
        title="Assignment statistics"
        description={assignment.title}
        actions={
          reports.length > 1 && (
            <div className="flex flex-wrap items-center gap-3">
              <Label htmlFor={versionId}>Version</Label>
              <Select
                value={report.version ?? ''}
                onValueChange={(value) => {
                  if (value !== null) setSelectedVersion(value);
                }}
              >
                <SelectTrigger id={versionId} className="min-w-20">
                  <SelectValue>{report.version}</SelectValue>
                </SelectTrigger>
                <SelectContent align="end">
                  {reports.map((item) => (
                    <SelectItem key={item.version} value={item.version ?? ''}>
                      {item.version}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )
        }
      />
      {query.isError && (
        <ErrorAlert
          className="mb-6"
          title="Statistics could not be refreshed. Showing the last loaded scores."
          error={query.error}
        >
          <Button variant="outline" onClick={() => void query.refetch()}>
            Retry
          </Button>
        </ErrorAlert>
      )}
      <AssignmentStatistics
        key={report.version}
        assignmentId={assignment.id}
        report={report}
      />
    </>
  );
}
