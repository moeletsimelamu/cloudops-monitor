/** Types of the JSON the API returns. Timestamps are ISO 8601 strings in UTC. */

export type DisplayStatus = 'paused' | 'pending' | 'stale' | 'healthy' | 'degraded' | 'unavailable';
export type HealthState = 'unknown' | 'healthy' | 'degraded' | 'unavailable';
export type Signal = 'ok' | 'warn' | 'fail' | 'inconclusive';
export type TimeWindow = '1h' | '6h' | '24h' | '7d';
export type Severity = 'debug' | 'info' | 'warning' | 'error' | 'critical';
export type EventSeverity = 'info' | 'warning' | 'error' | 'critical';
export type Outcome =
  'queued' | 'in_progress' | 'succeeded' | 'failed' | 'cancelled' | 'needs_attention';

export interface SessionStatus {
  auth_mode: 'token' | 'disabled';
  authenticated: boolean;
  expires_at: string | null;
}

export interface Check {
  id: number;
  started_at: string;
  duration_ms: number | null;
  http_status: number | null;
  signal: Signal;
  reason: string | null;
  detail: string | null;
  resolved_ip: string | null;
  state_after: HealthState;
}

export interface PrecedingDeployment {
  id: number;
  run_number: number | null;
  commit_sha: string;
  outcome: string;
  url: string | null;
  relation: 'in_progress' | 'completed_before';
  minutes_before: number;
  summary: string;
}

export interface Incident {
  id: number;
  opened_at: string;
  closed_at: string | null;
  worst_state: 'degraded' | 'unavailable';
  opening_reason: string | null;
  duration_seconds: number | null;
  preceding_deployment: PrecedingDeployment | null;
}

export interface ApplicationIncident extends Incident {
  application_slug: string;
  application_name: string;
  environment: string;
}

export interface Application {
  id: number;
  slug: string;
  name: string;
  description: string;
  environment: string;
  check_url: string;
  interval_seconds: number;
  timeout_seconds: number;
  slow_threshold_ms: number;
  failure_threshold: number;
  recovery_threshold: number;
  monitoring_enabled: boolean;
  health_state: HealthState;
  status: DisplayStatus;
  stale_since: string | null;
  state_since: string | null;
  last_observed_at: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_failure_reason: string | null;
  next_check_at: string | null;
  github_repo: string | null;
  deploy_workflow: string | null;
  deploy_branch: string | null;
  has_ingest_key: boolean;
  open_incident: Incident | null;
  latest_check: Check | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationSettings {
  name: string;
  description: string;
  environment: string;
  check_url: string;
  interval_seconds: number;
  timeout_seconds: number;
  slow_threshold_ms: number;
  failure_threshold: number;
  recovery_threshold: number;
  github_repo: string | null;
  deploy_workflow: string | null;
  deploy_branch: string | null;
}

export interface Availability {
  checks: number;
  ok: number;
  warn: number;
  fail: number;
  expected_checks: number;
  coverage: number | null;
  availability: number | null;
  insufficient_reason: string | null;
}

export interface Bucket {
  start: string;
  checks: number;
  failed: number;
  worst: 'ok' | 'warn' | 'fail' | null;
  p50_ms: number | null;
  p95_ms: number | null;
}

export interface Metrics {
  window: TimeWindow;
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  availability: Availability;
  p50_ms: number | null;
  p95_ms: number | null;
  buckets: Bucket[];
  definitions: Record<string, string>;
}

export interface RequestBucket {
  start: string;
  requests: number;
  errors: number;
}

export interface RequestMetrics {
  window: TimeWindow;
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  has_data: boolean;
  requests: number;
  errors: number;
  buckets: RequestBucket[];
  source: string;
}

export interface Deployment {
  id: number;
  application_slug: string;
  application_name: string;
  environment: string;
  source: string;
  external_id: string;
  attempt: number;
  run_number: number | null;
  workflow_name: string | null;
  commit_sha: string;
  branch: string | null;
  trigger: string | null;
  actor: string | null;
  title: string | null;
  outcome: Outcome;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  url: string | null;
  synced_at: string;
}

export interface DeploymentSource {
  application_slug: string;
  application_name: string;
  github_repo: string;
  deploy_workflow: string;
  deploy_branch: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error_kind: string | null;
  last_error_message: string | null;
}

export interface DeploymentSources {
  token_configured: boolean;
  sync_interval_seconds: number;
  rate_limit_remaining: number | null;
  rate_limit_reset_at: string | null;
  last_run_at: string | null;
  sources: DeploymentSource[];
}

export interface SyncResult {
  status: 'synced' | 'not_modified' | 'skipped' | 'failed';
  created: number;
  updated: number;
  error_kind: string | null;
  message: string | null;
}

export interface OperationalEvent {
  id: number;
  occurred_at: string;
  type: string;
  severity: EventSeverity;
  application_slug: string | null;
  application_name: string | null;
  incident_id: number | null;
  deployment_id: number | null;
  summary: string;
  details: Record<string, unknown>;
}

export interface LogEntry {
  id: number;
  timestamp: string;
  received_at: string;
  severity: Severity;
  event: string | null;
  message: string;
  correlation_id: string | null;
  environment: string | null;
  application_slug: string;
  application_name: string;
  attributes: Record<string, unknown> | null;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface CheckPage {
  items: Check[];
  next_before: number | null;
}

export interface FleetEntry {
  application: Application;
  availability_24h: Availability;
  p95_ms_24h: number | null;
  latest_deployment: Deployment | null;
}

export type StatusCounts = Record<
  'total' | 'healthy' | 'degraded' | 'unavailable' | 'stale' | 'pending' | 'paused',
  number
>;

export interface Overview {
  generated_at: string;
  health_window_hours: number;
  counts: StatusCounts;
  needs_attention: FleetEntry[];
  fleet: FleetEntry[];
  recent_incidents: ApplicationIncident[];
  recent_deployments: Deployment[];
  recent_changes: OperationalEvent[];
  definitions: Record<string, string>;
}

export interface HealthRow {
  application: Application;
  availability: Availability;
  p95_ms: number | null;
  buckets: Bucket[];
}

export interface HealthMatrix {
  window: TimeWindow;
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  rows: HealthRow[];
  definitions: Record<string, string>;
}

export interface Readiness {
  status: 'ready' | 'not_ready';
  checks: Record<string, 'ok' | 'error' | 'skipped'>;
}

export interface SystemStatus {
  version: string;
  generated_at: string;
  worker: {
    enabled: boolean;
    running: boolean;
    healthy: boolean;
    started_at: string | null;
    last_tick_at: string | null;
    loop_lag_ms: number | null;
    consecutive_tick_failures: number;
    checks_in_flight: number;
    last_retention_at: string | null;
    last_retention_deleted: Record<string, number> | null;
  };
  database: {
    size_bytes: number | null;
    applications: number;
    health_checks: number;
    open_incidents: number;
  };
  warnings: string[];
}

export interface SystemConfig {
  version: string;
  auth_mode: string;
  operator_token: string;
  session_hours: number;
  allowed_hosts: string[];
  trusted_proxies: string[];
  target_policy: string;
  allowed_targets: string[];
  worker_enabled: boolean;
  monitor_max_concurrency: number;
  github: { api_url: string; token: string; sync_seconds: number };
  retention: { check_days: number; event_days: number; log_days: number; log_max_rows: number };
  data_dir: string;
  auto_migrate: boolean;
  log_level: string;
  log_format: string;
  api_docs: boolean;
  warnings: string[];
}
