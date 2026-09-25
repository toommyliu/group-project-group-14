import { useQuery } from '@tanstack/react-query';
import { workspaceQuery } from '@/api/workspace';

/** Non-null while the service grades a single class folder opened from classic mode. */
export function useFolderSession() {
  const { data } = useQuery(workspaceQuery);
  return data?.folder_session ?? null;
}
