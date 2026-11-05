import { DOCUMENT, Injectable, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';

import { TimeWindow } from './models';

export const TIME_WINDOWS: readonly TimeWindow[] = ['1h', '6h', '24h', '7d'];

const WINDOW_MS: Record<TimeWindow, number> = {
  '1h': 3_600_000,
  '6h': 21_600_000,
  '24h': 86_400_000,
  '7d': 604_800_000,
};

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // private windows and blocked storage
  }
}

function store(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* a preference that does not persist is fine */
  }
}

/** The ticking clock behind "34 s ago", and whether times are shown in UTC or local time. */
@Injectable({ providedIn: 'root' })
export class ClockService {
  readonly now = signal(Date.now());
  readonly utc = signal(stored('cloudops.utc') === 'true');

  constructor() {
    setInterval(() => this.now.set(Date.now()), 5000);
  }

  toggleUtc(): void {
    this.utc.update((value) => !value);
    store('cloudops.utc', String(this.utc()));
  }
}

export type ThemeChoice = 'system' | 'light' | 'dark';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly root = inject(DOCUMENT).documentElement;
  readonly choice = signal<ThemeChoice>((stored('cloudops.theme') as ThemeChoice) ?? 'system');

  constructor() {
    this.apply();
  }

  /** System, then light, then dark. */
  cycle(): void {
    const order: ThemeChoice[] = ['system', 'light', 'dark'];
    this.choice.set(order[(order.indexOf(this.choice()) + 1) % order.length]);
    store('cloudops.theme', this.choice());
    this.apply();
  }

  private apply(): void {
    if (this.choice() === 'system') {
      this.root.removeAttribute('data-theme');
    } else {
      this.root.setAttribute('data-theme', this.choice());
    }
  }
}

/** The global time range. It lives in the URL, so a link shows the same period to someone else. */
@Injectable({ providedIn: 'root' })
export class TimeRangeService {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  readonly window = signal<TimeWindow>('24h');
  readonly label = computed(() => this.window());

  constructor() {
    this.router.events.pipe(filter((event) => event instanceof NavigationEnd)).subscribe(() => {
      const value = this.router.parseUrl(this.router.url).queryParams['window'];
      this.window.set(TIME_WINDOWS.includes(value) ? (value as TimeWindow) : '24h');
    });
  }

  set(window: TimeWindow): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { window },
      queryParamsHandling: 'merge',
    });
  }

  /** The window as an API range, ending now. */
  range(window: TimeWindow = this.window()): { from: string; to: string } {
    const end = Date.now();
    return {
      from: new Date(end - WINDOW_MS[window]).toISOString(),
      to: new Date(end).toISOString(),
    };
  }
}
