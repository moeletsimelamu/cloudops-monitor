import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { TimeRangeService } from './core/preferences.service';
import { Shell } from './shell';

@Component({ template: '<p>page content</p>' })
class Blank {}

describe('Shell', () => {
  async function render(url: string): Promise<{
    element: HTMLElement;
    harness: RouterTestingHarness;
    http: HttpTestingController;
  }> {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        // Going through the real router matters: the shell is created while its child route is
        // still being activated, which once crashed every page.
        provideRouter([
          {
            path: '',
            component: Shell,
            children: [
              { path: 'overview', component: Blank, data: { heading: 'Overview' } },
              { path: 'system', component: Blank, data: { heading: 'System', hideRange: true } },
            ],
          },
        ]),
      ],
    });
    const harness = await RouterTestingHarness.create(url);
    return {
      harness,
      element: harness.routeNativeElement as HTMLElement,
      http: TestBed.inject(HttpTestingController),
    };
  }

  it('renders the page heading and the persistent navigation', async () => {
    const { harness } = await render('/overview');
    await harness.fixture.whenStable();
    expect(document.querySelector('h1')?.textContent).toContain('Overview');
    const links = [...document.querySelectorAll('nav a')].map((a) => a.textContent?.trim());
    expect(links).toEqual([
      'Overview',
      'Applications',
      'Health',
      'Deployments',
      'Logs',
      'Events',
      'System',
    ]);
  });

  it('labels its controls, which also confirms the template rendered', async () => {
    await render('/overview');
    const labels = [...document.querySelectorAll('.controls button')].map((b) =>
      b.textContent?.trim(),
    );
    expect(labels).toEqual(['1h', '6h', '24h', '7d', 'Times: local', 'Theme: system']);
    expect(document.querySelector('[aria-pressed="true"]')?.textContent).toContain('24h');
  });

  it('hides the time range on pages it does not apply to', async () => {
    await render('/system');
    expect(document.querySelector('h1')?.textContent).toContain('System');
    expect(document.querySelector('[aria-label="Time range"]')).toBeNull();
  });

  it('shows open incidents on the Overview link', async () => {
    const { harness, http } = await render('/overview');
    http
      .expectOne((request) => request.url === '/api/incidents')
      .flush({ items: [{ id: 1 }, { id: 2 }], next_cursor: null });
    await harness.fixture.whenStable();
    expect(document.querySelector('.count')?.textContent).toContain('2');
  });

  it('puts the time range in the URL, so a link shows the same period', async () => {
    const { harness } = await render('/overview');
    TestBed.inject(TimeRangeService).set('7d');
    await harness.fixture.whenStable();
    expect(TestBed.inject(Router).url).toContain('window=7d');
    expect(document.querySelector('[aria-pressed="true"]')?.textContent).toContain('7d');
  });
});
