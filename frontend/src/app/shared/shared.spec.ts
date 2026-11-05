import { HttpErrorResponse } from '@angular/common/http';
import { ComponentRef } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { TEST_PROVIDERS, bucket, deployment, metrics } from '../../testing/fixtures';
import { UNREACHABLE_CODE, toApiError } from '../core/api.service';
import { DisplayStatus, RequestMetrics } from '../core/models';
import { EvidenceTimeline } from './charts/evidence-timeline';
import { ErrorPanel } from './states';
import { StatusBadge } from './status';

describe('StatusBadge', () => {
  const cases: [DisplayStatus, string, string][] = [
    ['healthy', 'Healthy', 'circle'],
    ['degraded', 'Degraded', 'path'],
    ['unavailable', 'Unavailable', 'rect'],
    ['stale', 'Stale', 'circle[stroke-dasharray]'],
    ['pending', 'Pending', 'circle[stroke]'],
    ['paused', 'Paused', 'rect'],
  ];

  it.each(cases)(
    'shows %s with a word and a shape, not colour alone',
    async (status, word, mark) => {
      const fixture = TestBed.createComponent(StatusBadge);
      fixture.componentRef.setInput('status', status);
      await fixture.whenStable();
      const element: HTMLElement = fixture.nativeElement;
      expect(element.textContent).toContain(word);
      expect(element.querySelector(`svg ${mark}`)).not.toBeNull();
    },
  );
});

describe('toApiError', () => {
  it('uses the message and request ID from the error envelope', () => {
    const error = toApiError(
      new HttpErrorResponse({
        status: 409,
        error: { error: { code: 'conflict', message: 'Already exists.', request_id: 'abc' } },
      }),
    );
    expect(error).toMatchObject({
      status: 409,
      code: 'conflict',
      message: 'Already exists.',
      requestId: 'abc',
    });
  });

  it('explains an unreachable server', () => {
    const error = toApiError(new HttpErrorResponse({ status: 0 }));
    expect(error.code).toBe(UNREACHABLE_CODE);
    expect(error.message).toContain("Can't reach");
  });
});

describe('ErrorPanel', () => {
  it('says what failed, how to recover and which request to quote', async () => {
    const fixture = TestBed.createComponent(ErrorPanel);
    fixture.componentRef.setInput('what', 'Logs could not be loaded');
    fixture.componentRef.setInput('error', {
      status: 500,
      code: 'internal_error',
      message: 'Something went wrong.',
      requestId: 'req-9',
      details: null,
    });
    let retried = false;
    fixture.componentInstance.retry.subscribe(() => (retried = true));
    await fixture.whenStable();
    const element: HTMLElement = fixture.nativeElement;
    expect(element.textContent).toContain('Logs could not be loaded');
    expect(element.textContent).toContain('req-9');
    expect(element.querySelector('[role="alert"]')).not.toBeNull();
    element.querySelector('button')?.click();
    expect(retried).toBe(true);
  });
});

