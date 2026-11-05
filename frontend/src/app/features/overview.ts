import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../core/api.service';
import { formatDuration, formatMs, formatPercent, formatReason, shortSha } from '../core/format';
import { Availability, FleetEntry } from '../core/models';
import { poll } from '../core/poll';
import { ClockService, TimeRangeService } from '../core/preferences.service';
import { EmptyState, ErrorPanel, Skeleton, TimeText } from '../shared/states';
import { Glyph, OutcomeBadge, SeverityBadge, StatusBadge } from '../shared/status';

/**
 * What needs attention now, every application's state, and what changed recently. Everything is
 * from stored records through /api/overview; nothing here is estimated.
 */
@Component({
  selector: 'co-overview',
  imports: [
    RouterLink,
    EmptyState,
    ErrorPanel,
    Skeleton,
    TimeText,
    Glyph,
    OutcomeBadge,
    SeverityBadge,
    StatusBadge,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './overview.html',
  styleUrl: './overview.css',
})
export class OverviewPage {
  private readonly api = inject(ApiService);
  private readonly clock = inject(ClockService);
  protected readonly timeRange = inject(TimeRangeService);

  protected readonly overview = poll(
    () => true,
    () => this.api.overview(),
  );

  protected readonly data = this.overview.data;
  protected readonly counts = computed(() => this.data()?.counts ?? null);

  /** Enough to see what just happened; "All events" has the rest. */
  protected readonly recentChangesShown = 8;

  protected readonly formatMs = formatMs;
  protected readonly formatReason = formatReason;
  protected readonly shortSha = shortSha;
  protected readonly formatDuration = formatDuration;

  protected availabilityText(availability: Availability): string {
    return availability.availability === null ? '—' : formatPercent(availability.availability);
  }

  /** How long this problem has lasted, from the state change or from when it went quiet. */
  protected problemFor(entry: FleetEntry): string {
    const application = entry.application;
    const since =
      application.status === 'stale' ? application.stale_since : application.state_since;
    if (!since) {
      return '—';
    }
    return formatDuration((this.clock.now() - new Date(since).getTime()) / 1000);
  }
}
