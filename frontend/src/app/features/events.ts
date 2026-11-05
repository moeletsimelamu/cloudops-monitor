import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { map } from 'rxjs';

import { ApiService } from '../core/api.service';
import { EventSeverity, TimeWindow } from '../core/models';
import { pagedPoll } from '../core/paged';
import { poll } from '../core/poll';
import { TimeRangeService } from '../core/preferences.service';
import { EventList } from '../shared/records';
import { EmptyState, ErrorPanel, Skeleton } from '../shared/states';

const CATEGORIES = [
  { value: 'health', label: 'Health' },
  { value: 'deployment', label: 'Deployments' },
  { value: 'monitoring', label: 'Monitoring' },
  { value: 'application', label: 'Applications' },
];

const SEVERITIES: EventSeverity[] = ['info', 'warning', 'error', 'critical'];

/** Everything that changed, as a timeline. */
@Component({
  selector: 'co-events',
  imports: [FormsModule, EmptyState, ErrorPanel, EventList, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <section class="section" aria-label="Operational events">
        <div class="filters">
          <label class="field">
            Application
            <select [ngModel]="application()" (ngModelChange)="application.set($event)">
              <option value="">All</option>
              @for (app of applicationOptions(); track app.slug) {
                <option [value]="app.slug">{{ app.name }}</option>
              }
            </select>
          </label>
          <label class="field">
            Kind
            <select [ngModel]="category()" (ngModelChange)="category.set($event)">
              <option value="">All</option>
              @for (option of categories; track option.value) {
                <option [value]="option.value">{{ option.label }}</option>
              }
            </select>
          </label>
          <label class="field">
            Minimum severity
            <select [ngModel]="severity()" (ngModelChange)="severity.set($event)">
              <option value="">All</option>
              @for (option of severities; track option) {
                <option [value]="option">{{ option }}</option>
              }
            </select>
          </label>
        </div>
        @if (list.error(); as failure) {
          <co-error what="Events could not be loaded" [error]="failure" (retry)="list.reload()" />
        }
        @if (list.loading()) {
          <co-skeleton [count]="8" />
        } @else if (list.items().length === 0) {
          @if (hasFilters()) {
            <co-empty title="No events match these filters in this time range">
              <button class="btn" type="button" (click)="clearFilters()">Clear filters</button>
            </co-empty>
          } @else {
            <co-empty title="No events in this time range">
              Events are recorded when something changes: a state, a deployment or a setting. A
              steady system records few.
            </co-empty>
          }
        } @else {
          <co-event-list [items]="list.items()" />
          @if (list.hasMore()) {
            <div class="load-more">
              <button
                class="btn"
                type="button"
                [disabled]="list.loadingMore()"
                (click)="list.loadMore()"
              >
                {{ list.loadingMore() ? 'Loading…' : 'Load older events' }}
              </button>
            </div>
          }
        }
      </section>
    </div>
  `,
})
export class EventsPage {
  private readonly api = inject(ApiService);
  private readonly timeRange = inject(TimeRangeService);

  protected readonly categories = CATEGORIES;
  protected readonly severities = SEVERITIES;
  protected readonly application = signal('');
  protected readonly category = signal('');
  protected readonly severity = signal('');

  protected readonly hasFilters = computed(
    () => !!(this.application() || this.category() || this.severity()),
  );

  private readonly key = computed(() => ({
    application: this.application(),
    category: this.category(),
    min_severity: this.severity(),
    window: this.timeRange.window() as TimeWindow,
  }));

  protected readonly list = pagedPoll(this.key, (key, cursor) => {
    const { window, ...filters } = key;
    return this.api.events({ ...filters, ...this.timeRange.range(window), limit: 100, cursor });
  });

  private readonly applications = poll(
    () => true,
    () => this.api.applications().pipe(map((page) => page.items)),
    60_000,
  );
  protected readonly applicationOptions = computed(() => this.applications.data() ?? []);

  protected clearFilters(): void {
    this.application.set('');
    this.category.set('');
    this.severity.set('');
  }
}
