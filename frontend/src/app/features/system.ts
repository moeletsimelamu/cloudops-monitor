import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, firstValueFrom, of } from 'rxjs';
import { HttpErrorResponse } from '@angular/common/http';

import { ApiError, ApiService, toApiError } from '../core/api.service';
import { AuthService } from '../core/auth.service';
import { formatBytes, formatDuration } from '../core/format';
import { Readiness } from '../core/models';
import { poll } from '../core/poll';
import { ClockService } from '../core/preferences.service';
import { ConfirmDialog, ErrorPanel, Skeleton, TimeText } from '../shared/states';
import { Glyph, GlyphShape, GlyphTone } from '../shared/status';

/** Is the monitor itself all right? Secrets appear only as "set" or "not set". */
@Component({
  selector: 'co-system',
  imports: [ConfirmDialog, ErrorPanel, Skeleton, TimeText, Glyph],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './system.html',
  styleUrl: './system.css',
})
export class SystemPage {
  private readonly api = inject(ApiService);
  private readonly clock = inject(ClockService);
  private readonly router = inject(Router);
  protected readonly auth = inject(AuthService);

  protected readonly signOutError = signal<ApiError | null>(null);

  /** A 503 is an answer here, not a failure: its body says what is not ready. */
  protected readonly readiness = poll(
    () => true,
    () =>
      this.api.readiness().pipe(
        catchError((failure: unknown) => {
          if (
            failure instanceof HttpErrorResponse &&
            failure.status === 503 &&
            failure.error?.checks
          ) {
            return of(failure.error as Readiness);
          }
          throw failure;
        }),
      ),
  );
  protected readonly status = poll(
    () => true,
    () => this.api.systemStatus(),
  );
  protected readonly config = poll(
    () => true,
    () => this.api.systemConfig(),
    60_000,
  );

  protected readonly readinessChecks = computed(() =>
    Object.entries(this.readiness.data()?.checks ?? {}).map(([name, state]) => ({ name, state })),
  );

  protected readonly tickAge = computed(() => {
    const tick = this.status.data()?.worker.last_tick_at;
    if (!tick) {
      return null;
    }
    return formatDuration((this.clock.now() - new Date(tick).getTime()) / 1000);
  });

  protected readonly formatBytes = formatBytes;

  protected glyph(state: string): { shape: GlyphShape; tone: GlyphTone } {
    if (state === 'ok') {
      return { shape: 'circle', tone: 'ok' };
    }
    if (state === 'error') {
      return { shape: 'square', tone: 'fail' };
    }
    return { shape: 'hollow', tone: 'neutral' };
  }

  /** Ends every session, this browser's too, then goes to the sign-in page. */
  protected async signOutEverywhere(): Promise<void> {
    this.signOutError.set(null);
    try {
      await firstValueFrom(this.api.signOutEverywhere());
    } catch (failure) {
      this.signOutError.set(toApiError(failure));
      return;
    }
    this.auth.markSignedOut();
    await this.router.navigate(['/sign-in']);
  }

  protected word(state: string): string {
    return state === 'ok' ? 'OK' : state === 'error' ? 'Failing' : 'Not applicable';
  }
}
