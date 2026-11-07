import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  Application,
  ApplicationIncident,
  ApplicationSettings,
  Check,
  CheckPage,
  Deployment,
  DeploymentSources,
  HealthMatrix,
  Incident,
  LogEntry,
  Metrics,
  OperationalEvent,
  Overview,
  Page,
  Readiness,
  RequestMetrics,
  SessionStatus,
  SyncResult,
  SystemConfig,
  SystemStatus,
  TimeWindow,
} from './models';

export type QueryValues = Record<string, string | number | null | undefined>;

export interface ApiError {
  status: number;
  code: string;
  message: string;
  requestId: string | null;
  details: unknown;
}

export const UNREACHABLE_CODE = 'unreachable';

/** The API's error envelope as a plain object, or a clear message when there is no envelope. */
export function toApiError(error: unknown): ApiError {
  if (error instanceof HttpErrorResponse) {
    const body = error.error?.error;
    if (body && typeof body.message === 'string') {
      return {
        status: error.status,
        code: body.code ?? 'error',
        message: body.message,
        requestId: body.request_id ?? null,
        details: body.details ?? null,
      };
    }
    if (error.status === 0 || error.status >= 500) {
      return {
        status: error.status,
        code: UNREACHABLE_CODE,
        message: "Can't reach the CloudOps Monitor server. Check that it is running, then retry.",
        requestId: null,
        details: null,
      };
    }
    return {
      status: error.status,
      code: 'error',
      message: `The server answered with HTTP ${error.status}.`,
      requestId: null,
      details: null,
    };
  }
  return {
    status: 0,
    code: 'error',
    message: 'Something went wrong in the browser. Reload the page.',
    requestId: null,
    details: null,
  };
}

function params(values: QueryValues): HttpParams {
  let result = new HttpParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== null && value !== undefined && value !== '') {
      result = result.set(key, String(value));
    }
  }
  return result;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  session(): Observable<SessionStatus> {
    return this.http.get<SessionStatus>('/api/auth/session');
  }

  signIn(token: string): Observable<SessionStatus> {
    return this.http.post<SessionStatus>('/api/auth/session', { token });
  }

  signOut(): Observable<void> {
    return this.http.delete<void>('/api/auth/session');
  }

  /** Ends every browser session, this one included. */
  signOutEverywhere(): Observable<{ ended: number }> {
    return this.http.delete<{ ended: number }>('/api/auth/sessions');
  }

  overview(): Observable<Overview> {
    return this.http.get<Overview>('/api/overview');
  }

  healthMatrix(window: TimeWindow): Observable<HealthMatrix> {
    return this.http.get<HealthMatrix>('/api/health-matrix', { params: params({ window }) });
  }

  applications(): Observable<{ items: Application[]; generated_at: string }> {
    return this.http.get<{ items: Application[]; generated_at: string }>('/api/applications');
  }

  application(slug: string): Observable<Application> {
    return this.http.get<Application>(`/api/applications/${slug}`);
  }

  createApplication(body: ApplicationSettings & { slug: string }): Observable<Application> {
    return this.http.post<Application>('/api/applications', body);
  }

  updateApplication(slug: string, body: Partial<ApplicationSettings>): Observable<Application> {
    return this.http.patch<Application>(`/api/applications/${slug}`, body);
  }

  deleteApplication(slug: string): Observable<void> {
    return this.http.delete<void>(`/api/applications/${slug}`);
  }

  pause(slug: string): Observable<Application> {
    return this.http.post<Application>(`/api/applications/${slug}/pause`, null);
  }

  resume(slug: string): Observable<Application> {
    return this.http.post<Application>(`/api/applications/${slug}/resume`, null);
  }

  checkNow(slug: string): Observable<Check> {
    return this.http.post<Check>(`/api/applications/${slug}/checks`, null);
  }

  checks(slug: string, query: QueryValues): Observable<CheckPage> {
    return this.http.get<CheckPage>(`/api/applications/${slug}/checks`, { params: params(query) });
  }

  incidents(slug: string, limit = 20): Observable<{ items: Incident[] }> {
    return this.http.get<{ items: Incident[] }>(`/api/applications/${slug}/incidents`, {
      params: params({ limit }),
    });
  }

  allIncidents(query: QueryValues): Observable<Page<ApplicationIncident>> {
    return this.http.get<Page<ApplicationIncident>>('/api/incidents', { params: params(query) });
  }

  metrics(slug: string, window: TimeWindow): Observable<Metrics> {
    return this.http.get<Metrics>(`/api/applications/${slug}/metrics`, {
      params: params({ window }),
    });
  }

  requestMetrics(slug: string, window: TimeWindow): Observable<RequestMetrics> {
    return this.http.get<RequestMetrics>(`/api/applications/${slug}/request-metrics`, {
      params: params({ window }),
    });
  }

  createIngestKey(slug: string): Observable<{ ingest_key: string; application_slug: string }> {
    return this.http.post<{ ingest_key: string; application_slug: string }>(
      `/api/applications/${slug}/ingest-key`,
      null,
    );
  }

  revokeIngestKey(slug: string): Observable<void> {
    return this.http.delete<void>(`/api/applications/${slug}/ingest-key`);
  }

  syncNow(slug: string): Observable<SyncResult> {
    return this.http.post<SyncResult>(`/api/applications/${slug}/deployments/sync`, null);
  }

  logs(query: QueryValues): Observable<Page<LogEntry>> {
    return this.http.get<Page<LogEntry>>('/api/logs', { params: params(query) });
  }

  events(query: QueryValues): Observable<Page<OperationalEvent>> {
    return this.http.get<Page<OperationalEvent>>('/api/events', { params: params(query) });
  }

  deployments(query: QueryValues): Observable<Page<Deployment>> {
    return this.http.get<Page<Deployment>>('/api/deployments', { params: params(query) });
  }

  deploymentSources(): Observable<DeploymentSources> {
    return this.http.get<DeploymentSources>('/api/deployments/sources');
  }

  readiness(): Observable<Readiness> {
    return this.http.get<Readiness>('/api/health/ready');
  }

  systemStatus(): Observable<SystemStatus> {
    return this.http.get<SystemStatus>('/api/system/status');
  }

  systemConfig(): Observable<SystemConfig> {
    return this.http.get<SystemConfig>('/api/system/config');
  }
}
