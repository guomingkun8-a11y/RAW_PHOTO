import http from "k6/http";
import { check, group, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE_URL = (__ENV.LOADTEST_BASE_URL || __ENV.K6_BASE_URL || "http://host.docker.internal:8002").replace(/\/$/, "");
const USERNAME = __ENV.LOADTEST_USERNAME || __ENV.K6_USERNAME || "admin";
const PASSWORD = __ENV.LOADTEST_PASSWORD || __ENV.K6_PASSWORD || "admin123456";
const VUS = Number(__ENV.LOADTEST_VUS || 10);
const DURATION = __ENV.LOADTEST_DURATION || "30s";
const LOGIN_EACH_ITERATION = String(__ENV.LOADTEST_LOGIN_EACH_ITERATION || "false").toLowerCase() === "true";
const SLEEP_SECONDS = Number(__ENV.LOADTEST_SLEEP_SECONDS || 1);
const PUBLIC_P95_MS = Number(__ENV.LOADTEST_PUBLIC_P95_MS || 500);
const AUTH_P95_MS = Number(__ENV.LOADTEST_AUTH_P95_MS || 1000);
const READ_P95_MS = Number(__ENV.LOADTEST_READ_P95_MS || 1500);
const INCLUDE_LIBRARY = String(__ENV.LOADTEST_INCLUDE_LIBRARY || "true").toLowerCase() === "true";
const INCLUDE_LIBRARY_HEALTH = String(__ENV.LOADTEST_INCLUDE_LIBRARY_HEALTH || "true").toLowerCase() === "true";
const INCLUDE_TASKS = String(__ENV.LOADTEST_INCLUDE_TASKS || "true").toLowerCase() === "true";
const INCLUDE_MODELS = String(__ENV.LOADTEST_INCLUDE_MODELS || "true").toLowerCase() === "true";
const INCLUDE_PRODUCTS = String(__ENV.LOADTEST_INCLUDE_PRODUCTS || "true").toLowerCase() === "true";
const INCLUDE_PROMPT_TEMPLATES = String(__ENV.LOADTEST_INCLUDE_PROMPT_TEMPLATES || "true").toLowerCase() === "true";
const INCLUDE_USERS = String(__ENV.LOADTEST_INCLUDE_USERS || "true").toLowerCase() === "true";
const INCLUDE_SETTINGS = String(__ENV.LOADTEST_INCLUDE_SETTINGS || "true").toLowerCase() === "true";
const INCLUDE_AUDIT_LOGS = String(__ENV.LOADTEST_INCLUDE_AUDIT_LOGS || "true").toLowerCase() === "true";
const INCLUDE_MONITORING = String(__ENV.LOADTEST_INCLUDE_MONITORING || "true").toLowerCase() === "true";

const healthDuration = new Trend("health_duration");
const versionDuration = new Trend("version_duration");
const authMeDuration = new Trend("auth_me_duration");
const modelsDuration = new Trend("models_duration");
const tasksDuration = new Trend("tasks_duration");
const libraryDuration = new Trend("library_duration");
const libraryHealthDuration = new Trend("library_health_duration");
const usersDuration = new Trend("users_duration");
const settingsDuration = new Trend("settings_duration");
const productsDuration = new Trend("products_duration");
const templatesDuration = new Trend("templates_duration");
const auditLogsDuration = new Trend("audit_logs_duration");
const monitoringDuration = new Trend("monitoring_duration");

export const options = {
  scenarios: {
    baseline: {
      executor: "constant-vus",
      vus: VUS,
      duration: DURATION,
      gracefulStop: "5s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
    "http_req_duration{kind:public}": [`p(95)<${PUBLIC_P95_MS}`],
    "http_req_duration{kind:auth}": [`p(95)<${AUTH_P95_MS}`],
    "http_req_duration{kind:read}": [`p(95)<${READ_P95_MS}`],
  },
};

function jsonHeaders(token) {
  const headers = {
    "Content-Type": "application/json",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

function loginOnce() {
  const res = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({
      username: USERNAME,
      password: PASSWORD,
    }),
    {
      headers: jsonHeaders(),
      tags: { kind: "auth", name: "POST /auth/login" },
    },
  );

  check(res, {
    "login status is 200": (r) => r.status === 200,
    "login returns token": (r) => Boolean(r.json("token")),
  });

  const token = res.json("token");
  if (!token) {
    throw new Error(`Login failed: ${res.status} ${res.body}`);
  }
  return token;
}

function addReadRequest(requests, steps, authHeaders, name, url, metric) {
  requests.push([
    "GET",
    url,
    null,
    {
      headers: authHeaders,
      tags: { kind: "read", name },
    },
  ]);
  steps.push({ name, metric });
}

export function setup() {
  console.log(`k6 base url: ${BASE_URL}`);
  if (LOGIN_EACH_ITERATION) {
    return { token: "" };
  }
  return { token: loginOnce() };
}

export default function (data) {
  const token = LOGIN_EACH_ITERATION ? loginOnce() : data.token;
  const authHeaders = jsonHeaders(token);

  group("public", () => {
    const health = http.get(`${BASE_URL}/health?format=json`, {
      tags: { kind: "public", name: "GET /health" },
    });
    const version = http.get(`${BASE_URL}/version`, {
      tags: { kind: "public", name: "GET /version" },
    });
    healthDuration.add(health.timings.duration);
    versionDuration.add(version.timings.duration);

    check(health, {
      "health status is 200": (r) => r.status === 200,
      "health is ok": (r) => r.json("status") === "ok",
    });
    check(version, {
      "version status is 200": (r) => r.status === 200,
      "version is present": (r) => Boolean(r.json("version")),
    });
  });

  group("read", () => {
    const requests = [];
    const steps = [];
    addReadRequest(requests, steps, authHeaders, "GET /api/auth/me", `${BASE_URL}/api/auth/me`, authMeDuration);
    if (INCLUDE_MODELS) addReadRequest(requests, steps, authHeaders, "GET /v1/models", `${BASE_URL}/v1/models`, modelsDuration);
    if (INCLUDE_TASKS) addReadRequest(requests, steps, authHeaders, "GET /api/image-tasks", `${BASE_URL}/api/image-tasks?ids=`, tasksDuration);
    if (INCLUDE_LIBRARY) addReadRequest(requests, steps, authHeaders, "GET /api/image-library", `${BASE_URL}/api/image-library?limit=20&offset=0`, libraryDuration);
    if (INCLUDE_LIBRARY_HEALTH) addReadRequest(requests, steps, authHeaders, "GET /api/image-library/health", `${BASE_URL}/api/image-library/health`, libraryHealthDuration);
    if (INCLUDE_PRODUCTS) addReadRequest(requests, steps, authHeaders, "GET /api/products", `${BASE_URL}/api/products`, productsDuration);
    if (INCLUDE_PROMPT_TEMPLATES) addReadRequest(requests, steps, authHeaders, "GET /api/prompt-templates", `${BASE_URL}/api/prompt-templates`, templatesDuration);
    if (INCLUDE_USERS) addReadRequest(requests, steps, authHeaders, "GET /api/users", `${BASE_URL}/api/users`, usersDuration);
    if (INCLUDE_SETTINGS) addReadRequest(requests, steps, authHeaders, "GET /api/settings", `${BASE_URL}/api/settings`, settingsDuration);
    if (INCLUDE_AUDIT_LOGS) addReadRequest(requests, steps, authHeaders, "GET /api/audit-logs", `${BASE_URL}/api/audit-logs`, auditLogsDuration);
    if (INCLUDE_MONITORING) addReadRequest(requests, steps, authHeaders, "GET /api/monitoring/summary", `${BASE_URL}/api/monitoring/summary`, monitoringDuration);

    const responses = http.batch(requests);
    for (let i = 0; i < responses.length; i += 1) {
      const response = responses[i];
      const step = steps[i];
      step.metric.add(response.timings.duration);
      check(response, {
        [`${step.name} status is 200`]: (r) => r.status === 200,
      });
    }
  });

  sleep(SLEEP_SECONDS);
}
