import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ApiService } from '../core/api.service';
import { Application, DisplayStatus } from '../core/models';
import { poll } from '../core/poll';
import { EmptyState, ErrorPanel, Skeleton, TimeText } from '../shared/states';
import { StatusBadge } from '../shared/status';

type SortKey = 'name' | 'status' | 'environment' | 'last_observed_at';

// Problems first, when sorting by status.
const STATUS_ORDER: DisplayStatus[] = [
  'unavailable',
  'degraded',
  'stale',
  'pending',
  'paused',
  'healthy',
];

/** What is monitored, and how. */
@Component({
  selector: 'co-applications',
  imports: [FormsModule, RouterLink, EmptyState, ErrorPanel, Skeleton, TimeText, StatusBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <section class="section" aria-label="Registered applications">
        <div class="filters">
          <label class="field">
            Search
            <input
              type="search"
              [ngModel]="search()"
              (ngModelChange)="search.set($event)"
              placeholder="Name or URL"
            />
          </label>
          <label class="field">
            Environment
            <select [ngModel]="environment()" (ngModelChange)="environment.set($event)">
              <option value="">All</option>
              @for (option of environments(); track option) {
                <option [value]="option">{{ option }}</option>
              }
            </select>
          </label>
          <label class="field">
            Status
            <select [ngModel]="status()" (ngModelChange)="status.set($event)">
              <option value="">All</option>
              @for (option of statuses; track option) {
                <option [value]="option">{{ option }}</option>
              }
            </select>
          </label>
          <a class="btn primary register" routerLink="/applications/new">Register application</a>
        </div>

        @if (applications.error(); as failure) {
          <co-error
            what="The applications could not be loaded"
            [error]="failure"
            (retry)="applications.reload()"
          />
        }
        @if (applications.loading()) {
          <co-skeleton />
        } @else if (applications.data(); as data) {
          @if (data.items.length === 0) {
            <co-empty title="No applications are registered">
              <a routerLink="/applications/new">Register the first one</a>.
            </co-empty>
          } @else if (visible().length === 0) {
            <co-empty title="No applications match these filters">
              <button class="btn" type="button" (click)="clearFilters()">Clear filters</button>
            </co-empty>
          } @else {
            <div class="table-scroll">
              <table class="data">
                <caption class="visually-hidden">
                  Registered applications; column headers sort the table
                </caption>
                <thead>
                  <tr>
                    <th scope="col" [attr.aria-sort]="ariaSort('status')">
                      <button type="button" (click)="sortBy('status')">
                        Status{{ arrow('status') }}
                      </button>
                    </th>
                    <th scope="col" [attr.aria-sort]="ariaSort('name')">
                      <button type="button" (click)="sortBy('name')">
                        Name{{ arrow('name') }}
                      </button>
                    </th>
                    <th scope="col" class="low" [attr.aria-sort]="ariaSort('environment')">
                      <button type="button" (click)="sortBy('environment')">
                        Environment{{ arrow('environment') }}
                      </button>
                    </th>
                    <th scope="col" class="low">Health URL</th>
                    <th scope="col" class="num low">Interval</th>
                    <th scope="col" [attr.aria-sort]="ariaSort('last_observed_at')">
                      <button type="button" (click)="sortBy('last_observed_at')">
                        Last check{{ arrow('last_observed_at') }}
                      </button>
                    </th>
                    <th scope="col" class="low">Last failure</th>
                  </tr>
                </thead>
                <tbody>
                  @for (application of visible(); track application.id) {
                    <tr>
                      <td><co-status [status]="application.status" /></td>
                      <td>
                        <a [routerLink]="['/applications', application.slug]">{{
                          application.name
                        }}</a>
                      </td>
                      <td class="low muted">{{ application.environment }}</td>
                      <td class="low mono url">{{ application.check_url }}</td>
                      <td class="num low">{{ application.interval_seconds }} s</td>
                      <td><co-time [at]="application.last_observed_at" none="never" /></td>
                      <td class="low">
                        <co-time [at]="application.last_failure_at" none="none" />
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        }
      </section>
    </div>
  `,
  styles: `
    .register {
      margin-left: auto;
    }
    .url {
      overflow-wrap: anywhere;
    }
    @media (max-width: 719px) {
      .register {
        margin-left: 0;
      }
    }
  `,
})
export class ApplicationsPage {
  private readonly api = inject(ApiService);

  protected readonly applications = poll(
    () => true,
    () => this.api.applications(),
  );

  protected readonly statuses = STATUS_ORDER;
  protected readonly search = signal('');
  protected readonly environment = signal('');
  protected readonly status = signal('');
  private readonly sort = signal<{ key: SortKey; descending: boolean }>({
    key: 'status',
    descending: false,
  });

  protected readonly environments = computed(() => {
    const values = (this.applications.data()?.items ?? []).map((app) => app.environment);
    return [...new Set(values)].sort();
  });

  protected readonly visible = computed(() => {
    const text = this.search().trim().toLowerCase();
    const environment = this.environment();
    const status = this.status();
    const { key, descending } = this.sort();
    const rows = (this.applications.data()?.items ?? []).filter(
      (app) =>
        (!text ||
          app.name.toLowerCase().includes(text) ||
          app.check_url.toLowerCase().includes(text)) &&
        (!environment || app.environment === environment) &&
        (!status || app.status === status),
    );
    const compare = (a: Application, b: Application): number => {
      if (key === 'status') {
        return STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status);
      }
      return (a[key] ?? '').localeCompare(b[key] ?? '');
    };
    rows.sort((a, b) => (descending ? -compare(a, b) : compare(a, b)));
    return rows;
  });

  protected sortBy(key: SortKey): void {
    this.sort.update((current) => ({
      key,
      descending: current.key === key ? !current.descending : false,
    }));
  }

  protected ariaSort(key: SortKey): 'ascending' | 'descending' | null {
    const current = this.sort();
    if (current.key !== key) {
      return null;
    }
    return current.descending ? 'descending' : 'ascending';
  }

  protected arrow(key: SortKey): string {
    const current = this.sort();
    if (current.key !== key) {
      return '';
    }
    return current.descending ? ' ↓' : ' ↑';
  }

  protected clearFilters(): void {
    this.search.set('');
    this.environment.set('');
    this.status.set('');
  }
}
