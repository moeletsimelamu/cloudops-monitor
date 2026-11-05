import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom, map } from 'rxjs';

import { ApiError, ApiService, toApiError } from '../core/api.service';
import { formatMs, formatPercent, formatReason, formatTimestamp } from '../core/format';
import { poll } from '../core/poll';
import { ClockService, TimeRangeService } from '../core/preferences.service';
import { EvidenceTimeline, TimeSelection } from '../shared/charts/evidence-timeline';
import { EmptyState, ErrorPanel, Skeleton, TimeText } from '../shared/states';
import { StatusBadge } from '../shared/status';
import {
  ChecksPanel,
  ConfigurationPanel,
  DeploymentsPanel,
  EventsPanel,
  LogsPanel,
} from './application-panels';

const TABS = [
  { id: 'checks', label: 'Checks' },
  { id: 'events', label: 'Events' },
  { id: 'deployments', label: 'Deployments' },
  { id: 'logs', label: 'Logs' },
  { id: 'configuration', label: 'Configuration' },
] as const;
type TabId = (typeof TABS)[number]['id'];

/** The troubleshooting workspace: one application's state, evidence and history. */
@Component({
  selector: 'co-application-detail',
  imports: [
    RouterLink,
    EmptyState,
    ErrorPanel,
    Skeleton,
    TimeText,
    StatusBadge,
    EvidenceTimeline,
    ChecksPanel,
    EventsPanel,
    DeploymentsPanel,
    LogsPanel,
    ConfigurationPanel,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './application-detail.html',
  styleUrl: './application-detail.css',
})
export class ApplicationDetailPage {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly clock = inject(ClockService);
  protected readonly timeRange = inject(TimeRangeService);

  readonly slug = input.required<string>();
  /** The open tab, from the URL. */
  readonly tab = input<string>();

  protected readonly tabs = TABS;
  protected readonly activeTab = computed<TabId>(() => {
    const requested = this.tab();
    return TABS.some((tab) => tab.id === requested) ? (requested as TabId) : 'checks';
  });

  /** Set by clicking the timeline; narrows the tabs to that period. */
  protected readonly focus = signal<TimeSelection | null>(null);
  /** Bumped after an action, so panels reload at once instead of at their next refresh. */
  protected readonly refreshCount = signal(0);

  protected readonly application = poll(this.slug, (slug) => this.api.application(slug));

  private readonly windowKey = computed(() => ({
    slug: this.slug(),
    window: this.timeRange.window(),
    refresh: this.refreshCount(),
  }));

  protected readonly metrics = poll(this.windowKey, (key) =>
    this.api.metrics(key.slug, key.window),
  );
  protected readonly requests = poll(this.windowKey, (key) =>
    this.api.requestMetrics(key.slug, key.window),
  );
  protected readonly incidents = poll(this.windowKey, (key) =>
    this.api.incidents(key.slug, 100).pipe(map((page) => page.items)),
  );
  protected readonly deployments = poll(this.windowKey, (key) =>
    this.api
      .deployments({ application: key.slug, ...this.timeRange.range(key.window), limit: 200 })
      .pipe(map((page) => page.items)),
  );

  protected readonly checking = signal(false);
  protected readonly checkResult = signal<string | null>(null);
  protected readonly actionError = signal<ApiError | null>(null);
  protected readonly togglingMonitoring = signal(false);

  protected readonly focusLabel = computed(() => {
    const focus = this.focus();
    if (!focus) {
      return null;
    }
    const utc = this.clock.utc();
    return `${formatTimestamp(focus.from, utc).slice(5, 16)} to ${formatTimestamp(focus.to, utc).slice(11, 16)}`;
  });

  protected readonly availabilityText = computed(() => {
    const value = this.metrics.data()?.availability.availability;
    return value === null || value === undefined ? '—' : formatPercent(value);
  });

  protected readonly coverageText = computed(() => {
    const value = this.metrics.data()?.availability.coverage;
    return value === null || value === undefined ? '—' : formatPercent(value);
  });

  protected readonly formatMs = formatMs;
  protected readonly formatReason = formatReason;

  protected selectTab(tab: TabId): void {
    void this.router.navigate([], {
      queryParams: { tab },
      queryParamsHandling: 'merge',
    });
  }

  /** Arrow keys move between tabs, as the tab pattern expects. */
  protected onTabKey(event: KeyboardEvent, index: number): void {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (step === 0) {
      return;
    }
    event.preventDefault();
    const next = TABS[(index + step + TABS.length) % TABS.length];
    this.selectTab(next.id);
    document.getElementById(`tab-${next.id}`)?.focus();
  }

  protected async checkNow(): Promise<void> {
    if (this.checking()) {
      return;
    }
    this.checking.set(true);
    this.actionError.set(null);
    this.checkResult.set(null);
    try {
      const check = await firstValueFrom(this.api.checkNow(this.slug()));
      const outcome = check.detail ?? (check.signal === 'ok' ? 'OK' : check.signal);
      this.checkResult.set(`Check finished in ${formatMs(check.duration_ms)}: ${outcome}`);
      this.afterAction();
    } catch (failure) {
      this.actionError.set(toApiError(failure));
    } finally {
      this.checking.set(false);
    }
  }

  protected async toggleMonitoring(): Promise<void> {
    const application = this.application.data();
    if (!application || this.togglingMonitoring()) {
      return;
    }
    this.togglingMonitoring.set(true);
    this.actionError.set(null);
    try {
      await firstValueFrom(
        application.monitoring_enabled
          ? this.api.pause(application.slug)
          : this.api.resume(application.slug),
      );
      this.afterAction();
    } catch (failure) {
      this.actionError.set(toApiError(failure));
    } finally {
      this.togglingMonitoring.set(false);
    }
  }

  protected afterAction(): void {
    this.application.reload();
    this.refreshCount.update((count) => count + 1);
  }
}
