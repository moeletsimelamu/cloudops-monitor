import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

import { EmptyState } from '../shared/states';

@Component({
  selector: 'co-not-found',
  imports: [EmptyState, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <section class="section">
        <co-empty title="There is nothing at this address">
          Go back to the <a routerLink="/overview">overview</a>.
        </co-empty>
      </section>
    </div>
  `,
})
export class NotFoundPage {}
