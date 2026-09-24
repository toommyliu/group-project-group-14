import type { ComponentProps } from 'react';

type IllustrationProps = Omit<ComponentProps<'svg'>, 'viewBox'>;

function Canvas({ children, ...props }: IllustrationProps) {
  return (
    <svg
      viewBox="0 0 400 300"
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

function Sparkle({ x, y, size = 8 }: { x: number; y: number; size?: number }) {
  return (
    <path
      className="fill-chart-4"
      d={`M${x} ${y - size}Q${x} ${y} ${x + size} ${y}Q${x} ${y} ${x} ${y + size}Q${x} ${y} ${x - size} ${y}Q${x} ${y} ${x} ${y - size}Z`}
    />
  );
}

function Face({ cx, cy }: { cx: number; cy: number }) {
  return (
    <g className="fill-foreground/70 stroke-foreground/70">
      <circle cx={cx - 4} cy={cy - 2} r={1.5} stroke="none" />
      <circle cx={cx + 4} cy={cy - 2} r={1.5} stroke="none" />
      <path
        d={`M${cx - 4} ${cy + 3}Q${cx} ${cy + 7} ${cx + 4} ${cy + 3}`}
        fill="none"
        strokeWidth={1.5}
      />
    </g>
  );
}

function Sheet(props: ComponentProps<'rect'>) {
  return (
    <rect
      rx={4}
      strokeWidth={2}
      className="fill-popover stroke-border"
      {...props}
    />
  );
}

function Line({
  x,
  y,
  width,
  className = 'fill-foreground/15',
}: {
  x: number;
  y: number;
  width: number;
  className?: string;
}) {
  return (
    <rect x={x} y={y} width={width} height={6} rx={3} className={className} />
  );
}

export function AnswerKeyIllustration(props: IllustrationProps) {
  const rows = [
    { y: 110, width: 64, color: 'chart-2' },
    { y: 150, width: 48, color: 'chart-3' },
    { y: 190, width: 58, color: 'chart-1' },
  ];
  return (
    <Canvas {...props}>
      <Sheet x={125} y={40} width={150} height={220} />
      <Line x={147} y={64} width={72} className="fill-foreground/30" />
      <Line x={147} y={80} width={44} />
      {rows.map((row, index) => (
        <g key={row.y}>
          <circle
            cx={155}
            cy={row.y}
            r={8}
            strokeWidth={2}
            className={
              [
                'fill-chart-2/20 stroke-chart-2',
                'fill-chart-3/20 stroke-chart-3',
                'fill-chart-1/20 stroke-chart-1',
              ][index]
            }
          />
          <Line x={172} y={row.y - 3} width={row.width} />
          <path
            d={`M${244} ${row.y}l5 5l10 -10`}
            strokeWidth={3}
            className="stroke-chart-1"
          />
        </g>
      ))}
      <g transform="rotate(-40 300 230)">
        <path d="M262 223L244 230L262 237Z" className="fill-chart-4/35" />
        <path
          d="M250 227.7L244 230L250 232.3Z"
          className="fill-foreground/70"
        />
        <rect x={262} y={223} width={52} height={14} className="fill-chart-4" />
        <rect
          x={314}
          y={223}
          width={14}
          height={14}
          rx={3}
          className="fill-chart-3/50"
        />
      </g>
      <Sparkle x={100} y={70} />
      <Sparkle x={300} y={62} size={6} />
    </Canvas>
  );
}

export function UploadScansIllustration(props: IllustrationProps) {
  const students = [
    { y: 44, face: 'fill-chart-1/30' },
    { y: 122, face: 'fill-chart-2/30' },
    { y: 200, face: 'fill-chart-3/30' },
  ];
  return (
    <Canvas {...props}>
      <Sheet
        x={48}
        y={76}
        width={104}
        height={140}
        transform="rotate(-8 100 146)"
      />
      <Sheet
        x={48}
        y={76}
        width={104}
        height={140}
        transform="rotate(4 100 146)"
      />
      <Sheet x={48} y={76} width={104} height={140} />
      <Line x={64} y={96} width={52} className="fill-foreground/30" />
      <Line x={64} y={114} width={72} />
      <Line x={64} y={128} width={60} />
      <Line x={64} y={152} width={72} />
      <Line x={64} y={166} width={40} />
      <path
        d="M172 150H222M212 140L222 150L212 160"
        strokeWidth={2.5}
        className="stroke-muted-foreground"
      />
      {students.map((student) => (
        <g key={student.y}>
          <Sheet x={244} y={student.y} width={108} height={56} />
          <circle
            cx={268}
            cy={student.y + 28}
            r={12}
            className={student.face}
          />
          <Face cx={268} cy={student.y + 28} />
          <Line
            x={290}
            y={student.y + 20}
            width={46}
            className="fill-foreground/30"
          />
          <Line x={290} y={student.y + 32} width={32} />
        </g>
      ))}
      <Sparkle x={184} y={98} size={7} />
    </Canvas>
  );
}

export function GradeIllustration(props: IllustrationProps) {
  const rubric = [
    { y: 88, selected: true, width: 34 },
    { y: 132, selected: false, width: 26 },
    { y: 176, selected: false, width: 40 },
  ];
  return (
    <Canvas {...props}>
      <Sheet x={40} y={70} width={200} height={160} />
      <Line x={60} y={90} width={48} className="fill-foreground/30" />
      <g strokeWidth={3} className="stroke-foreground/60">
        <path d="M62 136c7-14 14-14 18 0s11 14 18 0 11-14 18 0 11 14 18 0 11-14 18 0" />
        <path d="M62 176c6-11 12-11 15 0s9 11 15 0 9-11 15 0" />
        <path d="M130 176h14M137 169v14" />
      </g>
      <circle cx={232} cy={76} r={22} className="fill-chart-1" />
      <path d="M222 76l7 7l13 -14" strokeWidth={4} className="stroke-popover" />
      <Sparkle x={266} y={50} />
      <Sparkle x={210} y={40} size={5} />
      {rubric.map((item) => (
        <g key={item.y}>
          <rect
            x={270}
            y={item.y}
            width={96}
            height={32}
            rx={4}
            strokeWidth={2}
            className={
              item.selected
                ? 'fill-chart-1/15 stroke-chart-1'
                : 'fill-popover stroke-border'
            }
          />
          <rect
            x={281}
            y={item.y + 10}
            width={12}
            height={12}
            rx={2}
            strokeWidth={2}
            className={
              item.selected ? 'fill-chart-1 stroke-chart-1' : 'stroke-border'
            }
          />
          {item.selected && (
            <path
              d={`M284 ${item.y + 16}l2 2l4 -4`}
              strokeWidth={2}
              className="stroke-popover"
            />
          )}
          <Line
            x={302}
            y={item.y + 13}
            width={item.width}
            className={item.selected ? 'fill-chart-1/50' : undefined}
          />
        </g>
      ))}
    </Canvas>
  );
}

export function ReviewIllustration(props: IllustrationProps) {
  const bars = [
    { height: 84, className: 'fill-chart-2/60' },
    { height: 132, className: 'fill-chart-3/60' },
    { height: 104, className: 'fill-chart-5/50' },
    { height: 164, className: 'fill-chart-1/70' },
    { height: 64, className: 'fill-chart-4/60' },
  ];
  const baseline = 240;
  return (
    <Canvas {...props}>
      <path
        d={`M60 40V${baseline}H350`}
        strokeWidth={2}
        className="stroke-border"
      />
      <path
        d="M60 140H350"
        strokeWidth={2}
        strokeDasharray="2 8"
        className="stroke-muted-foreground/60"
      />
      {bars.map((bar, index) => (
        <rect
          key={index}
          x={82 + index * 54}
          y={baseline - bar.height}
          width={34}
          height={bar.height}
          rx={3}
          className={bar.className}
        />
      ))}
      <circle cx={261} cy={52} r={14} className="fill-chart-4/40" />
      <Face cx={261} cy={52} />
      <Sparkle x={236} y={36} size={6} />
      <Sparkle x={290} y={70} size={5} />
    </Canvas>
  );
}
