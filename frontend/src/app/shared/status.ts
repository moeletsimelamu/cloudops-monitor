import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { DisplayStatus, EventSeverity, Outcome, Severity, Signal } from '../core/models';

/** Status never relies on colour alone: each kind has a shape, a colour and a word. */
export type GlyphShape = 'circle' | 'triangle' | 'square' | 'dashed' | 'hollow' | 'bars' | 'dot';
export type GlyphTone = 'ok' | 'warn' | 'fail' | 'neutral';

@Component({
  selector: 'co-glyph',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
      @switch (shape()) {
        @case ('circle') {
          <circle cx="6" cy="6" r="5" fill="currentColor" />
        }
        @case ('triangle') {
          <path d="M6 1 L11.5 11 H0.5 Z" fill="currentColor" />
        }
        @case ('square') {
          <rect x="1" y="1" width="10" height="10" fill="currentColor" />
        }
        @case ('dashed') {
          <circle
            cx="6"
            cy="6"
            r="4.5"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-dasharray="2.2 1.8"
          />
        }
        @case ('hollow') {
          <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6" />
        }
        @case ('bars') {
          <rect x="2.5" y="1.5" width="2.5" height="9" fill="currentColor" />
          <rect x="7" y="1.5" width="2.5" height="9" fill="currentColor" />
        }
        @case ('dot') {
          <circle cx="6" cy="6" r="2.5" fill="currentColor" />
        }
      }
    </svg>
  `,
  styles: `
    :host {
      display: inline-flex;
      flex: none;
      vertical-align: -1px;
    }
    :host(.ok) {
      color: var(--ok);
    }
    :host(.warn) {
      color: var(--warn);
    }
    :host(.fail) {
      color: var(--fail);
    }
    :host(.neutral) {
      color: var(--neutral);
    }
  `,
  host: { '[class]': 'tone()' },
})
export class Glyph {
  readonly shape = input.required<GlyphShape>();
  readonly tone = input.required<GlyphTone>();
}

interface Look {
  shape: GlyphShape;
  tone: GlyphTone;
  word: string;
}

const STATUS: Record<DisplayStatus, Look> = {
  healthy: { shape: 'circle', tone: 'ok', word: 'Healthy' },
  degraded: { shape: 'triangle', tone: 'warn', word: 'Degraded' },
  unavailable: { shape: 'square', tone: 'fail', word: 'Unavailable' },
  stale: { shape: 'dashed', tone: 'neutral', word: 'Stale' },
  pending: { shape: 'hollow', tone: 'neutral', word: 'Pending' },
  paused: { shape: 'bars', tone: 'neutral', word: 'Paused' },
};

const SIGNAL: Record<Signal, Look> = {
  ok: { shape: 'circle', tone: 'ok', word: 'OK' },
  warn: { shape: 'triangle', tone: 'warn', word: 'Warning' },
  fail: { shape: 'square', tone: 'fail', word: 'Failed' },
  inconclusive: { shape: 'hollow', tone: 'neutral', word: 'Inconclusive' },
};

const OUTCOME: Record<Outcome, Look> = {
  succeeded: { shape: 'circle', tone: 'ok', word: 'Succeeded' },
  failed: { shape: 'square', tone: 'fail', word: 'Failed' },
  in_progress: { shape: 'hollow', tone: 'neutral', word: 'In progress' },
  queued: { shape: 'hollow', tone: 'neutral', word: 'Queued' },
  cancelled: { shape: 'bars', tone: 'neutral', word: 'Cancelled' },
  needs_attention: { shape: 'triangle', tone: 'warn', word: 'Needs attention' },
};

const SEVERITY: Record<Severity, Look> = {
  debug: { shape: 'dot', tone: 'neutral', word: 'debug' },
  info: { shape: 'dot', tone: 'neutral', word: 'info' },
  warning: { shape: 'triangle', tone: 'warn', word: 'warning' },
  error: { shape: 'square', tone: 'fail', word: 'error' },
  critical: { shape: 'square', tone: 'fail', word: 'critical' },
};

export function statusLook(status: DisplayStatus): Look {
  return STATUS[status];
}

@Component({
  selector: 'co-status',
  imports: [Glyph],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<co-glyph [shape]="look().shape" [tone]="look().tone" /><span class="word">{{
      look().word
    }}</span>`,
  styleUrl: './status-badge.css',
})
export class StatusBadge {
  readonly status = input.required<DisplayStatus>();
  protected readonly look = computed(() => STATUS[this.status()]);
}

@Component({
  selector: 'co-signal',
  imports: [Glyph],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<co-glyph [shape]="look().shape" [tone]="look().tone" /><span class="plain">{{
      look().word
    }}</span>`,
  styleUrl: './status-badge.css',
})
export class SignalBadge {
  readonly signal = input.required<Signal>();
  protected readonly look = computed(() => SIGNAL[this.signal()]);
}

@Component({
  selector: 'co-outcome',
  imports: [Glyph],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<co-glyph [shape]="look().shape" [tone]="look().tone" /><span class="plain">{{
      look().word
    }}</span>`,
  styleUrl: './status-badge.css',
})
export class OutcomeBadge {
  readonly outcome = input.required<Outcome>();
  protected readonly look = computed(() => OUTCOME[this.outcome()]);
}

@Component({
  selector: 'co-severity',
  imports: [Glyph],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<co-glyph [shape]="look().shape" [tone]="look().tone" /><span class="plain">{{
      look().word
    }}</span>`,
  styleUrl: './status-badge.css',
})
export class SeverityBadge {
  readonly severity = input.required<Severity | EventSeverity>();
  protected readonly look = computed(() => SEVERITY[this.severity()]);
}
