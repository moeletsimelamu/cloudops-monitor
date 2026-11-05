import { ChangeDetectionStrategy, Component, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { aroundMoment, formatDayHeading, formatDuration, shortSha } from '../core/format';
import { Deployment, LogEntry, OperationalEvent } from '../core/models';
import { ClockService } from '../core/preferences.service';
import { TimeText } from './states';
import { OutcomeBadge, SeverityBadge } from './status';

/** Compact single-line log entries; a row expands to show everything recorded with it. */
@Component({
  selector: 'co-log-list',
  imports: [RouterLink, TimeText, SeverityBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ul class="logs">
      @for (entry of items(); track entry.id) {
        <li [class.expanded]="expanded() === entry.id">
          <button
            class="row"
            type="button"
            [attr.aria-expanded]="expanded() === entry.id"
            (click)="toggle(entry.id)"
          >
            <co-time class="when" [at]="entry.timestamp" mode="exact" />
            <co-severity class="severity" [severity]="entry.severity" />
            @if (showApplication()) {
              <span class="application">{{ entry.application_name }}</span>
            }
            <span class="event mono">{{ entry.event }}</span>
            <span class="message">{{ entry.message }}</span>
          </button>
          @if (expanded() === entry.id) {
            <dl class="detail small">
              <div>
                <dt>Application</dt>
                <dd>
                  <a [routerLink]="['/applications', entry.application_slug]">{{
                    entry.application_name
                  }}</a>
                  ({{ entry.environment }})
                </dd>
              </div>
              <div>
                <dt>Received</dt>
                <dd><co-time [at]="entry.received_at" mode="exact" /></dd>
              </div>
              @if (entry.correlation_id; as correlation) {
                <div>
                  <dt>Correlation ID</dt>
                  <dd>
                    <a
                      class="mono"
                      routerLink="/logs"
                      [queryParams]="{ correlation_id: correlation }"
                      queryParamsHandling="merge"
                      >{{ correlation }}</a
                    >
                  </dd>
                </div>
              }
              @for (attribute of attributes(entry); track attribute.key) {
                <div>
                  <dt class="mono">{{ attribute.key }}</dt>
                  <dd class="mono">{{ attribute.value }}</dd>
                </div>
              }
            </dl>
          }
        </li>
      }
    </ul>
  `,
  styles: `
    .logs {
      margin: 0;
      padding: 0;
      list-style: none;
    }
    li {
      border-bottom: 1px solid var(--rule);
    }
    li:last-child {
      border-bottom: 0;
    }
    .row {
      display: grid;
      grid-template-columns: 148px 78px auto 150px minmax(0, 1fr);
      gap: 10px;
      align-items: baseline;
      width: 100%;
      padding: 4px 12px;
      border: 0;
      background: none;
      color: var(--ink);
      font: inherit;
      font-size: 13px;
      text-align: left;
      cursor: pointer;
    }
    .row:hover {
      background: var(--row-hover);
    }
    .row:focus-visible {
      outline-offset: -2px;
    }
    .application {
      white-space: nowrap;
    }
    .event {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--ink-muted);
    }
    .message {
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .severity {
      font-size: 12px;
    }
    .detail {
      margin: 0;
      padding: 8px 12px 10px;
      background: var(--surface-0);
      border-top: 1px solid var(--rule);
    }
    .detail > div {
      display: grid;
      grid-template-columns: 160px minmax(0, 1fr);
      gap: 10px;
      padding: 1px 0;
    }
    dt {
      color: var(--ink-muted);
    }
    dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
    @media (max-width: 899px) {
      .row {
        grid-template-columns: auto minmax(0, 1fr);
      }
      .message {
        grid-column: 1 / -1;
      }
      .event,
      .application {
        display: none;
      }
    }
  `,
})
export class LogList {
  readonly items = input.required<readonly LogEntry[]>();
  readonly showApplication = input(true);
  protected readonly expanded = signal<number | null>(null);

  protected toggle(id: number): void {
    this.expanded.update((current) => (current === id ? null : id));
  }

  protected attributes(entry: LogEntry): { key: string; value: string }[] {
    return Object.entries(entry.attributes ?? {}).map(([key, value]) => ({
      key,
      value: String(value),
    }));
  }
}

/** A vertical timeline of events, grouped by day. */
@Component({
  selector: 'co-event-list',
  imports: [RouterLink, TimeText, SeverityBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (day of days(); track day.heading) {
      <h2 class="day mono">{{ day.heading }}</h2>
      <ol class="events">
        @for (event of day.events; track event.id) {
          <li>
            <co-time class="when" [at]="event.occurred_at" mode="exact" />
            <co-severity class="severity" [severity]="event.severity" />
            <div class="body">
              <p>{{ event.summary }}</p>
              <p class="meta small muted">
                <span class="mono">{{ event.type }}</span>
                @if (showApplication() && event.application_slug) {
                  ·
                  <a [routerLink]="['/applications', event.application_slug]">{{
                    event.application_name
                  }}</a>
                }
                @if (event.application_slug; as slug) {
                  ·
                  <a routerLink="/logs" [queryParams]="logsAround(slug, event.occurred_at)"
                    >Logs ±10 min</a
                  >
                }
                @if (link(event); as url) {
                  · <a [href]="url" target="_blank" rel="noopener">GitHub run</a>
                }
              </p>
            </div>
          </li>
        }
      </ol>
    }
  `,
  styles: `
    .day {
      padding: 6px 12px;
      background: var(--surface-0);
      border-bottom: 1px solid var(--rule);
      font-weight: 500;
    }
    .events {
      margin: 0;
      padding: 0;
      list-style: none;
    }
    li {
      display: grid;
      grid-template-columns: 148px 78px minmax(0, 1fr);
      gap: 10px;
      align-items: baseline;
      padding: 6px 12px;
      border-bottom: 1px solid var(--rule);
      font-size: 13px;
    }
    .severity {
      font-size: 12px;
    }
    .body {
      overflow-wrap: anywhere;
    }
    @media (max-width: 719px) {
      li {
        grid-template-columns: auto minmax(0, 1fr);
      }
      .body {
        grid-column: 1 / -1;
      }
    }
  `,
})
export class EventList {
  private readonly clock = inject(ClockService);

  readonly items = input.required<readonly OperationalEvent[]>();
  readonly showApplication = input(true);

  protected readonly days = computed(() => {
    const groups: { heading: string; events: OperationalEvent[] }[] = [];
    for (const event of this.items()) {
      const heading = formatDayHeading(event.occurred_at, this.clock.utc());
      const last = groups.at(-1);
      if (last?.heading === heading) {
        last.events.push(event);
      } else {
        groups.push({ heading, events: [event] });
      }
    }
    return groups;
  });

  protected logsAround(slug: string, moment: string): Record<string, string> {
    return { application: slug, ...aroundMoment(moment) };
  }

  protected link(event: OperationalEvent): string | null {
    const url = event.details['url'];
    return typeof url === 'string' && url.startsWith('https://') ? url : null;
  }
}

/** Deployment records. Each comes from GitHub Actions, and links back to its run. */
@Component({
  selector: 'co-deployment-table',
  imports: [RouterLink, TimeText, OutcomeBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="table-scroll">
      <table class="data">
        <caption class="visually-hidden">
          Deployments, newest first
        </caption>
        <thead>
          <tr>
            <th scope="col">Outcome</th>
            @if (showApplication()) {
              <th scope="col">Application</th>
            }
            <th scope="col">Run</th>
            <th scope="col" class="low">Commit</th>
            <th scope="col" class="low">Title</th>
            <th scope="col" class="low">Branch</th>
            <th scope="col" class="low">Actor</th>
            <th scope="col" class="num low">Duration</th>
            <th scope="col">Started</th>
          </tr>
        </thead>
        <tbody>
          @for (deployment of items(); track deployment.id) {
            <tr>
              <td><co-outcome [outcome]="deployment.outcome" /></td>
              @if (showApplication()) {
                <td>
                  <a [routerLink]="['/applications', deployment.application_slug]">{{
                    deployment.application_name
                  }}</a>
                </td>
              }
              <td class="mono">
                @if (deployment.url; as url) {
                  <a [href]="url" target="_blank" rel="noopener">{{ runLabel(deployment) }}</a>
                } @else {
                  {{ runLabel(deployment) }}
                }
              </td>
              <td class="low mono">{{ shortSha(deployment.commit_sha) }}</td>
              <td class="low">{{ deployment.title }}</td>
              <td class="low mono">{{ deployment.branch }}</td>
              <td class="low">{{ deployment.actor }}</td>
              <td class="num low">
                {{
                  deployment.duration_seconds === null
                    ? '—'
                    : formatDuration(deployment.duration_seconds)
                }}
              </td>
              <td>
                <co-time [at]="deployment.started_at ?? deployment.queued_at" />
              </td>
            </tr>
          }
        </tbody>
      </table>
    </div>
  `,
})
export class DeploymentTable {
  readonly items = input.required<readonly Deployment[]>();
  readonly showApplication = input(true);

  protected readonly shortSha = shortSha;
  protected readonly formatDuration = formatDuration;

  protected runLabel(deployment: Deployment): string {
    const number = deployment.run_number ? `#${deployment.run_number}` : deployment.external_id;
    return deployment.attempt > 1 ? `${number} (attempt ${deployment.attempt})` : number;
  }
}
