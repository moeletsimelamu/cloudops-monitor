import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { ApiService } from '../core/api.service';
import { formatMs, formatPercent, formatTimestamp } from '../core/format';
import { Bucket, HealthRow } from '../core/models';
import { poll } from '../core/poll';
import { ClockService, TimeRangeService } from '../core/preferences.service';
import { EmptyState, ErrorPanel, Skeleton } from '../shared/states';
import { Glyph, StatusBadge } from '../shared/status';

/**
 * How every application behaved over the window. Each cell is one period's worst check. Height
 * encodes the signal as well as colour, so it reads in greyscale; a period with no checks has no
 * mark at all, so missing data never looks like good data.
 */
@Component({
  selector: 'co-health',
  imports: [RouterLink, EmptyState, ErrorPanel, Skeleton, StatusBadge, Glyph],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <section class="section" aria-labelledby="matrix-heading">
        <div class="section-head">
          <h2 id="matrix-heading">Worst check per period</h2>
          @if (matrix.data(); as data) {
            <span class="note">Last {{ data.window }}, {{ periodLabel() }} per column</span>
          }
          <span class="legend small end">
            <span class="key"><co-glyph shape="circle" tone="ok" /> OK (short)</span>
            <span class="key"><co-glyph shape="triangle" tone="warn" /> Warning (medium)</span>
            <span class="key"><co-glyph shape="square" tone="fail" /> Failed (tall)</span>
            <span class="key muted">Gap: no checks</span>
          </span>
        </div>
        @if (matrix.error(); as failure) {
          <co-error
            what="Health history could not be loaded"
            [error]="failure"
            (retry)="matrix.reload()"
          />
        }
        @if (matrix.loading()) {
          <co-skeleton [count]="6" />
        } @else if (matrix.data(); as data) {
          @if (data.rows.length === 0) {
            <co-empty title="No applications are being monitored yet">
              <a routerLink="/applications/new">Register an application</a> to build up history.
            </co-empty>
          } @else {
            <div class="matrix">
              @for (row of data.rows; track row.application.id) {
                <div class="row">
                  <div class="identity">
                    <a [routerLink]="['/applications', row.application.slug]">{{
                      row.application.name
                    }}</a>
                    <co-status [status]="row.application.status" class="small" />
                  </div>
                  <div class="strip" [attr.aria-label]="summary(row)" role="img">
                    @for (bucket of row.buckets; track bucket.start) {
                      <span
                        class="cell"
                        [class]="'cell ' + (bucket.worst ?? 'none')"
                        [title]="cellTitle(bucket)"
                        (click)="open(row.application.slug)"
                      ></span>
                    }
                  </div>
                  <div class="numbers mono">
                    <span
                      [title]="
                        row.availability.insufficient_reason ?? data.definitions['availability']
                      "
                    >
                      {{ availabilityText(row) }}
                    </span>
                    <span class="muted" [title]="data.definitions['coverage']">{{
                      coverageText(row)
                    }}</span>
                    <span class="muted">p95 {{ formatMs(row.p95_ms) }}</span>
                  </div>
                </div>
              }
              <div class="axis mono small muted">
                <span class="axis-start">{{ startLabel() }}</span>
                <span class="axis-end">{{ endLabel() }}</span>
              </div>
            </div>
            <p class="foot muted small">
              Availability, coverage and p95 are for the same window.
              {{ data.definitions['availability'] }}
              {{ data.definitions['coverage'] }}
            </p>
          }
        }
      </section>
    </div>
  `,
  styles: `
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 2px 12px;
    }
    .key {
      display: inline-flex;
      align-items: baseline;
      gap: 4px;
    }
    .row {
      display: grid;
      grid-template-columns: 190px minmax(0, 1fr) 230px;
      gap: 12px;
      align-items: center;
      padding: 7px 12px;
      border-bottom: 1px solid var(--rule);
    }
    .identity {
      display: flex;
      flex-direction: column;
      min-width: 0;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .strip {
      display: flex;
      align-items: flex-end;
      gap: 1px;
      height: 26px;
      border-bottom: 1px solid var(--rule-strong);
    }
    .cell {
      flex: 1 1 0;
      min-width: 1px;
      cursor: pointer;
    }
    .cell.none {
      height: 100%;
      background: transparent;
    }
    .cell.ok {
      height: 28%;
      background: var(--ok);
    }
    .cell.warn {
      height: 62%;
      background: var(--warn);
    }
    .cell.fail {
      height: 100%;
      background: var(--fail);
    }
    .cell:hover {
      outline: 1px solid var(--ink);
    }
    .numbers {
      display: flex;
      flex-wrap: wrap;
      gap: 0 10px;
      justify-content: flex-end;
    }
    .axis {
      display: grid;
      grid-template-columns: 190px 1fr 1fr 230px;
      gap: 0 12px;
      padding: 4px 12px;
    }
    .axis-start {
      grid-column: 2;
    }
    .axis-end {
      grid-column: 3;
      text-align: right;
    }
    .foot {
      padding: 8px 12px;
      border-top: 1px solid var(--rule);
    }
    @media (max-width: 899px) {
      .row {
        grid-template-columns: minmax(0, 1fr);
        gap: 4px;
      }
      .numbers {
        justify-content: flex-start;
      }
      .axis {
        grid-template-columns: 1fr 1fr;
      }
      .axis-start {
        grid-column: 1;
      }
      .axis-end {
        grid-column: 2;
      }
    }
  `,
})
export class HealthPage {
  private readonly api = inject(ApiService);
  private readonly clock = inject(ClockService);
  private readonly router = inject(Router);
  private readonly timeRange = inject(TimeRangeService);

  protected readonly formatMs = formatMs;

  protected readonly matrix = poll(this.timeRange.window, (window) =>
    this.api.healthMatrix(window),
  );

  protected readonly periodLabel = computed(() => {
    const seconds = this.matrix.data()?.bucket_seconds ?? 0;
    return seconds >= 3600 ? `${seconds / 3600} h` : `${seconds / 60} min`;
  });

  protected readonly startLabel = computed(() => {
    const data = this.matrix.data();
    return data ? formatTimestamp(data.window_start, this.clock.utc()).slice(5, 16) : '';
  });

  protected readonly endLabel = computed(() => {
    const data = this.matrix.data();
    return data ? formatTimestamp(data.window_end, this.clock.utc()).slice(5, 16) : '';
  });

  protected availabilityText(row: HealthRow): string {
    const value = row.availability.availability;
    return value === null ? '—' : formatPercent(value);
  }

  protected coverageText(row: HealthRow): string {
    const value = row.availability.coverage;
    return value === null ? 'no coverage' : `${Math.round(value * 100)}% cov.`;
  }

  protected cellTitle(bucket: Bucket): string {
    const when = formatTimestamp(bucket.start, this.clock.utc()).slice(5, 16);
    if (bucket.worst === null) {
      return `${when}: no checks`;
    }
    return `${when}: worst ${bucket.worst}, ${bucket.checks} checks, ${bucket.failed} not OK, p95 ${formatMs(bucket.p95_ms)}`;
  }

  protected summary(row: HealthRow): string {
    const count = (worst: Bucket['worst']): number =>
      row.buckets.filter((bucket) => bucket.worst === worst).length;
    return (
      `${row.application.name}: ${count('fail')} periods with failed checks, ` +
      `${count('warn')} with warnings, ${count('ok')} OK, ${count(null)} with no checks.`
    );
  }

  protected open(slug: string): void {
    void this.router.navigate(['/applications', slug], { queryParamsHandling: 'preserve' });
  }
}
