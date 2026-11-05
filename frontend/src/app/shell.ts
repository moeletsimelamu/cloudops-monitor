import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import {
  ActivatedRoute,
  NavigationEnd,
  Router,
  RouterLink,
  RouterLinkActive,
  RouterOutlet,
} from '@angular/router';
import { filter, map, startWith } from 'rxjs';

import { ApiService } from './core/api.service';
import { AuthService } from './core/auth.service';
import { poll } from './core/poll';
import {
  ClockService,
  ThemeService,
  TIME_WINDOWS,
  TimeRangeService,
} from './core/preferences.service';

const NAVIGATION = [
  { path: '/overview', label: 'Overview', icon: 'M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z' },
  { path: '/applications', label: 'Applications', icon: 'M2 3h12M2 8h12M2 13h12' },
  { path: '/health', label: 'Health', icon: 'M1 9h3l2-5 3 8 2-3h4' },
  { path: '/deployments', label: 'Deployments', icon: 'M8 14V3M4 7l4-4 4 4' },
  { path: '/logs', label: 'Logs', icon: 'M2 3h8M2 6.5h12M2 10h6M2 13.5h10' },
  { path: '/events', label: 'Events', icon: 'M4 2v12M4 4h9M4 9h6' },
  { path: '/system', label: 'System', icon: 'M2 3h12v4H2zM2 9h12v4H2zM5 5h.01M5 11h.01' },
];

@Component({
  selector: 'co-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './shell.html',
  styleUrl: './shell.css',
})
export class Shell {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly api = inject(ApiService);
  protected readonly auth = inject(AuthService);
  protected readonly clock = inject(ClockService);
  protected readonly theme = inject(ThemeService);
  protected readonly timeRange = inject(TimeRangeService);

  protected readonly navigation = NAVIGATION;
  protected readonly windows = TIME_WINDOWS;
  protected readonly menuOpen = signal(false);

  /** A count on the Overview link, so a problem is visible from any page. */
  private readonly openIncidents = poll(
    () => true,
    () => this.api.allIncidents({ status: 'open', limit: 50 }),
  );
  protected readonly openIncidentCount = computed(
    () => this.openIncidents.data()?.items.length ?? 0,
  );

  private readonly routeData = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      startWith(null),
      map(() => {
        let current = this.route;
        while (current.firstChild) {
          current = current.firstChild;
        }
        // A child route has no snapshot yet while the router is still activating it.
        return (current.snapshot?.data ?? {}) as Record<string, unknown>;
      }),
    ),
    { initialValue: {} as Record<string, unknown> },
  );
  protected readonly heading = computed(() => String(this.routeData()['heading'] ?? ''));
  protected readonly showRange = computed(() => !this.routeData()['hideRange']);

  protected readonly themeLabel = computed(() => {
    const choice = this.theme.choice();
    return choice === 'system'
      ? 'Theme: system'
      : choice === 'light'
        ? 'Theme: light'
        : 'Theme: dark';
  });

  protected async signOut(): Promise<void> {
    await this.auth.signOut();
    await this.router.navigate(['/sign-in']);
  }

  protected skipToContent(event: Event): void {
    event.preventDefault();
    document.getElementById('main')?.focus();
  }
}
