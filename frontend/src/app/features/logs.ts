import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import { ApiService } from '../core/api.service';
import { formatTimestamp } from '../core/format';
import { Severity, TimeWindow } from '../core/models';
import { pagedPoll } from '../core/paged';
import { poll } from '../core/poll';
import { ClockService, TimeRangeService } from '../core/preferences.service';
import { LogList } from '../shared/records';
import { EmptyState, ErrorPanel, Skeleton } from '../shared/states';

const SEVERITIES: Severity[] = ['debug', 'info', 'warning', 'error', 'critical'];

/**
 * The log explorer. Filters live in the URL, so "logs around this moment" links from anywhere on
 * the site and a filtered view can be shared.
 */
@Component({
  selector: 'co-logs',
  imports: [FormsModule, RouterLink, EmptyState, ErrorPanel, LogList, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <section class="section" aria-label="Log explorer">
        <form class="filters" (ngSubmit)="applyText()">
          <label class="field">
            Application
            <select
              [ngModel]="application() ?? ''"
              name="application"
              (ngModelChange)="update('application', $event)"
            >
              <option value="">All</option>
              @for (app of applicationOptions(); track app.slug) {
                <option [value]="app.slug">{{ app.name }}</option>
              }
            </select>
          </label>
          <label class="field">
            Minimum severity
            <select
              [ngModel]="min_severity() ?? ''"
              name="severity"
              (ngModelChange)="update('min_severity', $event)"
            >
              <option value="">All</option>
              @for (option of severities; track option) {
                <option [value]="option">{{ option }}</option>
              }
            </select>
          </label>
          <label class="field">
            Event
            <input class="mono" [(ngModel)]="eventInput" name="event" placeholder="http.request" />
          </label>
          <label class="field">
            Correlation ID
            <input class="mono" [(ngModel)]="correlationInput" name="correlation" />
          </label>
          <label class="field grow">
            Text in message
            <input [(ngModel)]="textInput" name="text" />
          </label>
          <button class="btn" type="submit">Apply</button>
          @if (hasFilters()) {
            <button class="btn" type="button" (click)="clearFilters()">Clear</button>
          }
        </form>
        @if (fixedRangeLabel(); as label) {
          <p class="range small">
            Showing <span class="mono">{{ label }}</span>
            <button class="btn tiny" type="button" (click)="clearRange()">
              Use the time range above instead
            </button>
          </p>
        }
        @if (list.error(); as failure) {
          <co-error what="Logs could not be loaded" [error]="failure" (retry)="list.reload()" />
        }
        @if (list.loading()) {
          <co-skeleton [count]="8" />
        } @else if (list.items().length === 0) {
          @if (hasFilters()) {
            <co-empty title="No log entries match these filters">
              <button class="btn" type="button" (click)="clearFilters()">Clear filters</button>
            </co-empty>
          } @else {
            <co-empty title="No log entries in this time range">
              Logs appear once an application sends them. Create an ingest key on its
              <a routerLink="/applications">application page</a>, under Configuration.
            </co-empty>
          }
        } @else {
          <co-log-list [items]="list.items()" />
          @if (list.hasMore()) {
            <div class="load-more">
              <button
                class="btn"
                type="button"
                [disabled]="list.loadingMore()"
                (click)="list.loadMore()"
              >
                {{ list.loadingMore() ? 'Loading…' : 'Load older entries' }}
              </button>
            </div>
          }
        }
      </section>
    </div>
  `,
  styles: `
    .grow {
      flex: 1 1 200px;
    }
    .range {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      border-bottom: 1px solid var(--rule);
      background: var(--surface-0);
    }
    .tiny {
      padding: 2px 8px;
      font-size: 12px;
    }
  `,
})
export class LogsPage {
  private readonly api = inject(ApiService);
  private readonly clock = inject(ClockService);
  private readonly router = inject(Router);
  private readonly timeRange = inject(TimeRangeService);

  // From the URL.
  readonly application = input<string>();
  readonly min_severity = input<string>();
  readonly event = input<string>();
  readonly correlation_id = input<string>();
  readonly q = input<string>();
  readonly from = input<string>();
  readonly to = input<string>();

  protected readonly severities = SEVERITIES;
  protected readonly eventInput = signal('');
  protected readonly correlationInput = signal('');
  protected readonly textInput = signal('');

  protected readonly hasFilters = computed(
    () =>
      !!(
        this.application() ||
        this.min_severity() ||
        this.event() ||
        this.correlation_id() ||
        this.q()
      ),
  );

  protected readonly fixedRangeLabel = computed(() => {
    const from = this.from();
    const to = this.to();
    if (!from || !to) {
      return null;
    }
    const utc = this.clock.utc();
    return `${formatTimestamp(from, utc).slice(5, 19)} to ${formatTimestamp(to, utc).slice(5, 19)}`;
  });

  private readonly key = computed(() => ({
    application: this.application(),
    min_severity: this.min_severity(),
    event: this.event(),
    correlation_id: this.correlation_id(),
    q: this.q(),
    from: this.from(),
    to: this.to(),
    window: this.timeRange.window() as TimeWindow,
  }));

  protected readonly list = pagedPoll(this.key, (key, cursor) => {
    const { window, from, to, ...filters } = key;
    const range = from && to ? { from, to } : this.timeRange.range(window);
    return this.api.logs({ ...filters, ...range, limit: 100, cursor });
  });

  private readonly applications = poll(
    () => true,
    () => this.api.applications().pipe(map((page) => page.items)),
    60_000,
  );
  protected readonly applicationOptions = computed(() => this.applications.data() ?? []);

  constructor() {
    // Text boxes follow the URL when it arrives from a link, then wait for Apply.
    queueMicrotask(() => {
      this.eventInput.set(this.event() ?? '');
      this.correlationInput.set(this.correlation_id() ?? '');
      this.textInput.set(this.q() ?? '');
    });
  }

  protected update(name: string, value: string): void {
    this.navigate({ [name]: value || null });
  }

  protected applyText(): void {
    this.navigate({
      event: this.eventInput().trim() || null,
      correlation_id: this.correlationInput().trim() || null,
      q: this.textInput().trim() || null,
    });
  }

  protected clearFilters(): void {
    this.eventInput.set('');
    this.correlationInput.set('');
    this.textInput.set('');
    this.navigate({
      application: null,
      min_severity: null,
      event: null,
      correlation_id: null,
      q: null,
    });
  }

  protected clearRange(): void {
    this.navigate({ from: null, to: null });
  }

  private navigate(queryParams: Record<string, string | null>): void {
    void this.router.navigate([], { queryParams, queryParamsHandling: 'merge' });
  }
}
