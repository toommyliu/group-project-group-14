export function validateAssignmentSearch(search: Record<string, unknown>) {
  if (typeof search.assignment !== 'string' || !search.assignment.trim()) {
    throw new Error('Choose an assignment from the assignment list.');
  }
  return { assignment: search.assignment };
}