describe('EvidenceTimeline', () => {
  const START = '2026-11-03T14:00:00.000Z';
  const at = (minutes: number): string =>
    new Date(new Date(START).getTime() + minutes * 60_000).toISOString();

  let fixture: ComponentFixture<EvidenceTimeline>;
  let ref: ComponentRef<EvidenceTimeline>;
  let element: HTMLElement;

  async function render(): Promise<void> {
    await fixture.whenStable();
    element = fixture.nativeElement;
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: TEST_PROVIDERS });
    fixture = TestBed.createComponent(EvidenceTimeline);
    ref = fixture.componentRef;
  });

  it('breaks the response-time line where there are no checks', async () => {
    ref.setInput(
      'metrics',
      metrics([
        bucket(at(0)),
        bucket(at(1)),
        bucket(at(2), { checks: 0, worst: null, p50_ms: null, p95_ms: null }),
        bucket(at(3)),
        bucket(at(4)),
      ]),
    );
    await render();
    const path = element.querySelector('path.latency')?.getAttribute('d') ?? '';
    expect(path.match(/M/g)).toHaveLength(2); // two separate segments
  });

  it('draws no mark for a period without checks', async () => {
    ref.setInput(
      'metrics',
      metrics([bucket(at(0), { worst: 'fail' }), bucket(at(1), { checks: 0, worst: null })]),
    );
    await render();
    expect(element.querySelectorAll('svg > rect.fill-fail')).toHaveLength(1);
  });

  it('draws no response-time scale when no response was received', async () => {
    ref.setInput(
      'metrics',
      metrics([
        bucket(at(0), { worst: 'fail', p50_ms: null, p95_ms: null }),
        bucket(at(1), { worst: 'fail', p50_ms: null, p95_ms: null }),
      ]),
    );
    await render();
    expect(element.querySelector('svg')?.textContent).toContain('No responses received');
    expect(element.querySelector('svg')?.textContent).not.toContain('ms');
  });

  it('makes worse results taller, so they read without colour', async () => {
    ref.setInput(
      'metrics',
      metrics([
        bucket(at(0), { worst: 'ok' }),
        bucket(at(1), { worst: 'warn' }),
        bucket(at(2), { worst: 'fail' }),
      ]),
    );
    await render();
    const height = (selector: string): number =>
      Number(element.querySelector(selector)?.getAttribute('height'));
    expect(height('svg > rect.fill-ok')).toBeLessThan(height('svg > rect.fill-warn'));
    expect(height('svg > rect.fill-warn')).toBeLessThan(height('svg > rect.fill-fail'));
  });

  it('shows the request-error row only when the application ships request logs', async () => {
    ref.setInput('metrics', metrics([bucket(at(0))]));
    const empty: RequestMetrics = {
      window: '1h',
      window_start: START,
      window_end: at(60),
      bucket_seconds: 60,
      has_data: false,
      requests: 0,
      errors: 0,
      buckets: [{ start: START, requests: 0, errors: 0 }],
      source: 'x',
    };
    ref.setInput('requests', empty);
    await render();
    expect(element.textContent).not.toContain('5xx');

    ref.setInput('requests', {
      ...empty,
      has_data: true,
      requests: 10,
      errors: 3,
      buckets: [{ start: START, requests: 10, errors: 3 }],
    });
    await render();
    expect(element.textContent).toContain('5xx responses, from request logs');
  });

  it('places incidents and deployments on the axis and describes them for screen readers', async () => {
    ref.setInput('metrics', metrics([bucket(at(0)), bucket(at(1)), bucket(at(2))]));
    ref.setInput('incidents', [
      {
        id: 1,
        opened_at: at(1),
        closed_at: null,
        worst_state: 'unavailable',
        opening_reason: 'timeout',
        duration_seconds: null,
        preceding_deployment: null,
      },
    ]);
    ref.setInput('deployments', [
      deployment({ run_number: 57, completed_at: at(1), started_at: at(0) }),
      deployment({ id: 8, completed_at: '2020-01-01T00:00:00Z' }), // outside the window
    ]);
    await render();
    expect(element.querySelectorAll('line.deploy-rule')).toHaveLength(1);
    expect(element.querySelector('text.deploy-label')?.textContent?.trim()).toBe('#57');
    const label = element.querySelector('svg')?.getAttribute('aria-label') ?? '';
    expect(label).toContain('1 incidents and 1 deployments');
    expect(element.querySelector('rect[fill="url(#hatch-fail)"]')).not.toBeNull();
  });

  it('offers the same data as a table', async () => {
    ref.setInput('metrics', metrics([bucket(at(0), { failed: 1, worst: 'warn' })]));
    await render();
    (element.querySelector('.toolbar button') as HTMLButtonElement).click();
    await render();
    const table = element.querySelector('table');
    expect(table?.textContent).toContain('warn');
    expect(element.querySelector('svg[role="img"]')).toBeNull();
  });

  it('can be read with the keyboard, and Enter selects the period', async () => {
    ref.setInput('metrics', metrics([bucket(at(0)), bucket(at(1))]));
    const selections: { from: string; to: string }[] = [];
    fixture.componentInstance.selected.subscribe((value) => selections.push(value));
    await render();
    const svg = element.querySelector('svg[role="img"]') as SVGElement;
    svg.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));
    await render();
    expect(element.querySelector('.tooltip')?.textContent).toContain('checks');
    svg.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(selections).toHaveLength(1);
    expect(selections[0].to > selections[0].from).toBe(true);
  });
});
