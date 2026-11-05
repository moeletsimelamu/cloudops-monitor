import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom, map } from 'rxjs';

import { ApiError, ApiService, toApiError } from '../core/api.service';
import { aroundMoment, formatMs, formatReason } from '../core/format';
import { Application, Check, TimeWindow } from '../core/models';
import { pagedPoll } from '../core/paged';
import { TimeRangeService } from '../core/preferences.service';
import { TimeSelection } from '../shared/charts/evidence-timeline';
import { DeploymentTable, EventList, LogList } from '../shared/records';
import { ConfirmDialog, EmptyState, ErrorPanel, Skeleton, TimeText } from '../shared/states';
import { SignalBadge } from '../shared/status';

interface PanelKey {
  slug: string;
  focus: TimeSelection | null;
  window: TimeWindow;
  refresh: number;
}

/** A panel's time range: the period picked on the timeline, or the global window ending now. */
function rangeOf(key: PanelKey, timeRange: TimeRangeService): { from: string; to: string } {
  return key.focus ?? timeRange.range(key.window);
}

@Component({
  selector: 'co-checks-panel',
  imports: [RouterLink, EmptyState, ErrorPanel, Skeleton, TimeText, SignalBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (list.error(); as failure) {
      <co-error what="Checks could not be loaded" [error]="failure" (retry)="list.reload()" />
    }
    @if (list.loading()) {
      <co-skeleton />
    } @else if (list.items().length === 0) {
      <co-empty title="No checks in this period" />
    } @else {
      <div class="table-scroll">
        <table class="data">
          <caption class="visually-hidden">
            Health checks, newest first
          </caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Result</th>
              <th scope="col" class="num low">HTTP</th>
              <th scope="col" class="num">Took</th>
              <th scope="col" class="low">Detail</th>
              <th scope="col" class="low">State after</th>
              <th scope="col" class="low">Address</th>
              <th scope="col" class="low"><span class="visually-hidden">Logs</span></th>
            </tr>
          </thead>
          <tbody>
            @for (check of list.items(); track check.id) {
              <tr class="main">
                <td><co-time [at]="check.started_at" mode="exact" /></td>
                <td><co-signal [signal]="check.signal" /></td>
                <td class="num low">{{ check.http_status ?? '—' }}</td>
                <td class="num">{{ formatMs(check.duration_ms) }}</td>
                <td class="low detail">{{ check.detail ?? formatReason(check.reason) }}</td>
                <td class="low muted">{{ check.state_after }}</td>
                <td class="low mono muted">{{ check.resolved_ip ?? '—' }}</td>
                <td class="low">
                  <a class="small nowrap" routerLink="/logs" [queryParams]="logsAround(check)"
                    >Logs ±10 min</a
                  >
                </td>
              </tr>
              <!-- On a phone, the detail takes a line of its own instead of a narrow column. -->
              <tr class="narrow">
                <td colspan="8">
                  <span class="detail small">{{ check.detail ?? formatReason(check.reason) }}</span>
                  ·
                  <a class="small nowrap" routerLink="/logs" [queryParams]="logsAround(check)"
                    >Logs ±10 min</a
                  >
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      @if (list.hasMore()) {
        <div class="load-more">
          <button
            class="btn"
            type="button"
            [disabled]="list.loadingMore()"
            (click)="list.loadMore()"
          >
            {{ list.loadingMore() ? 'Loading…' : 'Load older checks' }}
          </button>
        </div>
      }
    }
  `,
  styles: `
    .detail {
      overflow-wrap: anywhere;
    }
    .nowrap {
      white-space: nowrap;
    }
    .narrow {
      display: none;
    }
    @media (max-width: 719px) {
      .narrow {
        display: table-row;
      }
      table.data tr.main td {
        padding-bottom: 0;
        border-bottom: 0;
      }
      table.data tr.narrow td {
        padding-top: 2px;
      }
    }
  `,
})
export class ChecksPanel {
  private readonly api = inject(ApiService);
  private readonly timeRange = inject(TimeRangeService);

  readonly slug = input.required<string>();
  readonly focus = input<TimeSelection | null>(null);
  readonly refresh = input(0);

  protected readonly formatMs = formatMs;
  protected readonly formatReason = formatReason;

  private readonly key = computed<PanelKey>(() => ({
    slug: this.slug(),
    focus: this.focus(),
    window: this.timeRange.window(),
    refresh: this.refresh(),
  }));

  protected readonly list = pagedPoll<PanelKey, Check>(this.key, (key, cursor) =>
    this.api.checks(key.slug, { ...rangeOf(key, this.timeRange), limit: 50, before: cursor }).pipe(
      map((page) => ({
        items: page.items,
        next_cursor: page.next_before === null ? null : String(page.next_before),
      })),
    ),
  );

  protected logsAround(check: Check): Record<string, string> {
    return { application: this.slug(), ...aroundMoment(check.started_at) };
  }
}

@Component({
  selector: 'co-events-panel',
  imports: [EmptyState, ErrorPanel, EventList, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (list.error(); as failure) {
      <co-error what="Events could not be loaded" [error]="failure" (retry)="list.reload()" />
    }
    @if (list.loading()) {
      <co-skeleton />
    } @else if (list.items().length === 0) {
      <co-empty title="No events in this period">
        Events are recorded when something changes, such as a state change or a deployment.
      </co-empty>
    } @else {
      <co-event-list [items]="list.items()" [showApplication]="false" />
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
  `,
})
export class EventsPanel {
  private readonly api = inject(ApiService);
  private readonly timeRange = inject(TimeRangeService);

  readonly slug = input.required<string>();
  readonly focus = input<TimeSelection | null>(null);
  readonly refresh = input(0);

  private readonly key = computed<PanelKey>(() => ({
    slug: this.slug(),
    focus: this.focus(),
    window: this.timeRange.window(),
    refresh: this.refresh(),
  }));

  protected readonly list = pagedPoll(this.key, (key, cursor) =>
    this.api.events({
      application: key.slug,
      ...rangeOf(key, this.timeRange),
      limit: 50,
      cursor,
    }),
  );
}

@Component({
  selector: 'co-deployments-panel',
  imports: [DeploymentTable, EmptyState, ErrorPanel, RouterLink, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (!application().github_repo) {
      <co-empty title="No deployment source is configured for this application">
        <a [routerLink]="['/applications', application().slug, 'edit']"
          >Add a GitHub repository and workflow</a
        >
        to see its deployments here.
      </co-empty>
    } @else {
      <div class="bar small">
        <span class="muted"
          >From GitHub Actions: <span class="mono">{{ application().github_repo }}</span
          >, <span class="mono">{{ application().deploy_workflow }}</span></span
        >
        <button class="btn" type="button" [disabled]="syncing()" (click)="sync()">
          {{ syncing() ? 'Syncing…' : 'Sync now' }}
        </button>
        <span aria-live="polite">{{ syncMessage() }}</span>
      </div>
      @if (syncError(); as failure) {
        <co-error what="The sync did not run" [error]="failure" [retryable]="false" />
      }
      @if (list.error(); as failure) {
        <co-error
          what="Deployments could not be loaded"
          [error]="failure"
          (retry)="list.reload()"
        />
      }
      @if (list.loading()) {
        <co-skeleton />
      } @else if (list.items().length === 0) {
        <co-empty
          [title]="focus() ? 'No deployments in the selected period' : 'No deployments yet'"
        >
          {{ focus() ? 'Clear the selection to see them all, or sync' : 'Sync' }} now if a run has
          only just finished.
        </co-empty>
      } @else {
        <co-deployment-table [items]="list.items()" [showApplication]="false" />
        @if (list.hasMore()) {
          <div class="load-more">
            <button
              class="btn"
              type="button"
              [disabled]="list.loadingMore()"
              (click)="list.loadMore()"
            >
              {{ list.loadingMore() ? 'Loading…' : 'Load older deployments' }}
            </button>
          </div>
        }
      }
    }
  `,
  styles: `
    .bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px 12px;
      padding: 8px 12px;
      border-bottom: 1px solid var(--rule);
      overflow-wrap: anywhere;
    }
  `,
})
export class DeploymentsPanel {
  private readonly api = inject(ApiService);

  readonly application = input.required<Application>();
  readonly focus = input<TimeSelection | null>(null);
  readonly refresh = input(0);
  readonly changed = output<void>();

  protected readonly syncing = signal(false);
  protected readonly syncMessage = signal('');
  protected readonly syncError = signal<ApiError | null>(null);

  private readonly key = computed(() => ({
    slug: this.application().slug,
    focus: this.focus(),
    refresh: this.refresh(),
  }));

  // The page's time range does not hide older records here: deployments are rare, and the last one
  // matters however long ago it was. Selecting a period on the timeline does narrow the list.
  protected readonly list = pagedPoll(this.key, (key, cursor) =>
    this.api.deployments({
      application: key.slug,
      ...key.focus,
      limit: 50,
      cursor,
    }),
  );

  protected async sync(): Promise<void> {
    if (this.syncing()) {
      return;
    }
    this.syncing.set(true);
    this.syncMessage.set('');
    this.syncError.set(null);
    try {
      const result = await firstValueFrom(this.api.syncNow(this.application().slug));
      this.syncMessage.set(
        result.status === 'synced'
          ? `Synced: ${result.created} new, ${result.updated} updated.`
          : result.status === 'not_modified'
            ? 'Already up to date.'
            : (result.message ?? 'The sync did not complete.'),
      );
      this.changed.emit();
    } catch (failure) {
      this.syncError.set(toApiError(failure));
    } finally {
      this.syncing.set(false);
    }
  }
}

@Component({
  selector: 'co-logs-panel',
  imports: [EmptyState, ErrorPanel, LogList, RouterLink, Skeleton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (list.error(); as failure) {
      <co-error what="Logs could not be loaded" [error]="failure" (retry)="list.reload()" />
    }
    @if (list.loading()) {
      <co-skeleton />
    } @else if (list.items().length === 0) {
      @if (application().has_ingest_key) {
        <co-empty title="No log entries in this period">
          The application has an ingest key, but sent nothing for this time range.
        </co-empty>
      } @else {
        <co-empty title="This application does not send logs yet">
          Create an ingest key under Configuration, then send entries to
          <span class="mono">POST /api/ingest/logs</span>.
        </co-empty>
      }
    } @else {
      <p class="bar small">
        <a routerLink="/logs" [queryParams]="{ application: application().slug }"
          >Open in the log explorer</a
        >
        for filters.
      </p>
      <co-log-list [items]="list.items()" [showApplication]="false" />
      @if (list.hasMore()) {
        <div class="load-more">
          <button
            class="btn"
            type="button"
            [disabled]="list.loadingMore()"
            (click)="list.loadMore()"
          >
            {{ list.loadingMore() ? 'Loading…' : 'Load older logs' }}
          </button>
        </div>
      }
    }
  `,
  styles: `
    .bar {
      padding: 6px 12px;
      border-bottom: 1px solid var(--rule);
    }
  `,
})
export class LogsPanel {
  private readonly api = inject(ApiService);
  private readonly timeRange = inject(TimeRangeService);

  readonly application = input.required<Application>();
  readonly focus = input<TimeSelection | null>(null);
  readonly refresh = input(0);

  private readonly key = computed<PanelKey>(() => ({
    slug: this.application().slug,
    focus: this.focus(),
    // The log API allows at most 7 days, which the longest window fits.
    window: this.timeRange.window(),
    refresh: this.refresh(),
  }));

  protected readonly list = pagedPoll(this.key, (key, cursor) =>
    this.api.logs({
      application: key.slug,
      ...rangeOf(key, this.timeRange),
      limit: 50,
      cursor,
    }),
  );
}

@Component({
  selector: 'co-configuration-panel',
  imports: [ConfirmDialog, ErrorPanel, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dl class="settings">
      <div>
        <dt>Health URL</dt>
        <dd class="mono">{{ application().check_url }}</dd>
      </div>
      <div>
        <dt>Check every</dt>
        <dd class="mono">{{ application().interval_seconds }} s</dd>
      </div>
      <div>
        <dt>Timeout</dt>
        <dd class="mono">{{ application().timeout_seconds }} s</dd>
      </div>
      <div>
        <dt>Slow above</dt>
        <dd class="mono">{{ application().slow_threshold_ms }} ms</dd>
      </div>
      <div>
        <dt>Failing checks before changing state</dt>
        <dd class="mono">{{ application().failure_threshold }}</dd>
      </div>
      <div>
        <dt>Good checks before recovering</dt>
        <dd class="mono">{{ application().recovery_threshold }}</dd>
      </div>
      <div>
        <dt>Deployment source</dt>
        <dd class="mono">
          @if (application().github_repo) {
            {{ application().github_repo }}, {{ application().deploy_workflow }}
            @if (application().deploy_branch) {
              , {{ application().deploy_branch }}
            }
          } @else {
            none
          }
        </dd>
      </div>
    </dl>
    <div class="actions">
      <a class="btn" [routerLink]="['/applications', application().slug, 'edit']">Edit settings</a>
    </div>

    <div class="block">
      <h3>Log ingest key</h3>
      <p class="small muted">
        The application sends its logs with this key. It can only write to this application.
        @if (application().has_ingest_key) {
          A key exists. Creating a new one stops the old one working at once.
        } @else {
          No key exists yet.
        }
      </p>
      @if (newKey(); as key) {
        <div class="key" role="status">
          <p class="small">Copy this now. It is shown once and only a hash of it is stored.</p>
          <code class="mono">{{ key }}</code>
        </div>
      }
      <div class="actions">
        <button class="btn" type="button" [disabled]="keyPending()" (click)="createKey()">
          {{ application().has_ingest_key ? 'Replace key' : 'Create key' }}
        </button>
        @if (application().has_ingest_key) {
          <button class="btn" type="button" [disabled]="keyPending()" (click)="revokeKey()">
            Revoke key
          </button>
        }
      </div>
    </div>

    <div class="block">
      <h3>Remove application</h3>
      <p class="small muted">
        Deletes its checks, incidents, deployments and logs. An audit event is kept.
      </p>
      <div class="actions">
        <button class="btn danger" type="button" (click)="confirm().open()">
          Remove {{ application().name }}
        </button>
      </div>
      <co-confirm
        [title]="'Remove ' + application().name + '?'"
        action="Remove application"
        (confirmed)="remove()"
      >
        This deletes its history and cannot be undone.
      </co-confirm>
    </div>

    @if (error(); as failure) {
      <co-error what="That did not work" [error]="failure" [retryable]="false" />
    }
  `,
  styles: `
    .settings {
      display: grid;
      gap: 3px;
      margin: 0;
      padding: 12px;
    }
    .settings > div {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      gap: 12px;
    }
    dt {
      color: var(--ink-muted);
      font-size: 13px;
    }
    dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 0 12px 12px;
    }
    .block {
      border-top: 1px solid var(--rule);
    }
    .block h3 {
      padding: 12px 12px 2px;
    }
    .block > p {
      padding: 0 12px 8px;
    }
    .block .actions {
      padding-top: 0;
    }
    .key {
      margin: 0 12px 10px;
      padding: 8px 10px;
      background: var(--surface-2);
      border-radius: var(--radius);
    }
    code {
      display: block;
      margin-top: 4px;
      overflow-wrap: anywhere;
    }
    @media (max-width: 719px) {
      .settings > div {
        grid-template-columns: minmax(0, 1fr);
        gap: 0;
      }
    }
  `,
})
export class ConfigurationPanel {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly application = input.required<Application>();
  readonly changed = output<void>();

  protected readonly confirm = viewChild.required(ConfirmDialog);
  protected readonly newKey = signal<string | null>(null);
  protected readonly keyPending = signal(false);
  protected readonly error = signal<ApiError | null>(null);

  protected async createKey(): Promise<void> {
    this.keyPending.set(true);
    this.error.set(null);
    try {
      const created = await firstValueFrom(this.api.createIngestKey(this.application().slug));
      this.newKey.set(created.ingest_key);
      this.changed.emit();
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.keyPending.set(false);
    }
  }

  protected async revokeKey(): Promise<void> {
    this.keyPending.set(true);
    this.error.set(null);
    try {
      await firstValueFrom(this.api.revokeIngestKey(this.application().slug));
      this.newKey.set(null);
      this.changed.emit();
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.keyPending.set(false);
    }
  }

  protected async remove(): Promise<void> {
    this.error.set(null);
    try {
      await firstValueFrom(this.api.deleteApplication(this.application().slug));
      await this.router.navigate(['/applications']);
    } catch (failure) {
      this.error.set(toApiError(failure));
    }
  }
}
