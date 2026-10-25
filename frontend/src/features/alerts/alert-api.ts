import { httpRequest } from "@/lib/request";

export type AlertSettings = {
  enabled: boolean;
  environment: string;
  webhook_configured: boolean;
  check_interval_secs: number;
  cooldown_secs: number;
  repeat_secs: number;
  queue_waiting_threshold: number;
  dead_letter_threshold: number;
  stale_task_threshold: number;
  worker_missing_alert: boolean;
  recovery_notifications: boolean;
};

export type AlertMonitorStatus = {
  last_check_at: string;
  last_check_status: "never" | "paused" | "healthy" | "unhealthy" | "error" | "unavailable" | string;
  last_check_error: string;
  last_check_duration_ms: number;
  worker_id: string;
  updated_at: string;
};

export type AlertStatusResponse = {
  settings: AlertSettings;
  monitor: AlertMonitorStatus;
  webhooks: AlertWebhook[];
};

export type AlertWebhook = {
  id: string;
  name: string;
  enabled: boolean;
  receive_alerts: boolean;
  receive_feedback: boolean;
  url_hint: string;
  created_at: string;
};

export type AlertIncident = {
  incident_id: string;
  fingerprint: string;
  kind: string;
  source: string;
  title: string;
  summary: string;
  severity: "P0" | "P1" | "P2" | "P3" | string;
  state: "open" | "resolved" | string;
  occurrence_count: number;
  first_seen_at: string;
  last_seen_at: string;
  last_notified_at: string;
  resolved_at: string;
  updated_at: string;
};

export type AlertIncidentsResponse = {
  items: AlertIncident[];
  total: number;
  open_count: number;
};

export function fetchAlertStatus() {
  return httpRequest<AlertStatusResponse>(`/api/alerts?_t=${Date.now()}`);
}

export function updateAlertSettings(body: Partial<AlertSettings>) {
  return httpRequest<AlertStatusResponse>("/api/alerts/settings", {
    method: "PATCH",
    body,
  });
}

export function addAlertWebhook(body: { name: string; url: string }) {
  return httpRequest<AlertWebhook>("/api/alerts/webhooks", { method: "POST", body });
}

export function updateAlertWebhook(id: string, body: { name?: string; enabled?: boolean; receive_alerts?: boolean; receive_feedback?: boolean }) {
  return httpRequest<AlertWebhook>(`/api/alerts/webhooks/${encodeURIComponent(id)}`, { method: "PATCH", body });
}

export function deleteAlertWebhook(id: string) {
  return httpRequest<{ ok: boolean }>(`/api/alerts/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function sendAlertTest(id: string) {
  return httpRequest<{ ok: boolean }>(`/api/alerts/webhooks/${encodeURIComponent(id)}/test`, { method: "POST" });
}

export function runAlertCheck() {
  return httpRequest<{ status: string; findings: number; duration_ms: number; error?: string }>(
    "/api/alerts/check",
    { method: "POST" },
  );
}

export function fetchAlertIncidents(limit = 10) {
  return httpRequest<AlertIncidentsResponse>(`/api/alerts/incidents?limit=${limit}&_t=${Date.now()}`);
}
