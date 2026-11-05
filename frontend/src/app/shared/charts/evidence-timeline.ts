import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  afterNextRender,
  computed,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

import { formatAxisTime, formatMs, formatTimestamp, shortSha } from '../../core/format';
import { Bucket, Deployment, Incident, Metrics, RequestMetrics } from '../../core/models';
import { ClockService } from '../../core/preferences.service';
import { bucketIndexAt, isolatedPoints, linePath, niceCeiling, timeTicks } from './scale';

const MARGIN_LEFT = 58;
const MARGIN_RIGHT = 14;
// Small enough to fit a phone: the newest data is at the right and must not start off-screen.
const MIN_WIDTH = 300;
const MIN_TICK_SPACING = 56;

const STATE_Y = 4;
const STATE_HEIGHT = 12;
const DEPLOY_Y = 22;
const DEPLOY_HEIGHT = 18;
const LATENCY_Y = 52;
const LATENCY_HEIGHT = 110;
const CHECKS_Y = LATENCY_Y + LATENCY_HEIGHT + 8;
const CHECKS_HEIGHT = 16;
const ERRORS_Y = CHECKS_Y + CHECKS_HEIGHT + 10;
const ERRORS_HEIGHT = 44;

export interface TimeSelection {
  from: string;
  to: string;
}

interface DeploymentMark {
  id: number;
  x: number;
  label: string;
  tone: string;
  shape: 'circle' | 'square' | 'triangle' | 'hollow';
  title: string;
}

/**
 * One time axis with everything that happened on it: incidents, deployments, response times, check
 * results and request errors. Gaps stay gaps: a period with no checks draws nothing, never a line
 * across it. docs/DESIGN.md section 6.
 */
@Component({
  selector: 'co-evidence-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './evidence-timeline.html',
  styleUrl: './evidence-timeline.css',
})
export class EvidenceTimeline {
  private readonly clock = inject(ClockService);
  private readonly destroyRef = inject(DestroyRef);

  readonly metrics = input.required<Metrics>();
  readonly requests = input<RequestMetrics | null>(null);
  readonly incidents = input<readonly Incident[]>([]);
  readonly deployments = input<readonly Deployment[]>([]);
  readonly selected = output<TimeSelection>();

  private readonly host = viewChild.required<ElementRef<HTMLElement>>('host');
  private readonly measuredWidth = signal(900);

  protected readonly showTable = signal(false);
  protected readonly activeIndex = signal<number | null>(null);

  protected readonly width = computed(() => Math.max(MIN_WIDTH, this.measuredWidth()));
  protected readonly plotRight = computed(() => this.width() - MARGIN_RIGHT);
  protected readonly left = MARGIN_LEFT;
  protected readonly stateY = STATE_Y;
  protected readonly stateHeight = STATE_HEIGHT;
  protected readonly deployY = DEPLOY_Y;
  protected readonly latencyY = LATENCY_Y;
  protected readonly latencyHeight = LATENCY_HEIGHT;
  protected readonly checksY = CHECKS_Y;
  protected readonly checksHeight = CHECKS_HEIGHT;
  protected readonly errorsY = ERRORS_Y;
  protected readonly errorsHeight = ERRORS_HEIGHT;

  protected readonly hasRequests = computed(() => this.requests()?.has_data === true);
  protected readonly axisY = computed(() =>
    this.hasRequests() ? ERRORS_Y + ERRORS_HEIGHT + 4 : CHECKS_Y + CHECKS_HEIGHT + 4,
  );
  protected readonly height = computed(() => this.axisY() + 18);

  private readonly startMs = computed(() => new Date(this.metrics().window_start).getTime());
  private readonly endMs = computed(() => new Date(this.metrics().window_end).getTime());
  private readonly bucketMs = computed(() => this.metrics().bucket_seconds * 1000);

  private x(ms: number): number {
    const span = this.endMs() - this.startMs();
    const plot = this.plotRight() - MARGIN_LEFT;
    return MARGIN_LEFT + ((ms - this.startMs()) / span) * plot;
  }

  private center(bucket: Bucket): number {
    return this.x(new Date(bucket.start).getTime() + this.bucketMs() / 2);
  }

  protected readonly cellWidth = computed(() => {
    const span = this.endMs() - this.startMs();
    const plot = this.plotRight() - MARGIN_LEFT;
    return (this.bucketMs() / span) * plot;
  });

  protected readonly latencyCeiling = computed(() => {
    const values = this.metrics()
      .buckets.map((b) => b.p95_ms)
      .filter((value): value is number => value !== null);
    return niceCeiling(Math.max(0, ...values));
  });

  /** A check that never got a response has no response time, so there is no scale to draw. */
  protected readonly hasLatency = computed(() =>
    this.metrics().buckets.some((bucket) => bucket.p95_ms !== null),
  );
  protected readonly latencyNoteY = LATENCY_Y + LATENCY_HEIGHT / 2 + 4;

