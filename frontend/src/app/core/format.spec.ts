import {
  aroundMoment,
  formatBytes,
  formatDuration,
  formatMs,
  formatPercent,
  formatReason,
  formatRelative,
  formatTimestamp,
  shortSha,
} from './format';
import {
  bucketIndexAt,
  isolatedPoints,
  linePath,
  niceCeiling,
  tickInterval,
  timeTicks,
} from '../shared/charts/scale';

describe('format', () => {
  it('shows timestamps in UTC when asked', () => {
    expect(formatTimestamp('2026-11-03T14:07:09Z', true)).toBe('2026-11-03 14:07:09');
  });

  it.each([
    [45, '45 s'],
    [720, '12 min'],
    [3600, '1 h'],
    [11_100, '3 h 5 min'],
    [172_800, '2 d'],
    [187_200, '2 d 4 h'],
    [-5, '0 s'],
  ])('formats %d seconds as %s', (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });

  it('describes moments relative to now', () => {
    const now = new Date('2026-11-03T14:00:00Z').getTime();
    expect(formatRelative('2026-11-03T13:59:26Z', now)).toBe('34 s ago');
    expect(formatRelative('2026-11-03T14:00:01Z', now)).toBe('just now');
    expect(formatRelative('2026-11-03T14:00:30Z', now)).toBe('in 30 s');
  });

  it('never rounds a percentage up to 100', () => {
    expect(formatPercent(0.99996)).toBe('99.99%');
    expect(formatPercent(1)).toBe('100.00%');
    expect(formatPercent(0.5)).toBe('50.00%');
  });

  it('shows missing values as a dash, not zero', () => {
    expect(formatMs(null)).toBe('—');
    expect(formatMs(0)).toBe('0 ms');
    expect(formatMs(212)).toBe('212 ms');
    expect(formatMs(12_500)).toBe('12.5 s');
    expect(formatReason(null)).toBe('—');
  });

  it('turns reason codes into words, and handles unknown ones', () => {
    expect(formatReason('server_error')).toBe('Server error (5xx)');
    expect(formatReason('something_new')).toBe('something new');
  });

  it('formats sizes and commits', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KiB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MiB');
    expect(shortSha('a1b2c3d4e5f6')).toBe('a1b2c3d');
  });

  it('builds a range around a moment', () => {
    expect(aroundMoment('2026-11-03T14:00:00Z')).toEqual({
      from: '2026-11-03T13:50:00.000Z',
      to: '2026-11-03T14:10:00.000Z',
    });
  });
});

describe('chart scales', () => {
  it.each([
    [0, 1],
    [3, 5],
    [212, 500],
    [1000, 1000],
    [1001, 2000],
    [3278, 5000],
  ])('rounds %d up to the clean number %d', (value, expected) => {
    expect(niceCeiling(value)).toBe(expected);
  });

  it('spaces ticks to suit the range', () => {
    expect(tickInterval(3_600_000)).toBe(600_000);
    expect(tickInterval(6 * 3_600_000)).toBe(3_600_000);
    expect(tickInterval(24 * 3_600_000)).toBe(4 * 3_600_000);
    expect(tickInterval(7 * 86_400_000)).toBe(86_400_000);
  });

  it('places ticks on round times inside the span', () => {
    const start = new Date('2026-11-03T14:03:00Z').getTime();
    const ticks = timeTicks(start, start + 3_600_000);
    expect(new Date(ticks[0]).toISOString()).toBe('2026-11-03T14:10:00.000Z');
    expect(ticks).toHaveLength(6);
  });

  it('breaks the line at a gap instead of drawing across it', () => {
    const path = linePath([
      { x: 0, y: 10 },
      { x: 1, y: 20 },
      { x: 2, y: null },
      { x: 3, y: 30 },
      { x: 4, y: 40 },
    ]);
    expect(path).toBe('M0.0 10.0 L1.0 20.0 M3.0 30.0 L4.0 40.0');
  });

  it('finds points a line cannot show', () => {
    const points = [
      { x: 0, y: 1 },
      { x: 1, y: null },
      { x: 2, y: 2 },
      { x: 3, y: 3 },
    ];
    expect(isolatedPoints(points).map((p) => p.x)).toEqual([0]);
  });

  it('clamps the bucket index to the range', () => {
    expect(bucketIndexAt(-5, 0, 10, 4)).toBe(0);
    expect(bucketIndexAt(25, 0, 10, 4)).toBe(2);
    expect(bucketIndexAt(500, 0, 10, 4)).toBe(3);
  });
});
