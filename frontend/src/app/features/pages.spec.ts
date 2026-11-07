import { HttpTestingController, TestRequest } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';

import { AuthService } from '../core/auth.service';
import {
  TEST_PROVIDERS,
  application,
  deployment,
  fleetEntry,
  logEntry,
  operationalEvent,
  overview,
} from '../../testing/fixtures';
import { ApplicationDetailPage } from './application-detail';
import { ApplicationForm, slugFromName } from './application-form';
import { ApplicationsPage } from './applications';
import { DeploymentsPage } from './deployments';
import { EventsPage } from './events';
import { LogsPage } from './logs';
import { OverviewPage } from './overview';
import { SignInPage } from './sign-in';
import { SystemPage } from './system';

type Http = HttpTestingController;

function http(): Http {
  return TestBed.inject(HttpTestingController);
}

/** Answer every outstanding request whose path matches. */
function answer(path: string, body: unknown): TestRequest[] {
  const requests = http().match((request) => request.url === path);
  for (const request of requests) {
    request.flush(body as never);
  }
  return requests;
}

function fail(path: string, status = 500): void {
  for (const request of http().match((item) => item.url === path)) {
    request.flush(
      { error: { code: 'internal_error', message: 'The server failed.', request_id: 'req-7' } },
      { status, statusText: 'Error' },
    );
  }
}

function text(element: HTMLElement): string {
  return (element.textContent ?? '').replace(/\s+/g, ' ');
}

const EMPTY_PAGE = { items: [], next_cursor: null };
const APP_LIST = { items: [], generated_at: '2026-11-03T14:00:00Z' };

beforeEach(() => {
  TestBed.configureTestingModule({ providers: TEST_PROVIDERS });
});

describe('OverviewPage', () => {
  async function render(): Promise<{ element: HTMLElement; settle: () => Promise<void> }> {
    const fixture = TestBed.createComponent(OverviewPage);
    await fixture.whenStable();
    return { element: fixture.nativeElement, settle: () => fixture.whenStable() };
  }

  it('lists problems worst first with the deployment before them', async () => {
    const { element, settle } = await render();
    const degraded = application({
      slug: 'orders-api',
      status: 'degraded',
      health_state: 'degraded',
      open_incident: {
        id: 1,
        opened_at: '2026-11-03T13:50:00Z',
        closed_at: null,
        worst_state: 'degraded',
        opening_reason: 'server_error',
        duration_seconds: null,
        preceding_deployment: {
          id: 7,
          run_number: 57,
          commit_sha: 'a1b2c3d',
          outcome: 'succeeded',
          url: 'https://github.com/x',
          relation: 'completed_before',
          minutes_before: 4,
          summary: 'Incident opened 4 min after deployment #57 (a1b2c3d) finished (succeeded)',
        },
      },
    });
    answer(
      '/api/overview',
      overview({
        counts: {
          total: 1,
          healthy: 0,
          degraded: 1,
          unavailable: 0,
          stale: 0,
          pending: 0,
          paused: 0,
        },
        needs_attention: [fleetEntry(degraded)],
        fleet: [fleetEntry(degraded)],
        recent_changes: [operationalEvent()],
      }),
    );
    await settle();
    expect(text(element)).toContain('Needs attention');
    expect(text(element)).toContain('Orders API');
    expect(text(element)).toContain('Degraded');
    expect(text(element)).toContain('Incident opened 4 min after deployment #57');
    expect(text(element)).toContain('98.00%');
    expect(text(element)).toContain('212 ms');
    expect(text(element)).toContain('is degraded: HTTP 500');
  });

  it('says plainly when nothing is registered', async () => {
    const { element, settle } = await render();
    answer('/api/overview', overview());
    await settle();
    expect(text(element)).toContain('No applications are being monitored yet');
    expect(element.querySelector('table')).toBeNull();
  });

  it('shows no percentage when there is too little data', async () => {
    const { element, settle } = await render();
    const entry = fleetEntry();
    entry.availability_24h = {
      ...entry.availability_24h,
      availability: null,
      insufficient_reason: 'Fewer than 10 checks in this window',
    };
    answer(
      '/api/overview',
      overview({
        counts: {
          total: 1,
          healthy: 1,
          degraded: 0,
          unavailable: 0,
          stale: 0,
          pending: 0,
          paused: 0,
        },
        fleet: [entry],
      }),
    );
    await settle();
    expect(text(element)).not.toContain('98.00%');
    expect(element.querySelector('td[title="Fewer than 10 checks in this window"]')).not.toBeNull();
  });

  it('keeps showing an API failure with its request ID', async () => {
    const { element, settle } = await render();
    fail('/api/overview');
    await settle();
    expect(text(element)).toContain('The overview could not be refreshed');
    expect(text(element)).toContain('req-7');
    expect(element.querySelector('[role="alert"]')).not.toBeNull();
  });
});

