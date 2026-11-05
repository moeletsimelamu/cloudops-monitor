import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { map } from 'rxjs';

import { ApiService } from '../core/api.service';
import { Outcome } from '../core/models';
import { pagedPoll } from '../core/paged';
import { poll } from '../core/poll';
import { DeploymentTable } from '../shared/records';
import { EmptyState, ErrorPanel, Skeleton, TimeText } from '../shared/states';
import { Glyph } from '../shared/status';

const OUTCOMES: { value: Outcome; label: string }[] = [
  { value: 'succeeded', label: 'Succeeded' },
  { value: 'failed', label: 'Failed' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'queued', label: 'Queued' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'needs_attention', label: 'Needs attention' },
];

/** What shipped and whether it worked. Records come only from GitHub Actions. */
@Component({
  selector: 'co-deployments',
  imports: [
    FormsModule,
    RouterLink,
    DeploymentTable,
    EmptyState,
    ErrorPanel,
    Skeleton,
    TimeText,
    Glyph,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <section class="section" aria-label="Deployments">
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
            Outcome
            <select [ngModel]="outcome()" (ngModelChange)="outcome.set($event)">
              <option value="">All</option>
              @for (option of outcomes; track option.value) {
                <option [value]="option.value">{{ option.label }}</option>
              }
            </select>
          </label>
          <label class="field">
            Branch
            <input
              [ngModel]="branch()"
              (ngModelChange)="branch.set($event)"
              [ngModelOptions]="{ updateOn: 'blur' }"
              (keydown.enter)="$any($event.target).blur()"
              placeholder="main"
            />
          </label>
        </div>
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
          @if (hasFilters()) {
            <co-empty title="No deployments match these filters">
              <button class="btn" type="button" (click)="clearFilters()">Clear filters</button>
            </co-empty>
          } @else if (sources.data()?.sources?.length === 0) {
            <co-empty title="No application has a deployment source">
              Edit an <a routerLink="/applications">application</a> and add a GitHub repository and
              workflow to record its deployments.
            </co-empty>
          } @else {
            <co-empty title="No deployment records yet">
              Records are read from GitHub Actions on the schedule below. If a source shows a
              problem, it is listed under Sources.
            </co-empty>
          }
        } @else {
          <co-deployment-table [items]="list.items()" />
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
      </section>

      <section class="section" aria-labelledby="sources-heading">
        <div class="section-head">
          <h2 id="sources-heading">Sources</h2>
          @if (sources.data(); as data) {
            <span class="note">
              Read from GitHub every {{ data.sync_interval_seconds / 60 }} min,
              {{ data.token_configured ? 'with a token' : 'without a token' }}.
              @if (data.rate_limit_remaining !== null) {
                {{ data.rate_limit_remaining }} requests left this hour.
              }
            </span>
          }
        </div>
        @if (sources.error(); as failure) {
          <co-error
            what="Sources could not be loaded"
            [error]="failure"
            (retry)="sources.reload()"
          />
        } @else if (sources.data(); as data) {
          @if (data.sources.length === 0) {
            <co-empty title="No sources are configured" />
          } @else {
            <div class="table-scroll">
              <table class="data">
                <thead>
                  <tr>
                    <th scope="col">Application</th>
                    <th scope="col">Repository and workflow</th>
                    <th scope="col" class="low">Last success</th>
                    <th scope="col">Sync</th>
                  </tr>
                </thead>
                <tbody>
                  @for (source of data.sources; track source.application_slug) {
                    <tr>
                      <td>
                        <a [routerLink]="['/applications', source.application_slug]">{{
                          source.application_name
                        }}</a>
                      </td>
                      <td class="mono">
                        {{ source.github_repo }}, {{ source.deploy_workflow }}
                        @if (source.deploy_branch) {
                          , {{ source.deploy_branch }}
                        }
                      </td>
                      <td class="low"><co-time [at]="source.last_success_at" none="never" /></td>
                      <td>
                        @if (source.last_error_kind) {
                          <co-glyph shape="triangle" tone="warn" />
                          {{ source.last_error_message }}
                        } @else if (source.last_success_at) {
                          <co-glyph shape="circle" tone="ok" /> Working
                        } @else {
                          <span class="muted">Not run yet</span>
                        }
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
})
export class DeploymentsPage {
  private readonly api = inject(ApiService);

  protected readonly outcomes = OUTCOMES;
  protected readonly application = signal('');
  protected readonly outcome = signal('');
  protected readonly branch = signal('');

  protected readonly hasFilters = computed(
    () => !!(this.application() || this.outcome() || this.branch()),
  );

  private readonly key = computed(() => ({
    application: this.application(),
    outcome: this.outcome(),
    branch: this.branch().trim(),
  }));

  // Not limited by time: deployments are rare, so the question is "what shipped last", however
  // long ago that was. Older records are one click away.
  protected readonly list = pagedPoll(this.key, (key, cursor) =>
    this.api.deployments({
      application: key.application,
      outcome: key.outcome,
      branch: key.branch,
      limit: 50,
      cursor,
    }),
  );

  protected readonly sources = poll(
    () => true,
    () => this.api.deploymentSources(),
  );

  private readonly applications = poll(
    () => true,
    () => this.api.applications().pipe(map((page) => page.items)),
    60_000,
  );

  protected readonly applicationOptions = computed(() => this.applications.data() ?? []);

  protected clearFilters(): void {
    this.application.set('');
    this.outcome.set('');
    this.branch.set('');
  }
}
