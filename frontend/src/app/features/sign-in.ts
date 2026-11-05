import { ChangeDetectionStrategy, Component, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiError, toApiError } from '../core/api.service';
import { AuthService } from '../core/auth.service';
import { ErrorPanel } from '../shared/states';

@Component({
  selector: 'co-sign-in',
  imports: [FormsModule, ErrorPanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <main class="wrap">
      <form class="card" (ngSubmit)="submit()">
        <h1>CloudOps Monitor</h1>
        <p class="muted small">
          Sign in with the operator token set on the server (CLOUDOPS_OPERATOR_TOKEN). This
          installation has one operator, not individual accounts.
        </p>
        @if (auth.loadError(); as failure) {
          <co-error what="Could not reach the server" [error]="failure" (retry)="auth.refresh()" />
        }
        <label class="field" for="token">Operator token</label>
        <input
          id="token"
          name="token"
          type="password"
          autocomplete="current-password"
          required
          [(ngModel)]="token"
          [attr.aria-invalid]="error() ? 'true' : null"
          [attr.aria-describedby]="error() ? 'token-error' : null"
        />
        @if (error(); as failure) {
          <p id="token-error" class="field-error" role="alert">{{ failure.message }}</p>
        }
        <button class="btn primary" type="submit" [disabled]="pending() || !token().trim()">
          {{ pending() ? 'Signing in…' : 'Sign in' }}
        </button>
      </form>
    </main>
  `,
  styles: `
    .wrap {
      display: grid;
      place-items: center;
      min-height: 100vh;
      padding: 16px;
    }
    .card {
      display: flex;
      flex-direction: column;
      gap: 10px;
      width: min(400px, 100%);
      padding: 20px;
      background: var(--surface-1);
      border: 1px solid var(--rule);
      border-radius: var(--radius);
    }
    .btn {
      align-self: flex-start;
    }
  `,
})
export class SignInPage {
  protected readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly returnUrl = input<string>();

  protected readonly token = signal('');
  protected readonly pending = signal(false);
  protected readonly error = signal<ApiError | null>(null);

  constructor() {
    // Already signed in, or authentication is off: there is nothing to do here.
    void this.auth.ready().then(() => {
      if (this.auth.signedIn()) {
        void this.router.navigateByUrl(this.safeReturnUrl());
      }
    });
  }

  protected async submit(): Promise<void> {
    if (this.pending()) {
      return;
    }
    this.pending.set(true);
    this.error.set(null);
    try {
      await this.auth.signIn(this.token().trim());
      this.token.set('');
      await this.router.navigateByUrl(this.safeReturnUrl());
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.pending.set(false);
    }
  }

  /** Only a path inside this application: a crafted link must not send someone elsewhere. */
  private safeReturnUrl(): string {
    const url = this.returnUrl();
    return url?.startsWith('/') && !url.startsWith('//') ? url : '/overview';
  }
}