describe('ApplicationsPage', () => {
  async function render(): Promise<{ element: HTMLElement; settle: () => Promise<void> }> {
    const fixture = TestBed.createComponent(ApplicationsPage);
    await fixture.whenStable();
    return { element: fixture.nativeElement, settle: () => fixture.whenStable() };
  }

  const rows = (element: HTMLElement): string[] =>
    [...element.querySelectorAll('tbody tr')].map((row) => text(row as HTMLElement));

  it('renders the registry with problems first', async () => {
    const { element, settle } = await render();
    answer('/api/applications', {
      generated_at: '2026-11-03T14:00:00Z',
      items: [
        application({ id: 1, name: 'Billing', slug: 'billing', status: 'healthy' }),
        application({ id: 2, name: 'Orders', slug: 'orders', status: 'unavailable' }),
      ],
    });
    await settle();
    expect(rows(element)[0]).toContain('Orders');
    expect(rows(element)[0]).toContain('Unavailable');
    expect(element.querySelector('th[aria-sort="ascending"]')?.textContent).toContain('Status');
  });

  it('sorts by a column, and says so to assistive technology', async () => {
    const { element, settle } = await render();
    answer('/api/applications', {
      generated_at: '2026-11-03T14:00:00Z',
      items: [
        application({ id: 1, name: 'Billing', slug: 'billing' }),
        application({ id: 2, name: 'Orders', slug: 'orders' }),
      ],
    });
    await settle();
    const nameButton = [...element.querySelectorAll('th button')].find((button) =>
      button.textContent?.includes('Name'),
    ) as HTMLButtonElement;
    nameButton.click();
    await settle();
    nameButton.click();
    await settle();
    expect(rows(element)[0]).toContain('Orders');
    expect(element.querySelector('th[aria-sort="descending"]')?.textContent).toContain('Name');
  });

  it('explains an empty registry, and an empty filter result differently', async () => {
    const { element, settle } = await render();
    answer('/api/applications', APP_LIST);
    await settle();
    expect(text(element)).toContain('No applications are registered');
  });

  it('shows API failures', async () => {
    const { element, settle } = await render();
    fail('/api/applications');
    await settle();
    expect(text(element)).toContain('The applications could not be loaded');
  });
});

