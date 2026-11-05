import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { ThemeService } from './core/preferences.service';

@Component({
  selector: 'co-root',
  imports: [RouterOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<router-outlet />`,
})
export class App {
  // Created here so the saved theme applies before the first page draws.
  protected readonly theme = inject(ThemeService);
}
