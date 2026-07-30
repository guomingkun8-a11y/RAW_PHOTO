import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const BASE_URL = (__ENV.LOADTEST_FRONTEND_URL || "http://host.docker.internal:4399").replace(/\/$/, "");
const VUS = Number(__ENV.LOADTEST_VUS || 20);
const DURATION = __ENV.LOADTEST_DURATION || "30s";
const SLEEP_SECONDS = Number(__ENV.LOADTEST_SLEEP_SECONDS || 0.1);
const FRONTEND_P95_MS = Number(__ENV.LOADTEST_FRONTEND_P95_MS || 1000);

const indexDuration = new Trend("frontend_index_duration");
const viteClientDuration = new Trend("frontend_vite_client_duration");
const mainDuration = new Trend("frontend_main_duration");
const stylesDuration = new Trend("frontend_styles_duration");
const logoDuration = new Trend("frontend_logo_duration");

export const options = {
  scenarios: {
    frontend_static: {
      executor: "constant-vus",
      vus: VUS,
      duration: DURATION,
      gracefulStop: "5s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
    "http_req_duration{kind:frontend}": [`p(95)<${FRONTEND_P95_MS}`],
  },
};

export default function () {
  const responses = http.batch([
    ["GET", `${BASE_URL}/`, null, { tags: { kind: "frontend", name: "GET /" } }],
    ["GET", `${BASE_URL}/@vite/client`, null, { tags: { kind: "frontend", name: "GET /@vite/client" } }],
    ["GET", `${BASE_URL}/src/main.ts`, null, { tags: { kind: "frontend", name: "GET /src/main.ts" } }],
    ["GET", `${BASE_URL}/src/styles.css`, null, { tags: { kind: "frontend", name: "GET /src/styles.css" } }],
    ["GET", `${BASE_URL}/jiakemei-mark.svg`, null, { tags: { kind: "frontend", name: "GET /jiakemei-mark.svg" } }],
  ]);

  indexDuration.add(responses[0].timings.duration);
  viteClientDuration.add(responses[1].timings.duration);
  mainDuration.add(responses[2].timings.duration);
  stylesDuration.add(responses[3].timings.duration);
  logoDuration.add(responses[4].timings.duration);

  check(responses[0], { "frontend index status is 200": (r) => r.status === 200 });
  check(responses[1], { "vite client status is 200": (r) => r.status === 200 });
  check(responses[2], { "frontend main status is 200": (r) => r.status === 200 });
  check(responses[3], { "frontend styles status is 200": (r) => r.status === 200 });
  check(responses[4], { "frontend logo status is 200": (r) => r.status === 200 });

  sleep(SLEEP_SECONDS);
}
