import { Signal, computed, effect, signal, untracked } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiError, toApiError } from './api.service';
import { Page } from './models';
import { Polled, REFRESH_MS, poll } from './poll';

export interface PagedList<T> extends Pick<Polled<Page<T>>, 'error' | 'loading' | 'refreshing'> {
  readonly items: Signal<T[]>;
  readonly hasMore: Signal<boolean>;
  readonly loadingMore: Signal<boolean>;
  readonly moreError: Signal<ApiError | null>;
  loadMore(): void;
  reload(): void;
}

/**
 * A newest-first list: the first page refreshes on a timer, and older pages are added on request.
 * Rows are de-duplicated by ID, because refreshing shifts what counts as "the first page".
 */
export function pagedPoll<K, T extends { id: number }>(
  key: () => K,
  load: (key: K, cursor: string | null) => Observable<Page<T>>,
  intervalMs: number = REFRESH_MS,
): PagedList<T> {
  const first = poll(key, (value) => load(value, null), intervalMs);
  const older = signal<T[]>([]);
  const olderCursor = signal<string | null>(null);
  const loadingMore = signal(false);
  const moreError = signal<ApiError | null>(null);

  // New filters start again from the first page.
  effect(() => {
    key();
    untracked(() => {
      older.set([]);
      olderCursor.set(null);
      moreError.set(null);
    });
  });

  const nextCursor = computed(
    () => olderCursor() ?? (older().length ? null : (first.data()?.next_cursor ?? null)),
  );

  return {
    error: first.error,
    loading: first.loading,
    refreshing: first.refreshing,
    reload: first.reload,
    loadingMore,
    moreError,
    hasMore: computed(() => nextCursor() !== null),
    items: computed(() => {
      const seen = new Set<number>();
      return [...(first.data()?.items ?? []), ...older()].filter((item) => {
        if (seen.has(item.id)) {
          return false;
        }
        seen.add(item.id);
        return true;
      });
    }),
    loadMore: () => {
      const cursor = nextCursor();
      if (cursor === null || loadingMore()) {
        return;
      }
      loadingMore.set(true);
      moreError.set(null);
      load(untracked(key), cursor).subscribe({
        next: (page) => {
          older.update((items) => [...items, ...page.items]);
          olderCursor.set(page.next_cursor);
          // A cursor of null with older rows loaded means the end was reached.
          loadingMore.set(false);
        },
        error: (failure: unknown) => {
          moreError.set(toApiError(failure));
          loadingMore.set(false);
        },
      });
    },
  };
}