  protected readonly latencyTicks = computed(() => {
    if (!this.hasLatency()) {
      return [];
    }
    const ceiling = this.latencyCeiling();
    return [0, ceiling / 2, ceiling].map((value) => ({
      y: LATENCY_Y + LATENCY_HEIGHT - (value / ceiling) * LATENCY_HEIGHT,
      label: formatMs(value),
    }));
  });

  private readonly latencyPoints = computed(() => {
    const ceiling = this.latencyCeiling();
    return this.metrics().buckets.map((bucket) => ({
      x: this.center(bucket),
      y:
        bucket.p95_ms === null
          ? null
          : LATENCY_Y + LATENCY_HEIGHT - (bucket.p95_ms / ceiling) * LATENCY_HEIGHT,
    }));
  });

  protected readonly latencyLine = computed(() => linePath(this.latencyPoints()));
  protected readonly latencyDots = computed(() => isolatedPoints(this.latencyPoints()));

  /** Result of the checks in each bucket: taller means worse, so it reads without colour. */
  protected readonly checkCells = computed(() => {
    const width = Math.max(1, this.cellWidth() - 2);
    return this.metrics()
      .buckets.filter((bucket) => bucket.worst !== null)
      .map((bucket) => {
        const share = bucket.worst === 'fail' ? 1 : bucket.worst === 'warn' ? 0.62 : 0.28;
        const height = CHECKS_HEIGHT * share;
        return {
          x: this.x(new Date(bucket.start).getTime()) + 1,
          y: CHECKS_Y + CHECKS_HEIGHT - height,
          width,
          height,
          tone: bucket.worst as string,
        };
      });
  });

  protected readonly errorCeiling = computed(() => {
    const counts = (this.requests()?.buckets ?? []).map((bucket) => bucket.errors);
    return niceCeiling(Math.max(0, ...counts));
  });

  protected readonly errorBars = computed(() => {
    const requests = this.requests();
    if (!requests?.has_data) {
      return [];
    }
    const ceiling = this.errorCeiling();
    const width = Math.min(24, Math.max(1, this.cellWidth() - 2));
    return requests.buckets
      .filter((bucket) => bucket.errors > 0)
      .map((bucket) => {
        const height = Math.max(2, (bucket.errors / ceiling) * ERRORS_HEIGHT);
        return {
          x: this.x(new Date(bucket.start).getTime()) + 1,
          y: ERRORS_Y + ERRORS_HEIGHT - height,
          width,
          height,
        };
      });
  });

  protected readonly incidentSpans = computed(() => {
    const start = this.startMs();
    const end = this.endMs();
    return this.incidents()
      .map((incident) => {
        const opened = new Date(incident.opened_at).getTime();
        const closed = incident.closed_at ? new Date(incident.closed_at).getTime() : end;
        return { incident, opened, closed };
      })
      .filter(({ opened, closed }) => closed >= start && opened <= end)
      .map(({ incident, opened, closed }) => {
        const x = this.x(Math.max(opened, start));
        return {
          id: incident.id,
          x,
          width: Math.max(3, this.x(Math.min(closed, end)) - x),
          tone: incident.worst_state === 'unavailable' ? 'fail' : 'warn',
        };
      });
  });

