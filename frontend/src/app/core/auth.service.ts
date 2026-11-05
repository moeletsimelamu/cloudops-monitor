import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, firstValueFrom, throwError } from 'rxjs';

import { ApiError, ApiService, toApiError } from './api.service';
import { SessionStatus } from './models';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiService);
  private loaded: Promise<void> | null = null;

  readonly session = signal<SessionStatus | null>(null);
  readonly loadError = signal<ApiError | null>(null);
  readonly signedIn = computed(() => this.session()?.authenticated === true);
  readonly authDisabled = computed(() => this.session()?.auth_mode === 'disabled');

  /** Loads the session once. Later calls wait for the same request. */
  ready(): Promise<void> {
    this.loaded ??= this.refresh();
    return this.loaded;
  }

  async refresh(): Promise<void> {
    try {
      this.session.set(await firstValueFrom(this.api.session()));
      this.loadError.set(null);
    } catch (error) {
      this.session.set(null);
      this.loadError.set(toApiError(error));
    }
  }

  async signIn(token: string): Promise<void> {
    this.session.set(await firstValueFrom(this.api.signIn(token)));
    this.loadError.set(null);
  }

  async signOut(): Promise<void> {
    await firstValueFrom(this.api.signOut());
    await this.refresh();
  }

  /** The server no longer accepts this browser's session. */
  markSignedOut(): void {
    const current = this.session();
    this.session.set({
      auth_mode: current?.auth_mode ?? 'token',
      authenticated: false,
      expires_at: null,
    });
  }
}

export const authGuard: CanActivateFn = async (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  await auth.ready();
  if (auth.signedIn()) {
    return true;
  }
  return router.createUrlTree(['/sign-in'], { queryParams: { returnUrl: state.url } });
};

/** A 401 on any request other than sign-in itself means the session has ended. */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return next(request).pipe(
    catchError((error: unknown) => {
      if (
        error instanceof HttpErrorResponse &&
        error.status === 401 &&
        !request.url.startsWith('/api/auth/')
      ) {
        auth.markSignedOut();
        if (!router.url.startsWith('/sign-in')) {
          void router.navigate(['/sign-in'], { queryParams: { returnUrl: router.url } });
        }
      }
      return throwError(() => error);
    }),
  );
};