describe('ApplicationForm', () => {
  async function render(): Promise<{
    element: HTMLElement;
    settle: () => Promise<void>;
    submit: () => void;
    button: () => HTMLButtonElement;
  }> {
    const fixture = TestBed.createComponent(ApplicationForm);
    await fixture.whenStable();
    answer('/api/system/config', {
      target_policy: 'allowlist',
      allowed_targets: ['localhost:8310'],
    });
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    return {
      element,
      settle: () => fixture.whenStable(),
      submit: () => element.querySelector('form')?.dispatchEvent(new Event('submit')),
      button: () => element.querySelector('button[type="submit"]') as HTMLButtonElement,
    };
  }

  it('suggests a slug from the name', () => {
    expect(slugFromName('Orders API (EU)')).toBe('orders-api-eu');
    expect(slugFromName('  --  ')).toBe('');
  });

  it('shows the operator-configured targets next to the URL', async () => {
    const { element } = await render();
    expect(text(element)).toContain('localhost:8310');
  });

  it('disables submitting while a request is pending, so it cannot be sent twice', async () => {
    const { button, settle, submit } = await render();
    expect(button().disabled).toBe(false);
    submit();
    await settle();
    expect(button().disabled).toBe(true);
    expect(button().textContent).toContain('Saving');
    submit();
    expect(http().match('/api/applications')).toHaveLength(1);
  });

  it('puts server validation next to the field, without leaving the form', async () => {
    const { element, settle, submit, button } = await render();
    submit();
    await settle();
    http()
      .expectOne('/api/applications')
      .flush(
        {
          error: {
            code: 'target_not_allowed',
            message: 'not allowed',
            request_id: 'r',
            details: [
              { location: ['body', 'check_url'], message: 'This host is not in the allowlist.' },
            ],
          },
        },
        { status: 422, statusText: 'Invalid' },
      );
    await settle();
    await settle();
    expect(element.querySelector('#check-url-error')?.textContent).toContain(
      'not in the allowlist',
    );
    expect(element.querySelector('#check_url')?.getAttribute('aria-invalid')).toBe('true');
    expect(button().disabled).toBe(false);
  });

  it('shows rules that span several fields above the form', async () => {
    const { element, settle, submit } = await render();
    submit();
    await settle();
    http()
      .expectOne('/api/applications')
      .flush(
        {
          error: {
            code: 'invalid_request',
            message: 'The request is invalid.',
            request_id: 'r',
            details: [
              {
                location: ['body'],
                message: 'Value error, timeout_seconds must be shorter than interval_seconds.',
              },
            ],
          },
        },
        { status: 422, statusText: 'Invalid' },
      );
    await settle();
    await settle();
    expect(text(element)).toContain('timeout_seconds must be shorter than interval_seconds.');
    expect(text(element)).not.toContain('Value error');
  });
});

describe('DeploymentsPage', () => {
  async function render(): Promise<{ element: HTMLElement; settle: () => Promise<void> }> {
    const fixture = TestBed.createComponent(DeploymentsPage);
    await fixture.whenStable();
    answer('/api/applications', APP_LIST);
    answer('/api/deployments/sources', {
      token_configured: false,
      sync_interval_seconds: 300,
      rate_limit_remaining: 58,
      rate_limit_reset_at: null,
      last_run_at: null,
      sources: [],
    });
    await fixture.whenStable();
    return { element: fixture.nativeElement, settle: () => fixture.whenStable() };
  }

  it('shows records with links back to GitHub', async () => {
    const { element, settle } = await render();
    answer('/api/deployments', { items: [deployment()], next_cursor: null });
    await settle();
    expect(text(element)).toContain('#57');
    expect(text(element)).toContain('a1b2c3d');
    expect(text(element)).toContain('Succeeded');
    expect(element.querySelector('a[href^="https://github.com"]')).not.toBeNull();
    expect(text(element)).toContain('58 requests left');
  });

  it('sends the outcome filter to the API', async () => {
    const { element, settle } = await render();
    answer('/api/deployments', { items: [deployment()], next_cursor: null });
    await settle();
    const select = element.querySelectorAll('select')[1];
    select.value = 'failed';
    select.dispatchEvent(new Event('change'));
    await settle();
    const [request] = http().match((item) => item.url === '/api/deployments');
    expect(request.request.params.get('outcome')).toBe('failed');
    request.flush(EMPTY_PAGE);
    await settle();
    expect(text(element)).toContain('No deployments match these filters');
  });

  it('does not hide older deployments behind a time range', async () => {
    const { settle } = await render();
    const [request] = http().match((item) => item.url === '/api/deployments');
    expect(request.request.params.has('from')).toBe(false);
    expect(request.request.params.has('to')).toBe(false);
    request.flush(EMPTY_PAGE);
    await settle();
  });

  it('explains when no application has a deployment source', async () => {
    const { element, settle } = await render();
    answer('/api/deployments', EMPTY_PAGE);
    await settle();
    expect(text(element)).toContain('No application has a deployment source');
  });

  it('loads older pages on request', async () => {
    const { element, settle } = await render();
    answer('/api/deployments', { items: [deployment()], next_cursor: 'abc' });
    await settle();
    const more = [...element.querySelectorAll('button')].find((button) =>
      button.textContent?.includes('Load older'),
    ) as HTMLButtonElement;
    more.click();
    const [request] = http().match((item) => item.url === '/api/deployments');
    expect(request.request.params.get('cursor')).toBe('abc');
    request.flush({ items: [deployment({ id: 6, run_number: 56 })], next_cursor: null });
    await settle();
    expect(text(element)).toContain('#56');
    expect(text(element)).not.toContain('Load older');
  });
});

