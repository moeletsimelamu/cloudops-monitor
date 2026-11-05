import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  inject,
  input,
  output,
  viewChild,
} from '@angular/core';

import { ApiError, UNREACHABLE_CODE } from '../core/api.service';
import { formatRelative, formatTimestamp } from '../core/format';
import { ClockService } from '../core/preferences.service';
import { Glyph } from './status';

/** A moment: relative by default, the exact time on hover and for screen readers. */
@Component({
  selector: 'co-time',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (at(); as moment) {
      <time class="mono" [attr.datetime]="moment" [title]="exact()">{{ text() }}</time>
    } @else {
      <span class="muted">{{ none() }}</span>
    }
  `,
  styles: `
    :host {
      white-space: nowrap;
    }
  `,
})
export class TimeText {
  private readonly clock = inject(ClockService);

  readonly at = input.required<string | null>();
  readonly mode = input<'relative' | 'exact'>('relative');
  readonly none = input('—');

  protected readonly exact = computed(() => {
    const moment = this.at();
    if (!moment) {
      return '';
    }
    const zone = this.clock.utc() ? 'UTC' : 'local';
    return `${formatTimestamp(moment, this.clock.utc())} ${zone}`;
  });

  protected readonly text = computed(() => {
    const moment = this.at();
    if (!moment) {
      return '';
    }
    return this.mode() === 'exact'
      ? formatTimestamp(moment, this.clock.utc())
      : formatRelative(moment, this.clock.now());
  });
}

/** What failed, what to do about it, and the request ID to quote. */
@Component({
  selector: 'co-error',
  imports: [Glyph],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="error" role="alert">
      <co-glyph shape="square" tone="fail" />
      <div class="body">
        <p class="what">{{ what() }}</p>
        <p>{{ error().message }}</p>
        @if (hint(); as text) {
          <p class="muted small">{{ text }}</p>
        }
        @if (error().requestId; as id) {
          <p class="muted small">
            Request ID <span class="mono">{{ id }}</span>
          </p>
        }
      </div>
      @if (retryable()) {
        <button class="btn" type="button" (click)="retry.emit()">Retry</button>
      }
    </div>
  `,
  styles: `
    .error {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 10px 12px;
      font-size: 13px;
    }
    .body {
      flex: 1;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .what {
      font-weight: 500;
    }
    co-glyph {
      margin-top: 3px;
    }
  `,
})
export class ErrorPanel {
  readonly error = input.required<ApiError>();
  readonly what = input('This could not be loaded');
  readonly retryable = input(true);
  readonly retry = output<void>();

  protected readonly hint = computed(() =>
    this.error().code === UNREACHABLE_CODE
      ? 'The rest of the page keeps refreshing, so it recovers by itself once the server is back.'
      : null,
  );
}

/** Empty is honest: it says what is missing and, through its content, what to do. */
@Component({
  selector: 'co-empty',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="empty">
      <p class="title">{{ title() }}</p>
      <div class="muted small"><ng-content /></div>
    </div>
  `,
  styles: `
    .empty {
      padding: 20px 12px;
      font-size: 13px;
    }
    .title {
      font-weight: 500;
      margin-bottom: 2px;
    }
  `,
})
export class EmptyState {
  readonly title = input.required<string>();
}

/** Placeholder rows, shown only for a first load. */
@Component({
  selector: 'co-skeleton',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div role="status" aria-label="Loading">
      @for (row of rows(); track row) {
        <span class="skeleton" [style.width.%]="90 - row * 7"></span>
      }
    </div>
  `,
})
export class Skeleton {
  readonly count = input(4);
  protected readonly rows = computed(() => Array.from({ length: this.count() }, (_, i) => i));
}

/** A confirmation for something that cannot be undone. Native <dialog> traps focus for us. */
@Component({
  selector: 'co-confirm',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dialog #dialog [attr.aria-labelledby]="'confirm-title'">
      <h2 id="confirm-title">{{ title() }}</h2>
      <p class="message"><ng-content /></p>
      <div class="actions">
        <button class="btn" type="button" (click)="close()">Cancel</button>
        <button class="btn danger" type="button" (click)="confirmed.emit(); close()">
          {{ action() }}
        </button>
      </div>
    </dialog>
  `,
  styles: `
    dialog {
      max-width: 460px;
      padding: 16px;
      border: 1px solid var(--rule-strong);
      border-radius: var(--radius);
      background: var(--surface-1);
      color: var(--ink);
    }
    .message {
      margin: 8px 0 14px;
      font-size: 13px;
    }
    .actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }
  `,
})
export class ConfirmDialog {
  readonly title = input.required<string>();
  readonly action = input.required<string>();
  readonly confirmed = output<void>();

  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  open(): void {
    this.dialog().nativeElement.showModal();
  }

  close(): void {
    this.dialog().nativeElement.close();
  }
}
