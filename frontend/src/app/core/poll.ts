import { DestroyRef, Signal, effect, inject, signal, untracked } from '@angular/core';
import { Observable, Subscription } from 'rxjs';

import { ApiError, toApiError } from './api.service';

export const REFRESH_MS = 15_000;

export interface Polled<T> {
  /** The latest result. Kept while refreshing, and after a failed refresh. */
  readonly data: Signal<T | null>;
  readonly error: Signal<ApiError | null>;
  /** The first load: nothing to show yet. */
  readonly loading: Signal<boolean>;
  /** A later load: what is on screen stays there. */
  readonly refreshing: Signal<boolean>;
  readonly updatedAt: Signal<number | null>;
  reload(): void;
}

/**
 * A panel's data: loaded when ``key()`` changes and refreshed on a timer while the page is visible.
 * Each panel polls for itself, so one failing endpoint does not blank the rest of the screen.
 * Must be called in an injection context. An interval of 0 loads only on change or ``reload()``.
 */
export function poll<K, T>(
  key: () => K,
  load: (key: K) => Observable<T>,
  intervalMs: number = REFRESH_MS,
): Polled<T> {
  const data = signal<T | null>(null);
  const error = signal<ApiError | null>(null);
  const loading = signal(true);
  const refreshing = signal(false);
  const updatedAt = signal<number | null>(null);
  let currentKey: K;
  let hasKey = false;
  let subscription: Subscription | null = null;

  const fetch = (): void => {
    if (!hasKey) {
      return;
    }
    subscription?.unsubscribe();
    if (data() === null) {
      loading.set(true);
    } else {
      refreshing.set(true);
    }
    subscription = load(currentKey).subscribe({
      next: (value) => {
        data.set(value);
        error.set(null);
        loading.set(false);
        refreshing.set(false);
        updatedAt.set(Date.now());
      },
      error: (failure: unknown) => {
        error.set(toApiError(failure));
        loading.set(false);
        refreshing.set(false);
      },
    });
  };

  effect(() => {
    const value = key();
    untracked(() => {
      currentKey = value;
      hasKey = true;
      fetch();
    });
  });

  const timer =
    intervalMs > 0
      ? setInterval(() => {
          if (!document.hidden) {
            fetch();
          }
        }, intervalMs)
      : null;

  inject(DestroyRef).onDestroy(() => {
    subscription?.unsubscribe();
    if (timer !== null) {
      clearInterval(timer);
    }
  });

  return { data, error, loading, refreshing, updatedAt, reload: fetch };
}