describe('LogsPage', () => {
  it('reads its filters from the URL and sends them to the API', async () => {
    const fixture = TestBed.createComponent(LogsPage);
    fixture.componentRef.setInput('application', 'orders-api');
    fixture.componentRef.setInput('min_severity', 'error');
    fixture.componentRef.setInput('correlation_id', 'req-1');
    fixture.componentRef.setInput('from', '2026-11-03T13:40:00.000Z');
    fixture.componentRef.setInput('to', '2026-11-03T14:00:00.000Z');
    await fixture.whenStable();
    answer('/api/applications', APP_LIST);
    const [request] = http().match((item) => item.url === '/api/logs');
    const params = request.request.params;
    expect(params.get('application')).toBe('orders-api');
    expect(params.get('min_severity')).toBe('error');
    expect(params.get('correlation_id')).toBe('req-1');
    expect(params.get('from')).toBe('2026-11-03T13:40:00.000Z');
    request.flush({ items: [logEntry()], next_cursor: null });
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    expect(text(element)).toContain('Could not load orders');
    expect(text(element)).toContain('order.load_failed');
  });

  it('expands a row to show its attributes', async () => {
    const fixture = TestBed.createComponent(LogsPage);
    await fixture.whenStable();
    answer('/api/applications', APP_LIST);
    answer('/api/logs', { items: [logEntry()], next_cursor: null });
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    expect(text(element)).not.toContain('timeout_ms');
    const row = element.querySelector('button.row') as HTMLButtonElement;
    row.click();
    await fixture.whenStable();
    expect(row.getAttribute('aria-expanded')).toBe('true');
    expect(text(element)).toContain('timeout_ms');
  });

  it('tells empty results apart from filtered ones', async () => {
    const fixture = TestBed.createComponent(LogsPage);
    await fixture.whenStable();
    answer('/api/applications', APP_LIST);
    answer('/api/logs', EMPTY_PAGE);
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    expect(text(element)).toContain('No log entries in this time range');

    fixture.componentRef.setInput('q', 'timeout');
    await fixture.whenStable();
    answer('/api/logs', EMPTY_PAGE);
    await fixture.whenStable();
    expect(text(element)).toContain('No log entries match these filters');
  });

  it('shows API failures with a retry', async () => {
    const fixture = TestBed.createComponent(LogsPage);
    await fixture.whenStable();
    answer('/api/applications', APP_LIST);
    fail('/api/logs');
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    expect(text(element)).toContain('Logs could not be loaded');
    (element.querySelector('[role="alert"] button') as HTMLButtonElement).click();
    expect(http().match((item) => item.url === '/api/logs')).toHaveLength(1);
  });
});

