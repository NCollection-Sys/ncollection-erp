// ============================================================================
//  P3-T03 — Odoo load test (k6)
// ============================================================================
//  Simulates concurrent interactive users across N tenant databases, routed by
//  Host under db_filter=^%d$ (the production routing model, proven by P1-T06).
//  Each VU logs in ONCE against its assigned tenant (the k6 per-VU cookie jar
//  persists across iterations), then repeatedly issues a representative
//  authenticated read — a res.partner web_search_read, the query behind every
//  list view. Latency is asserted against the §9 interactive budget (p95<500ms).
//
//  Parameterised entirely by env, so the SAME script drives the small dev-box
//  baseline and the full 50-VU × 3-tenant staging run:
//    BASE_URL  edge base (default http://nginx — the compose service)
//    TARGETS   JSON [{sub,db,login,password}, ...] (round-robined across VUs)
//    STAGES    k6 ramping-vus stages JSON
//  Run via scripts/perf/run_load_test.sh (brings up the routing stack + k6).
// ============================================================================
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://nginx';
const TARGETS = JSON.parse(
  __ENV.TARGETS ||
  '[{"sub":"ncollection.localhost","db":"ncollection","login":"admin","password":"admin"}]'
);
const STAGES = JSON.parse(
  __ENV.STAGES ||
  '[{"duration":"15s","target":10},{"duration":"30s","target":10},{"duration":"10s","target":0}]'
);

const loginMs = new Trend('odoo_login_ms', true);
const readMs = new Trend('odoo_read_ms', true);

// VUS set  -> a single fixed-concurrency point (the runner sweeps these to draw
//             the load curve). Otherwise ramp through STAGES.
const VUS = __ENV.VUS ? parseInt(__ENV.VUS, 10) : 0;
const DURATION = __ENV.DURATION || '20s';
const THRESHOLDS = {
  http_req_failed: ['rate<0.01'],         // <1% transport errors
  odoo_read_ms: ['p(95)<500'],            // §9 interactive budget
  checks: ['rate>0.99'],                  // fail loudly if auth/read breaks (not a silent clean run)
};

export const options = VUS > 0
  ? {
      scenarios: { interactive: { executor: 'constant-vus', vus: VUS, duration: DURATION } },
      thresholds: THRESHOLDS,
    }
  : {
      scenarios: {
        interactive: { executor: 'ramping-vus', startVUs: 0, stages: STAGES, gracefulRampDown: '5s' },
      },
      thresholds: THRESHOLDS,
    };

function targetFor(vu) {
  return TARGETS[(vu - 1) % TARGETS.length];
}

// Module scope IS per-VU in k6 (each VU is its own JS runtime), so this caches
// the session for the life of the VU — we log in ONCE and carry the session_id
// EXPLICITLY on every read. (k6's implicit cookie jar does not reliably survive
// across iterations here, which would silently session-expire every read.)
let session = null;

export default function () {
  const t = targetFor(__VU);
  const jsonHeaders = { Host: t.sub, 'Content-Type': 'application/json' };

  if (session === null) {
    const res = http.post(
      `${BASE}/web/session/authenticate`,
      JSON.stringify({
        jsonrpc: '2.0',
        method: 'call',
        params: { db: t.db, login: t.login, password: t.password },
      }),
      // NB: NO X-Odoo-Database header here — on /web/session/authenticate it
      // makes Odoo skip the Set-Cookie, so the session never sticks. Host +
      // db_filter already route the DB (CLAUDE.md Odoo-19 gotcha).
      { headers: jsonHeaders }
    );
    loginMs.add(res.timings.duration);
    const cookie = res.cookies.session_id;
    const uid = res.json('result.uid');
    check(res, { 'login ok': () => uid !== null && uid !== undefined && !!cookie });
    session = cookie && cookie.length ? cookie[0].value : '';
  }

  // Representative interactive read: the list-view query (res.partner), with the
  // session carried explicitly so it survives across iterations.
  const read = http.post(
    `${BASE}/web/dataset/call_kw`,
    JSON.stringify({
      jsonrpc: '2.0',
      method: 'call',
      params: {
        model: 'res.partner',
        method: 'web_search_read',
        args: [],
        kwargs: {
          domain: [],
          specification: { display_name: {}, email: {} },
          limit: 80,
        },
      },
    }),
    { headers: { ...jsonHeaders, Cookie: `session_id=${session}` } }
  );
  readMs.add(read.timings.duration);
  // Odoo returns HTTP 200 with a JSON-RPC `error` envelope on session/auth
  // failure, so status alone is not enough — assert the app-layer succeeded.
  check(read, { 'read ok': (r) => r.status === 200 && !r.json('error') });

  sleep(1);
}
