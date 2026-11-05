/** Chart arithmetic, kept free of the DOM so it can be tested directly. */

/** The smallest "clean" number (1, 2 or 5 × 10^n) at or above ``value``. */
export function niceCeiling(value: number): number {
  if (value <= 0) {
    return 1;
  }
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 5, 10]) {
    if (value <= step * magnitude) {
      return step * magnitude;
    }
  }
  return 10 * magnitude;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Tick spacing that gives a readable number of labels for a span. */
export function tickInterval(spanMs: number): number {
  if (spanMs <= 2 * HOUR) {
    return 10 * MINUTE;
  }
  if (spanMs <= 12 * HOUR) {
    return HOUR;
  }
  if (spanMs <= 2 * DAY) {
    return 4 * HOUR;
  }
  return DAY;
}

/** Evenly spaced moments on round times inside the span. */
export function timeTicks(startMs: number, endMs: number): number[] {
  const interval = tickInterval(endMs - startMs);
  const ticks: number[] = [];
  for (let tick = Math.ceil(startMs / interval) * interval; tick <= endMs; tick += interval) {
    ticks.push(tick);
  }
  return ticks;
}

/** An SVG path through the values, broken at gaps: a missing observation is never interpolated. */
export function linePath(points: readonly { x: number; y: number | null }[]): string {
  let path = '';
  let drawing = false;
  for (const point of points) {
    if (point.y === null) {
      drawing = false;
      continue;
    }
    const position = `${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
    path += drawing ? ` L${position}` : `${path ? ' ' : ''}M${position}`;
    drawing = true;
  }
  return path;
}

/** Points that have no neighbour on either side draw nothing as a line, so they get a dot. */
export function isolatedPoints<T extends { y: number | null }>(points: readonly T[]): T[] {
  return points.filter(
    (point, index) =>
      point.y !== null &&
      (points[index - 1]?.y ?? null) === null &&
      (points[index + 1]?.y ?? null) === null,
  );
}

/** The index of the bucket containing a moment, clamped to the range. */
export function bucketIndexAt(
  ms: number,
  startMs: number,
  bucketMs: number,
  count: number,
): number {
  return Math.max(0, Math.min(count - 1, Math.floor((ms - startMs) / bucketMs)));
}
