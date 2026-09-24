import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Link } from '@tanstack/react-router';
import { Pause, Play } from 'lucide-react';
import { cn } from 'cn';
import {
  AnswerKeyIllustration,
  GradeIllustration,
  ReviewIllustration,
  UploadScansIllustration,
} from '@/components/home-illustrations';
import { AuthControl } from '@/components/auth-control';
import { ModeToggle } from '@/components/mode-toggle';
import { PageHeading } from '@/components/page-heading';
import { Button, buttonVariants } from '@/components/ui/button';

const STEP_DURATION_MS = 6000;

const steps = [
  {
    title: 'Add your answer key',
    description: 'Create an assignment from the questions and answers.',
    Illustration: AnswerKeyIllustration,
  },
  {
    title: 'Upload scans',
    description:
      'Opengrader splits the stack by student and reads each answer.',
    Illustration: UploadScansIllustration,
  },
  {
    title: 'Grade',
    description:
      'Score answers against your rubric. Answers that match the key can be scored for you.',
    Illustration: GradeIllustration,
  },
  {
    title: 'Review',
    description: 'See how the class did on each question.',
    Illustration: ReviewIllustration,
  },
];

export default function HomePage() {
  const [active, setActive] = useState(0);
  const [playing, setPlaying] = useState(
    () => !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
  const elapsedRef = useRef(0);
  const progressRef = useRef<HTMLSpanElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    if (!playing) return;
    elapsedRef.current = 0;
    let last: number | undefined;
    let frame = requestAnimationFrame(function tick(now) {
      // Cap each frame so time spent in a background tab doesn't skip steps.
      elapsedRef.current += Math.min(now - (last ?? now), 100);
      last = now;
      if (elapsedRef.current >= STEP_DURATION_MS) {
        elapsedRef.current = 0;
        setActive((index) => (index + 1) % steps.length);
      }
      progressRef.current?.style.setProperty(
        'scale',
        `1 ${elapsedRef.current / STEP_DURATION_MS}`
      );
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  function select(index: number) {
    elapsedRef.current = 0;
    setActive(index);
  }

  function handleTabKeyDown(event: KeyboardEvent) {
    const offsets: Record<string, number> = { ArrowDown: 1, ArrowUp: -1 };
    let next: number;
    if (event.key in offsets) {
      next = (active + offsets[event.key] + steps.length) % steps.length;
    } else if (event.key === 'Home') {
      next = 0;
    } else if (event.key === 'End') {
      next = steps.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    select(next);
    tabRefs.current[next]?.focus();
  }

  const { Illustration } = steps[active];

  return (
    // Fill the viewport below the content padding (6rem).
    <div className="flex min-h-[calc(100svh-6rem)] flex-col items-start">
      <PageHeading
        className="mb-6 w-full"
        title="Opengrader"
        description="Grade paper assignments from a stack of scans, with no friction."
        actions={
          <div className="flex items-center gap-2">
            <AuthControl />
            <ModeToggle />
          </div>
        }
      />
      <Link to="/workspace" className={buttonVariants({ size: 'lg' })}>
        Start grading
      </Link>
      <section
        aria-label="How it works"
        className="mt-12 grid w-full flex-1 grid-rows-[auto_1fr] gap-8 border-t pt-8 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:grid-rows-1 lg:gap-12"
      >
        <div className="flex flex-col items-start justify-center gap-6">
          <div
            role="tablist"
            aria-orientation="vertical"
            aria-label="Steps"
            className="flex w-full flex-col"
          >
            {steps.map((step, index) => {
              const selected = index === active;
              return (
                <button
                  key={step.title}
                  ref={(node) => {
                    tabRefs.current[index] = node;
                  }}
                  id={`home-step-${index}`}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls="home-step-panel"
                  tabIndex={selected ? 0 : -1}
                  onClick={() => select(index)}
                  onKeyDown={handleTabKeyDown}
                  className={cn(
                    'relative border-s-2 py-6 ps-5 text-start',
                    !selected && 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  {selected && (
                    <span
                      ref={progressRef}
                      aria-hidden="true"
                      className="absolute inset-y-0 -start-0.5 w-0.5 origin-top bg-foreground"
                      style={{ scale: playing ? '1 0' : '1 1' }}
                    />
                  )}
                  <span className="flex gap-2 font-medium">
                    <span className="text-muted-foreground tabular-nums">
                      {index + 1}
                    </span>
                    {step.title}
                  </span>
                  <span className="mt-1 block ps-4 text-muted-foreground">
                    {step.description}
                  </span>
                </button>
              );
            })}
          </div>
          <Button
            variant="outline"
            size="icon"
            aria-label={playing ? 'Pause steps' : 'Play steps'}
            onClick={() => setPlaying((value) => !value)}
          >
            {playing ? <Pause /> : <Play />}
          </Button>
        </div>
        <div
          id="home-step-panel"
          role="tabpanel"
          aria-labelledby={`home-step-${active}`}
          className="flex items-center justify-center border bg-muted/40 p-6 sm:p-10 lg:min-h-96"
        >
          <div
            key={active}
            className="flex w-full animate-in justify-center duration-200 fade-in-0 motion-reduce:animate-none"
          >
            <Illustration className="w-full max-w-2xl" />
          </div>
        </div>
      </section>
    </div>
  );
}
