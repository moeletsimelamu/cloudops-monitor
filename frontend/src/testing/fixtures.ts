import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import {
  Application,
  Availability,
  Bucket,
  Deployment,
  FleetEntry,
  LogEntry,
  Metrics,
  OperationalEvent,
  Overview,
} from '../app/core/models';

export const TEST_PROVIDERS = [provideHttpClient(), provideHttpClientTesting(), provideRouter([])];

export function application(overrides: Partial<Application> = {}): Application {
  return {
    id: 1,
    slug: 'orders-api',
    name: 'Orders API',
    description: '',
    environment: 'production',
    check_url: 'http://localhost:8310/health',
    interval_seconds: 60,
    timeout_seconds: 5,
    slow_threshold_ms: 1000,
    failure_threshold: 2,
    recovery_threshold: 2,
    monitoring_enabled: true,
    health_state: 'healthy',
    status: 'healthy',
    stale_since: null,
    state_since: '2026-11-03T10:00:00Z',
    last_observed_at: '2026-11-03T14:00:00Z',
    last_attempt_at: '2026-11-03T14:00:00Z',
    last_success_at: '2026-11-03T14:00:00Z',
    last_failure_at: null,
    last_failure_reason: null,
    next_check_at: '2026-11-03T14:01:00Z',
    github_repo: null,
    deploy_workflow: null,
    deploy_branch: null,
    has_ingest_key: false,
    open_incident: null,
    latest_check: null,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:00:00Z',
    ...overrides,
  };
}

export function availability(overrides: Partial<Availability> = {}): Availability {
  return {
    checks: 100,
    ok: 95,
    warn: 3,
    fail: 2,
    expected_checks: 100,
    coverage: 1,
    availability: 0.98,
    insufficient_reason: null,
    ...overrides,
  };
}

export function deployment(overrides: Partial<Deployment> = {}): Deployment {
  return {
    id: 7,
    application_slug: 'orders-api',
    application_name: 'Orders API',
    environment: 'production',
    source: 'github_actions',
    external_id: '812',
    attempt: 1,
    run_number: 57,
    workflow_name: 'Deploy',
    commit_sha: 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678',
    branch: 'main',
    trigger: 'push',
    actor: 'octocat',
    title: 'Fix order totals',
    outcome: 'succeeded',
    queued_at: '2026-11-03T13:00:00Z',
    started_at: '2026-11-03T13:00:05Z',
    completed_at: '2026-11-03T13:03:00Z',
    duration_seconds: 175,
    url: 'https://github.com/acme/orders/actions/runs/812',
    synced_at: '2026-11-03T13:05:00Z',
    ...overrides,
  };
}

export function fleetEntry(app: Application = application()): FleetEntry {
  return {
    application: app,
    availability_24h: availability(),
    p95_ms_24h: 212,
    latest_deployment: null,
  };
}

export function operationalEvent(overrides: Partial<OperationalEvent> = {}): OperationalEvent {
  return {
    id: 1,
    occurred_at: '2026-11-03T13:50:00Z',
    type: 'health.degraded',
    severity: 'warning',
    application_slug: 'orders-api',
    application_name: 'Orders API',
    incident_id: 1,
    deployment_id: null,
    summary: 'Orders API is degraded: HTTP 500: the application is failing',
    details: {},
    ...overrides,
  };
}

export function logEntry(overrides: Partial<LogEntry> = {}): LogEntry {
  return {
    id: 1,
    timestamp: '2026-11-03T13:50:00Z',
    received_at: '2026-11-03T13:50:01Z',
    severity: 'error',
    event: 'order.load_failed',
    message: 'Could not load orders: database timeout after 5000 ms',
    correlation_id: 'req-1',
    environment: 'production',
    application_slug: 'orders-api',
    application_name: 'Orders API',
    attributes: { timeout_ms: 5000 },
    ...overrides,
  };
}

export function overview(overrides: Partial<Overview> = {}): Overview {
  return {
    generated_at: '2026-11-03T14:00:00Z',
    health_window_hours: 24,
    counts: { total: 0, healthy: 0, degraded: 0, unavailable: 0, stale: 0, pending: 0, paused: 0 },
    needs_attention: [],
    fleet: [],
    recent_incidents: [],
    recent_deployments: [],
    recent_changes: [],
    definitions: { availability: 'Not uptime.', coverage: 'Coverage.', latency: 'Latency.' },
    ...overrides,
  };
}

export function bucket(start: string, overrides: Partial<Bucket> = {}): Bucket {
  return { start, checks: 1, failed: 0, worst: 'ok', p50_ms: 40, p95_ms: 50, ...overrides };
}

export function metrics(buckets: Bucket[]): Metrics {
  return {
    window: '1h',
    window_start: buckets[0].start,
    window_end: new Date(
      new Date(buckets[0].start).getTime() + buckets.length * 60_000,
    ).toISOString(),
    bucket_seconds: 60,
    availability: availability(),
    p50_ms: 40,
    p95_ms: 50,
    buckets,
    definitions: { availability: 'Not uptime.', coverage: 'Coverage.', latency: 'Latency.' },
  };
}
