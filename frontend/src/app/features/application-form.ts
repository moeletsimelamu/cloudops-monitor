import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { ApiError, ApiService, toApiError } from '../core/api.service';
import { ApplicationSettings, SystemConfig } from '../core/models';
import { ErrorPanel } from '../shared/states';

interface FormValue extends ApplicationSettings {
  slug: string;
}

const DEFAULTS: FormValue = {
  slug: '',
  name: '',
  description: '',
  environment: 'production',
  check_url: '',
  interval_seconds: 60,
  timeout_seconds: 5,
  slow_threshold_ms: 1000,
  failure_threshold: 2,
  recovery_threshold: 2,
  github_repo: null,
  deploy_workflow: null,
  deploy_branch: null,
};

export function slugFromName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
    .replace(/-+$/, '');
}

interface ValidationDetail {
  location?: string[];
  message?: string;
}

/** Registers a new application, or edits one (at /applications/:slug/edit). */
@Component({
  selector: 'co-application-form',
  imports: [FormsModule, RouterLink, ErrorPanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './application-form.html',
  styleUrl: './application-form.css',
})
export class ApplicationForm {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  /** From the route; absent when registering a new application. */
  readonly slug = input<string>();

  protected readonly editing = computed(() => this.slug() !== undefined);
  protected readonly form = signal<FormValue>({ ...DEFAULTS });
  protected readonly loadError = signal<ApiError | null>(null);
  protected readonly saveError = signal<ApiError | null>(null);
  protected readonly fieldErrors = signal<Record<string, string>>({});
  protected readonly pending = signal(false);
  protected readonly config = signal<SystemConfig | null>(null);
  private slugEdited = false;

  protected readonly cancelLink = computed(() =>
    this.editing() ? ['/applications', this.slug() ?? ''] : ['/applications'],
  );

  constructor() {
    this.api.systemConfig().subscribe({
      next: (config) => this.config.set(config),
      error: () => this.config.set(null), // only a hint: the server still enforces the policy
    });
    effect(() => {
      const slug = this.slug();
      if (slug) {
        untracked(() => this.load(slug));
      }
    });
  }

  private load(slug: string): void {
    this.api.application(slug).subscribe({
      next: (application) => {
        this.form.set({
          slug: application.slug,
          name: application.name,
          description: application.description,
          environment: application.environment,
          check_url: application.check_url,
          interval_seconds: application.interval_seconds,
          timeout_seconds: application.timeout_seconds,
          slow_threshold_ms: application.slow_threshold_ms,
          failure_threshold: application.failure_threshold,
          recovery_threshold: application.recovery_threshold,
          github_repo: application.github_repo,
          deploy_workflow: application.deploy_workflow,
          deploy_branch: application.deploy_branch,
        });
      },
      error: (failure: unknown) => this.loadError.set(toApiError(failure)),
    });
  }

  protected set<K extends keyof FormValue>(field: K, value: FormValue[K]): void {
    this.form.update((current) => {
      const next = { ...current, [field]: value };
      if (field === 'name' && !this.editing() && !this.slugEdited) {
        next.slug = slugFromName(String(value));
      }
      return next;
    });
    if (field === 'slug') {
      this.slugEdited = true;
    }
  }

  protected errorFor(field: string): string | null {
    return this.fieldErrors()[field] ?? null;
  }

  protected async submit(): Promise<void> {
    if (this.pending()) {
      return;
    }
    this.pending.set(true);
    this.saveError.set(null);
    this.fieldErrors.set({});
    const value = this.form();
    const settings: ApplicationSettings = {
      name: value.name,
      description: value.description,
      environment: value.environment,
      check_url: value.check_url,
      interval_seconds: Number(value.interval_seconds),
      timeout_seconds: Number(value.timeout_seconds),
      slow_threshold_ms: Number(value.slow_threshold_ms),
      failure_threshold: Number(value.failure_threshold),
      recovery_threshold: Number(value.recovery_threshold),
      github_repo: value.github_repo?.trim() || null,
      deploy_workflow: value.deploy_workflow?.trim() || null,
      deploy_branch: value.deploy_branch?.trim() || null,
    };
    try {
      const slug = this.slug();
      const saved = slug
        ? await firstValueFrom(this.api.updateApplication(slug, settings))
        : await firstValueFrom(this.api.createApplication({ ...settings, slug: value.slug }));
      await this.router.navigate(['/applications', saved.slug]);
    } catch (failure) {
      this.showError(toApiError(failure));
    } finally {
      this.pending.set(false);
    }
  }

  /** Field problems go next to their field; anything else goes above the form. */
  private showError(error: ApiError): void {
    const fields: Record<string, string> = {};
    if (Array.isArray(error.details)) {
      for (const detail of error.details as ValidationDetail[]) {
        const field = detail.location?.[1];
        const message = detail.message?.replace(/^Value error, /, '');
        if (field && message) {
          fields[field] = message;
        }
      }
    }
    this.fieldErrors.set(fields);
    if (Object.keys(fields).length === 0) {
      // Rules across several fields have no single field to sit next to.
      const general = Array.isArray(error.details)
        ? (error.details as ValidationDetail[])
            .map((detail) => detail.message?.replace(/^Value error, /, ''))
            .filter(Boolean)
            .join(' ')
        : '';
      this.saveError.set(general ? { ...error, message: general } : error);
    }
  }
}