describe('EventsPage', () => {
  it('groups events by day and filters by kind', async () => {
    const fixture = TestBed.createComponent(EventsPage);
    await fixture.whenStable();
    answer('/api/applications', APP_LIST);
    answer('/api/events', { items: [operationalEvent()], next_cursor: null });
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    expect(element.querySelector('h2.day')?.textContent).toContain('2026-11-03');
    expect(text(element)).toContain('Logs ±10 min');

    const kind = element.querySelectorAll('select')[1];
    kind.value = 'health';
    kind.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    const [request] = http().match((item) => item.url === '/api/events');
    expect(request.request.params.get('category')).toBe('health');
    request.flush(EMPTY_PAGE);
  });
});

describe('ApplicationDetailPage', () => {
  async function render(): Promise<{ element: HTMLElement; settle: () => Promise<void> }> {
    const fixture = TestBed.createComponent(ApplicationDetailPage);
    fixture.componentRef.setInput('slug', 'orders-api');
    await fixture.whenStable();
    return { element: fixture.nativeElement, settle: () => fixture.whenStable() };
  }

  const answerPanels = (): void => {
    answer('/api/applications/orders-api/metrics', {
      window: '24h',
      window_start: '2026-11-02T14:00:00Z',
      window_end: '2026-11-03T14:00:00Z',
      bucket_seconds: 900,
      availability: {
        checks: 0,
        ok: 0,
        warn: 0,
        fail: 0,
        expected_checks: 0,
        coverage: null,
        availability: null,
        insufficient_reason: 'Fewer than 10 checks in this window',
      },
      p50_ms: null,
      p95_ms: null,
      buckets: [],
      definitions: { availability: 'Not uptime.', coverage: 'c', latency: 'l' },
    });
    answer('/api/applications/orders-api/request-metrics', {
      has_data: false,
      buckets: [],
    });
    answer('/api/applications/orders-api/incidents', { items: [] });
    answer('/api/deployments', EMPTY_PAGE);
  };

  it('loads the application with its evidence, and says when there is none yet', async () => {
    const { element, settle } = await render();
    answer('/api/applications/orders-api', application());
    answerPanels();
    await settle();
    expect(text(element)).toContain('Orders API');
    expect(text(element)).toContain('Healthy');
    expect(text(element)).toContain('No checks in the last 24h');
    expect(text(element)).toContain('No availability figure yet');
  });

  it('shows an unknown application as missing, not as a crash', async () => {
    const { element, settle } = await render();
    fail('/api/applications/orders-api', 404);
    answerPanels();
    await settle();
    expect(text(element)).toContain('There is no application called orders-api');
  });

  it('stops double checks while one is running, and announces the result', async () => {
    const { element, settle } = await render();
    answer('/api/applications/orders-api', application());
    answerPanels();
    await settle();
    const button = [...element.querySelectorAll('button')].find((item) =>
      item.textContent?.includes('Check now'),
    ) as HTMLButtonElement;
    button.click();
    await settle();
    expect(button.disabled).toBe(true);
    expect(button.textContent).toContain('Checking');
    answer('/api/applications/orders-api/checks', {
      id: 1,
      started_at: '2026-11-03T14:00:00Z',
      duration_ms: 84,
      http_status: 500,
      signal: 'warn',
      reason: 'server_error',
      detail: 'HTTP 500: the application is failing',
      resolved_ip: '127.0.0.1',
      state_after: 'degraded',
    });
    await settle();
    await settle();
    expect(button.disabled).toBe(false);
    expect(element.querySelector('[aria-live="polite"]')?.textContent).toContain(
      'HTTP 500: the application is failing',
    );
  });

  it('shows a clear error when a check cannot run', async () => {
    const { element, settle } = await render();
    answer('/api/applications/orders-api', application());
    answerPanels();
    await settle();
    const button = [...element.querySelectorAll('button')].find((item) =>
      item.textContent?.includes('Check now'),
    ) as HTMLButtonElement;
    button.click();
    await settle();
    http()
      .expectOne('/api/applications/orders-api/checks')
      .flush(
        { error: { code: 'too_many_checks', message: 'Try again in 3 seconds.', request_id: 'r' } },
        { status: 429, statusText: 'Too Many' },
      );
    await settle();
    await settle();
    expect(text(element)).toContain('Try again in 3 seconds.');
  });
});

