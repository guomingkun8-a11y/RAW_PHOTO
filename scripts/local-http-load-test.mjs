import { performance } from "node:perf_hooks";

const backendBase = (process.env.LOADTEST_BASE_URL || "http://127.0.0.1:8002").replace(/\/$/, "");
const frontendBase = (process.env.LOADTEST_FRONTEND_URL || "http://127.0.0.1:4399").replace(/\/$/, "");
const username = process.env.LOADTEST_USERNAME || "admin";
const password = process.env.LOADTEST_PASSWORD || "admin123456";
const timeoutMs = Number(process.env.LOADTEST_TIMEOUT_MS || 10000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function percentile(sorted, p) {
  if (!sorted.length) return 0;
  const index = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[index];
}

function summarize(samples, durationMs) {
  const latencies = samples.map((sample) => sample.ms).sort((a, b) => a - b);
  const ok = samples.filter((sample) => sample.ok).length;
  const byEndpoint = {};
  for (const sample of samples) {
    const key = sample.name;
    byEndpoint[key] ||= { count: 0, ok: 0, errors: 0, statuses: {}, latencies: [] };
    byEndpoint[key].count += 1;
    byEndpoint[key].ok += sample.ok ? 1 : 0;
    byEndpoint[key].errors += sample.ok ? 0 : 1;
    byEndpoint[key].statuses[sample.status] = (byEndpoint[key].statuses[sample.status] || 0) + 1;
    byEndpoint[key].latencies.push(sample.ms);
  }
  for (const item of Object.values(byEndpoint)) {
    item.latencies.sort((a, b) => a - b);
    item.p95_ms = Math.round(percentile(item.latencies, 95));
    item.avg_ms = Math.round(item.latencies.reduce((sum, value) => sum + value, 0) / Math.max(1, item.latencies.length));
    delete item.latencies;
  }
  return {
    requests: samples.length,
    ok,
    errors: samples.length - ok,
    error_rate: Number(((samples.length - ok) / Math.max(1, samples.length)).toFixed(4)),
    rps: Number((samples.length / Math.max(0.001, durationMs / 1000)).toFixed(2)),
    avg_ms: Math.round(latencies.reduce((sum, value) => sum + value, 0) / Math.max(1, latencies.length)),
    min_ms: Math.round(latencies[0] || 0),
    p50_ms: Math.round(percentile(latencies, 50)),
    p90_ms: Math.round(percentile(latencies, 90)),
    p95_ms: Math.round(percentile(latencies, 95)),
    p99_ms: Math.round(percentile(latencies, 99)),
    max_ms: Math.round(latencies.at(-1) || 0),
    by_endpoint: byEndpoint,
  };
}

async function timedRequest(endpoint, token = "") {
  const started = performance.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = endpoint.headers ? { ...endpoint.headers } : {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(endpoint.url, {
      method: endpoint.method || "GET",
      headers,
      body: endpoint.body,
      signal: controller.signal,
    });
    await response.arrayBuffer();
    return {
      name: endpoint.name,
      status: response.status,
      ok: response.status >= 200 && response.status < 300,
      ms: performance.now() - started,
    };
  } catch (error) {
    return {
      name: endpoint.name,
      status: error?.name === "AbortError" ? "timeout" : "error",
      ok: false,
      ms: performance.now() - started,
    };
  } finally {
    clearTimeout(timer);
  }
}

async function login() {
  const response = await fetch(`${backendBase}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.token) {
    throw new Error(`login failed: ${response.status}`);
  }
  return payload.token;
}

async function logout(token) {
  if (!token) return;
  await fetch(`${backendBase}/api/auth/logout`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  }).catch(() => {});
}

async function runDurationScenario({ name, concurrency, durationMs, endpoints, token = "" }) {
  const samples = [];
  let next = 0;
  const deadline = performance.now() + durationMs;
  async function worker() {
    while (performance.now() < deadline) {
      const endpoint = endpoints[next++ % endpoints.length];
      samples.push(await timedRequest(endpoint, token));
    }
  }
  const started = performance.now();
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  return {
    name,
    concurrency,
    duration_seconds: Number(((performance.now() - started) / 1000).toFixed(2)),
    ...summarize(samples, performance.now() - started),
  };
}

async function runFixedScenario({ name, concurrency, totalRequests, requestFactory, cleanup }) {
  const samples = [];
  const cleanupItems = [];
  let startedCount = 0;
  async function worker() {
    while (startedCount < totalRequests) {
      const index = startedCount++;
      const { sample, cleanupItem } = await requestFactory(index);
      samples.push(sample);
      if (cleanupItem) cleanupItems.push(cleanupItem);
    }
  }
  const started = performance.now();
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  if (cleanup) {
    await cleanup(cleanupItems);
  }
  return {
    name,
    concurrency,
    total_requests: totalRequests,
    duration_seconds: Number(((performance.now() - started) / 1000).toFixed(2)),
    ...summarize(samples, performance.now() - started),
  };
}

const frontendEndpoints = [
  { name: "frontend:index", url: `${frontendBase}/` },
  { name: "frontend:vite-client", url: `${frontendBase}/@vite/client` },
  { name: "frontend:main-ts", url: `${frontendBase}/src/main.ts` },
  { name: "frontend:styles", url: `${frontendBase}/src/styles.css` },
  { name: "frontend:logo", url: `${frontendBase}/jiakemei-mark.svg` },
];

const publicEndpoints = [
  { name: "backend:health", url: `${backendBase}/health?format=json` },
  { name: "backend:version", url: `${backendBase}/version` },
  { name: "backend:captcha", url: `${backendBase}/auth/captcha` },
];

const readEndpoints = [
  { name: "auth:me", url: `${backendBase}/api/auth/me` },
  { name: "ai:models", url: `${backendBase}/v1/models` },
  { name: "tasks:list", url: `${backendBase}/api/image-tasks?ids=` },
  { name: "library:list", url: `${backendBase}/api/image-library?limit=20&offset=0` },
  { name: "library:health", url: `${backendBase}/api/image-library/health` },
  { name: "products:list", url: `${backendBase}/api/products?status=active` },
  { name: "templates:list", url: `${backendBase}/api/prompt-templates` },
  { name: "users:list", url: `${backendBase}/api/users` },
  { name: "settings:get", url: `${backendBase}/api/settings` },
  { name: "audit:list", url: `${backendBase}/api/audit-logs?limit=100` },
  { name: "monitoring:summary", url: `${backendBase}/api/monitoring/summary` },
];

const output = {
  started_at: new Date().toISOString(),
  backend_base_url: backendBase,
  frontend_base_url: frontendBase,
  timeout_ms: timeoutMs,
  scenarios: [],
};

const token = await login();
try {
  for (const concurrency of [20, 50, 100, 200]) {
    output.scenarios.push(await runDurationScenario({
      name: "frontend_static",
      concurrency,
      durationMs: 8000,
      endpoints: frontendEndpoints,
    }));
    await sleep(1000);
  }

  for (const concurrency of [20, 50, 100]) {
    output.scenarios.push(await runDurationScenario({
      name: "public_api_mixed",
      concurrency,
      durationMs: 8000,
      endpoints: publicEndpoints,
    }));
    await sleep(1000);
  }

  for (const concurrency of [10, 25, 50, 100]) {
    output.scenarios.push(await runDurationScenario({
      name: "authenticated_read_mixed",
      concurrency,
      durationMs: 12000,
      endpoints: readEndpoints,
      token,
    }));
    await sleep(1000);
  }

  for (const [concurrency, totalRequests] of [[1, 10], [3, 30], [5, 50], [10, 80]]) {
    output.scenarios.push(await runFixedScenario({
      name: "login_burst",
      concurrency,
      totalRequests,
      requestFactory: async () => {
        const started = performance.now();
        try {
          const response = await fetch(`${backendBase}/auth/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
          });
          const payload = await response.json().catch(() => ({}));
          return {
            sample: {
              name: "auth:login",
              status: response.status,
              ok: response.ok && Boolean(payload.token),
              ms: performance.now() - started,
            },
            cleanupItem: payload.token,
          };
        } catch {
          return {
            sample: {
              name: "auth:login",
              status: "error",
              ok: false,
              ms: performance.now() - started,
            },
          };
        }
      },
      cleanup: async (tokens) => {
        await Promise.all(tokens.map((item) => logout(item)));
      },
    }));
    await sleep(1000);
  }
} finally {
  await logout(token);
}

output.finished_at = new Date().toISOString();
console.log(JSON.stringify(output, null, 2));