  protected readonly deploymentMarks = computed<DeploymentMark[]>(() => {
    const start = this.startMs();
    const end = this.endMs();
    const marks: DeploymentMark[] = [];
    for (const deployment of this.deployments()) {
      const moment = deployment.completed_at ?? deployment.started_at;
      if (!moment) {
        continue;
      }
      const ms = new Date(moment).getTime();
      if (ms < start || ms > end) {
        continue;
      }
      const look = this.deploymentLook(deployment);
      marks.push({
        id: deployment.id,
        x: this.x(ms),
        label: deployment.run_number
          ? `#${deployment.run_number}`
          : shortSha(deployment.commit_sha),
        tone: look.tone,
        shape: look.shape,
        title: `${deployment.run_number ? `#${deployment.run_number} ` : ''}${shortSha(
          deployment.commit_sha,
        )}, ${deployment.outcome.replace('_', ' ')}`,
      });
    }
    return marks;
  });

  /** Labels are selective: past a handful they collide, and the tooltip and tab carry them. */
  protected readonly labelDeployments = computed(() => this.deploymentMarks().length <= 8);

  protected readonly axisTicks = computed(() => {
    const longRange = this.endMs() - this.startMs() > 26 * 3_600_000;
    const ticks = timeTicks(this.startMs(), this.endMs());
    const fits = Math.max(1, Math.floor((this.plotRight() - MARGIN_LEFT) / MIN_TICK_SPACING));
    const step = Math.ceil(ticks.length / fits);
    return ticks
      .filter((_, index) => index % step === 0)
      .map((tick) => ({
        x: this.x(tick),
        label: formatAxisTime(tick, this.clock.utc(), longRange),
      }));
  });

  protected readonly summary = computed(() => {
    const metrics = this.metrics();
    const problems = metrics.buckets.filter((b) => b.worst === 'warn' || b.worst === 'fail').length;
    const empty = metrics.buckets.filter((b) => b.worst === null).length;
    return (
      `Evidence timeline, ${metrics.window}. p95 response time ${formatMs(metrics.p95_ms)}. ` +
      `${problems} of ${metrics.buckets.length} periods had failing checks, ${empty} had no checks. ` +
      `${this.incidentSpans().length} incidents and ${this.deploymentMarks().length} deployments. ` +
      'Use the left and right arrow keys to read each period, Enter to filter the details below.'
    );
  });

  protected readonly tableRows = computed(() => {
    const requests = this.requests();
    return this.metrics()
      .buckets.map((bucket, index) => ({
        bucket,
        errors: requests?.has_data ? (requests.buckets[index]?.errors ?? 0) : null,
      }))
      .filter(({ bucket, errors }) => bucket.checks > 0 || (errors ?? 0) > 0)
      .reverse();
  });

  protected readonly active = computed(() => {
    const index = this.activeIndex();
    if (index === null) {
      return null;
    }
    const bucket = this.metrics().buckets[index];
    if (!bucket) {
      return null;
    }
    const startMs = new Date(bucket.start).getTime();
    const endMs = startMs + this.bucketMs();
    const utc = this.clock.utc();
    const deployment = this.deployments().find((d) => {
      const moment = d.completed_at ?? d.started_at;
      const ms = moment ? new Date(moment).getTime() : NaN;
      return ms >= startMs && ms < endMs;
    });
    return {
      x: this.center(bucket),
      left: Math.min(this.x(startMs) + 10, this.width() - 230),
      period: `${formatTimestamp(bucket.start, utc).slice(5, 16)} to ${formatAxisTime(endMs, utc, false)}`,
      bucket,
      errors: this.requests()?.has_data ? (this.requests()?.buckets[index]?.errors ?? 0) : null,
      requests: this.requests()?.has_data ? (this.requests()?.buckets[index]?.requests ?? 0) : null,
      deployment,
    };
  });

  protected readonly formatMs = formatMs;

  constructor() {
    afterNextRender(() => {
      const element = this.host().nativeElement;
      this.measuredWidth.set(element.clientWidth || 900);
      const observer = new ResizeObserver(() => {
        this.measuredWidth.set(element.clientWidth || 900);
      });
      observer.observe(element);
      this.destroyRef.onDestroy(() => observer.disconnect());
    });
  }

  private deploymentLook(deployment: Deployment): {
    tone: string;
    shape: DeploymentMark['shape'];
  } {
    switch (deployment.outcome) {
      case 'succeeded':
        return { tone: 'ok', shape: 'circle' };
      case 'failed':
        return { tone: 'fail', shape: 'square' };
      case 'needs_attention':
        return { tone: 'warn', shape: 'triangle' };
      default:
        return { tone: 'neutral', shape: 'hollow' };
    }
  }

  protected onPointer(event: PointerEvent): void {
    const svg = event.currentTarget as SVGSVGElement;
    const bounds = svg.getBoundingClientRect();
    this.activeIndex.set(this.indexAtX(event.clientX - bounds.left));
  }

  private indexAtX(x: number): number | null {
    if (x < MARGIN_LEFT || x > this.plotRight()) {
      return null;
    }
    const plot = this.plotRight() - MARGIN_LEFT;
    const span = this.endMs() - this.startMs();
    const ms = this.startMs() + ((x - MARGIN_LEFT) / plot) * span;
    return bucketIndexAt(ms, this.startMs(), this.bucketMs(), this.metrics().buckets.length);
  }

  protected onKey(event: KeyboardEvent): void {
    const last = this.metrics().buckets.length - 1;
    const current = this.activeIndex();
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      const step = event.key === 'ArrowLeft' ? -1 : 1;
      const next = current === null ? last : current + step;
      this.activeIndex.set(Math.max(0, Math.min(last, next)));
    } else if (event.key === 'Enter' && current !== null) {
      event.preventDefault();
      this.select(current);
    } else if (event.key === 'Escape') {
      this.activeIndex.set(null);
    }
  }

  protected onClick(): void {
    const index = this.activeIndex();
    if (index !== null) {
      this.select(index);
    }
  }

  private select(index: number): void {
    const startMs = new Date(this.metrics().buckets[index].start).getTime();
    this.selected.emit({
      from: new Date(startMs).toISOString(),
      to: new Date(startMs + this.bucketMs()).toISOString(),
    });
  }
}
