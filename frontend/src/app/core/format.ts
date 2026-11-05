/** Display formatting. Pure functions, so they are tested without a browser. */

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** "2026-11-03 14:07:12", in UTC or the browser's time zone. */
export function formatTimestamp(iso: string, utc: boolean): string {
  const date = new Date(iso);
  const [year, month, day, hours, minutes, seconds] = utc
    ? [
        date.getUTCFullYear(),
        date.getUTCMonth() + 1,
        date.getUTCDate(),
        date.getUTCHours(),
        date.getUTCMinutes(),
        date.getUTCSeconds(),
      ]
    : [
        date.getFullYear(),
        date.getMonth() + 1,
        date.getDate(),
        date.getHours(),
        date.getMinutes(),
        date.getSeconds(),
      ];
  return `${year}-${pad(month)}-${pad(day)} ${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

/** "14:07" for chart axes; the date is added for ranges longer than a day. */
export function formatAxisTime(ms: number, utc: boolean, withDate: boolean): string {
  const text = formatTimestamp(new Date(ms).toISOString(), utc);
  return withDate ? `${text.slice(5, 10)} ${text.slice(11, 16)}` : text.slice(11, 16);
}

export function formatDayHeading(iso: string, utc: boolean): string {
  return formatTimestamp(iso, utc).slice(0, 10);
}

/** "45 s", "12 min", "3 h 5 min", "2 d 4 h". */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) {
    return `${seconds} s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  }
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
}

/** "34 s ago", or "in 12 s" for something scheduled. */
export function formatRelative(iso: string, nowMs: number): string {
  const difference = Math.round((nowMs - new Date(iso).getTime()) / 1000);
  if (Math.abs(difference) < 2) {
    return 'just now';
  }
  return difference >= 0
    ? `${formatDuration(difference)} ago`
    : `in ${formatDuration(-difference)}`;
}

/** Truncated rather than rounded: 99.996% must not read as 100%. */
export function formatPercent(fraction: number): string {
  return `${(Math.floor(fraction * 10000) / 100).toFixed(2)}%`;
}

export function formatMs(value: number | null): string {
  if (value === null) {
    return '—';
  }
  return value >= 10_000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`;
}

export function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ['KiB', 'MiB', 'GiB'];
  let size = value;
  let unit = '';
  for (const next of units) {
    size /= 1024;
    unit = next;
    if (size < 1024) {
      break;
    }
  }
  return `${size.toFixed(1)} ${unit}`;
}

export function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

const REASONS: Record<string, string> = {
  slow_response: 'Slow response',
  reported_degraded: 'Reports itself degraded',
  reported_unhealthy: 'Reports itself unhealthy',
  unexpected_redirect: 'Unexpected redirect',
  rate_limited: 'Rate limited',
  client_error: 'Client error (4xx)',
  server_error: 'Server error (5xx)',
  service_unavailable: 'Not serving (502/503/504)',
  unexpected_status: 'Unexpected status',
  timeout: 'Timed out',
  dns_failure: 'DNS failure',
  connection_failed: 'Connection failed',
  tls_error: 'TLS failure',
  protocol_error: 'Invalid HTTP response',
  target_blocked: 'Blocked by target policy',
  monitor_error: 'Monitor error',
};

export function formatReason(reason: string | null): string {
  if (reason === null) {
    return '—';
  }
  return REASONS[reason] ?? reason.replaceAll('_', ' ');
}

/** ISO range covering ``minutes`` either side of a moment, for "logs around this". */
export function aroundMoment(iso: string, minutes = 10): { from: string; to: string } {
  const moment = new Date(iso).getTime();
  const span = minutes * 60_000;
  return {
    from: new Date(moment - span).toISOString(),
    to: new Date(moment + span).toISOString(),
  };
}
