import type { ComponentProps } from 'react';

export function ScansIcon(props: ComponentProps<'svg'>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <rect x="2" y="3" width="20" height="8" rx="2" />
      <path d="M7 11v9a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2v-9" />
      <path d="M10 15h4" />
      <path d="M10 18h2" />
    </svg>
  );
}