describe('SignInPage', () => {
  async function render(returnUrl?: string): Promise<{
    element: HTMLElement;
    settle: () => Promise<void>;
  }> {
    const fixture = TestBed.createComponent(SignInPage);
    if (returnUrl) {
      fixture.componentRef.setInput('returnUrl', returnUrl);
    }
    await fixture.whenStable();
    answer('/api/auth/session', { auth_mode: 'token', authenticated: false, expires_at: null });
    await fixture.whenStable();
    return { element: fixture.nativeElement, settle: () => fixture.whenStable() };
  }

  it('says this is a single operator token, and reports a wrong one', async () => {
    const { element, settle } = await render();
    expect(text(element)).toContain('one operator');
    const input = element.querySelector('input') as HTMLInputElement;
    input.value = 'wrong';
    input.dispatchEvent(new Event('input'));
    await settle();
    element.querySelector('form')?.dispatchEvent(new Event('submit'));
    await settle();
    http()
      .expectOne('/api/auth/session')
      .flush(
        {
          error: { code: 'invalid_token', message: 'That token is not correct.', request_id: 'r' },
        },
        { status: 401, statusText: 'Unauthorized' },
      );
    // The rejection passes through the auth service first, so let the promises finish.
    await new Promise((resolve) => setTimeout(resolve));
    await settle();
    expect(element.querySelector('#token-error')?.textContent).toContain('not correct');
    expect(input.getAttribute('aria-invalid')).toBe('true');
  });

  it('never redirects to another site after signing in', async () => {
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const { element, settle } = await render('//attacker.example/steal');
    const input = element.querySelector('input') as HTMLInputElement;
    input.value = 'the-token';
    input.dispatchEvent(new Event('input'));
    await settle();
    element.querySelector('form')?.dispatchEvent(new Event('submit'));
    await settle();
    http()
      .expectOne('/api/auth/session')
      .flush(
        { auth_mode: 'token', authenticated: true, expires_at: null },
        { status: 201, statusText: 'Created' },
      );
    await settle();
    await settle();
    expect(navigate).toHaveBeenCalledWith('/overview');
  });
});

describe('SystemPage: sign out everywhere', () => {
  async function render(
    authMode: 'token' | 'disabled',
  ): Promise<{ element: HTMLElement; settle: () => Promise<void> }> {
    TestBed.inject(AuthService).session.set({
      auth_mode: authMode,
      authenticated: true,
      expires_at: null,
    });
    const fixture = TestBed.createComponent(SystemPage);
    await fixture.whenStable();
    return { element: fixture.nativeElement, settle: () => fixture.whenStable() };
  }

  function signOutButton(element: HTMLElement): HTMLButtonElement | undefined {
    return Array.from(element.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Sign out everywhere'),
    );
  }

  it('asks first, then ends every session and goes to the sign-in page', async () => {
    const { element, settle } = await render('token');
    const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const dialog = element.querySelector('co-confirm dialog') as HTMLDialogElement;
    dialog.showModal = vi.fn();
    dialog.close = vi.fn();

    signOutButton(element)?.click();
    expect(dialog.showModal).toHaveBeenCalled();
    expect(http().match('/api/auth/sessions')).toHaveLength(0); // nothing until confirmed

    const confirm = Array.from(dialog.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Sign out everywhere'),
    ) as HTMLButtonElement;
    confirm.click();
    const request = http().expectOne('/api/auth/sessions');
    expect(request.request.method).toBe('DELETE');
    request.flush({ ended: 2 });
    await settle();
    expect(TestBed.inject(AuthService).signedIn()).toBe(false);
    expect(navigate).toHaveBeenCalledWith(['/sign-in']);
  });

  it('is not offered when authentication is disabled', async () => {
    const { element } = await render('disabled');
    expect(signOutButton(element)).toBeUndefined();
  });
});
