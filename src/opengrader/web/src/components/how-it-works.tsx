import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Pause, Play } from 'lucide-react';
import { cn } from 'cn';
import {
  AnswerKeyIllustration,
  GradeIllustration,
  ReviewIllustration,
  UploadScansIllustration,
} from '@/components/home-illustrations';
import { Button } from '@/components/ui/button';
import { useMediaQuery } from '@/hooks/use-media-query';

const STEP_DURATION_MS = 6000;

const steps = [
  {
    title: 'Outline the template',
    description: 'Upload the blank assignment and draw a box around each question.',
    Illustration: AnswerKeyIllustration,
  },
  {
    title: 'Upload scans',
    description:
      'Opengrader splits the stack by student and cuts out each answer.',
    Illustration: UploadScansIllustration,
  },
  {
    title: 'Grade',
    description: 'Score each question across the class against your rubric.',
    Illustration: GradeIllustration,
  },
  {
    title: 'Review',
    description: 'Check every score, then see how the class did.',
    Illustration: ReviewIllustration,
  },
];

export function HowItWorks({ className }: { className?: string }) {
  const [active, setActive] = useState(0);
  const [playing, setPlaying] = useState(
    () => !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
  const elapsedRef = useRef(0);
  const progressRef = useRef<HTMLSpanElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const vertical = useMediaQuery('(min-width: 64rem)');

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
      const progress = elapsedRef.current / STEP_DURATION_MS;
      progressRef.current?.style.setProperty(
        'scale',
        vertical ? `1 ${progress}` : `${progress} 1`
      );
      frame = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(frame);
  }, [playing, vertical]);

  function select(index: number) {
    elapsedRef.current = 0;
    setActive(index);
  }

  function handleTabKeyDown(event: KeyboardEvent) {
    const offsets: Record<string, number> = vertical
      ? { ArrowDown: 1, ArrowUp: -1 }
      : { ArrowRight: 1, ArrowLeft: -1 };
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

  return (
    <section
      aria-label="How it works"
      className={cn(
        'grid w-full gap-8 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:items-center lg:gap-12',
        className
      )}
    >
      <div className="flex flex-col justify-center gap-4 lg:gap-6">
        <div className="flex items-center gap-4 lg:flex-col lg:items-start lg:gap-6">
          <div
            role="tablist"
            aria-orientation={vertical ? 'vertical' : 'horizontal'}
            aria-label="Steps"
            className="flex min-w-0 flex-1 gap-2 lg:w-full lg:flex-col lg:gap-0"
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
                    'relative min-h-11 min-w-0 flex-1 border-t-2 pt-3 text-start lg:flex-none lg:border-s-2 lg:border-t-0 lg:py-6 lg:ps-5',
                    'transition-colors duration-300',
                    !selected && 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  {selected && (
                    <span
                      ref={progressRef}
                      aria-hidden="true"
                      className="absolute inset-x-0 -top-0.5 h-0.5 origin-left bg-foreground lg:inset-x-auto lg:inset-y-0 lg:-start-0.5 lg:h-auto lg:w-0.5 lg:origin-top"
                      style={{
                        scale: playing ? (vertical ? '1 0' : '0 1') : '1 1',
                      }}
                    />
                  )}
                  <span className="flex gap-2 font-medium">
                    <span className="text-muted-foreground tabular-nums">
                      {index + 1}
                    </span>
                    <span className="hidden min-w-0 truncate sm:block lg:whitespace-normal">
                      {step.title}
                    </span>
                    <span className="sr-only sm:hidden">{step.title}</span>
                  </span>
                  <span className="mt-1 hidden ps-4 text-muted-foreground lg:block">
                    {step.description}
                  </span>
                </button>
              );
            })}
          </div>
          <Button
            variant="outline"
            size="icon"
            className="shrink-0"
            aria-label={playing ? 'Pause steps' : 'Play steps'}
            onClick={() => setPlaying((value) => !value)}
          >
            {playing ? <Pause /> : <Play />}
          </Button>
        </div>
        {/* Every caption shares one cell so the height holds steady between steps. */}
        <div className="grid lg:hidden">
          {steps.map((step, index) => (
            <p
              key={step.title}
              className={cn(
                'text-muted-foreground transition-[opacity,visibility] [grid-area:1/1] motion-reduce:transition-none',
                index === active
                  ? 'delay-100 duration-300 ease-out'
                  : 'invisible opacity-0 duration-200 ease-in'
              )}
            >
              <span className="block font-medium text-foreground sm:hidden">
                {step.title}
              </span>
              {step.description}
            </p>
          ))}
        </div>
      </div>
      <div
        id="home-step-panel"
        role="tabpanel"
        aria-labelledby={`home-step-${active}`}
        className="grid place-items-center border bg-muted/40 p-4 sm:p-6"
      >
        {/* Stack every illustration so steps crossfade instead of swapping.
            The outgoing step fades out quickly and the next one fades in just
            behind it, so the two never sit half-visible over each other. */}
        {steps.map(({ title, Illustration }, index) => (
          <Illustration
            key={title}
            className={cn(
              'w-full max-w-xl transition-[opacity,visibility] [grid-area:1/1] motion-reduce:transition-none',
              index === active
                ? 'delay-100 duration-300 ease-out'
                : 'invisible opacity-0 duration-200 ease-in'
            )}
          />
        ))}
      </div>
    </section>
  );
}
