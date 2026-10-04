import { test, mock, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import worker, { dispatchIdFor, dispatchWorkflow } from "../src/index.js";

const ENV = {
  GITHUB_DISPATCH_TOKEN: "test-token",
  GITHUB_OWNER: "srat0029-ui",
  GITHUB_REPO: "AFL",
  GITHUB_WORKFLOW: "nba-live-cycle.yml",
  GITHUB_REF: "master",
};

let logs;
beforeEach(() => {
  logs = [];
  mock.method(console, "log", (line) => logs.push(JSON.parse(line)));
});

function fakeFetch(status, body = "") {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    return new Response(status === 204 ? null : body, { status });
  };
  return { impl, calls };
}

test("dispatch id is derived from the scheduled time", () => {
  assert.equal(dispatchIdFor(Date.parse("2026-10-03T06:34:00.000Z")), "cf-20261003T0634Z");
});

test("dispatches run-live-cycle on master with the cloudflare trigger marker", async () => {
  const f = fakeFetch(204);
  await dispatchWorkflow(ENV, "cf-x", f.impl);
  assert.equal(f.calls.length, 1);
  const { url, init } = f.calls[0];
  assert.equal(url, "https://api.github.com/repos/srat0029-ui/AFL/actions/workflows/nba-live-cycle.yml/dispatches");
  assert.equal(init.method, "POST");
  assert.equal(init.headers.Authorization, "Bearer test-token");
  assert.ok(init.headers["User-Agent"]);
  assert.deepEqual(JSON.parse(init.body), {
    ref: "master",
    inputs: { command: "run-live-cycle", force: "false", trigger: "cloudflare-cron", dispatch_id: "cf-x" },
  });
});

test("204 is accepted without a run id", async () => {
  const result = await dispatchWorkflow(ENV, "cf-x", fakeFetch(204).impl);
  assert.equal(result.workflow_run_id, null);
  assert.equal(logs.at(-1).event, "dispatch_accepted");
});

test("200 is accepted and the run id GitHub returned is logged", async () => {
  const body = JSON.stringify({ workflow_run_id: 123, run_url: "u", html_url: "h" });
  const result = await dispatchWorkflow(ENV, "cf-x", fakeFetch(200, body).impl);
  assert.equal(result.workflow_run_id, 123);
  assert.deepEqual(logs.at(-1), { event: "dispatch_accepted", dispatch_id: "cf-x", status: 200, workflow_run_id: 123, html_url: "h" });
});

for (const status of [401, 403, 404, 422, 500]) {
  test(`HTTP ${status} is logged and thrown, with exactly one request`, async () => {
    const f = fakeFetch(status, '{"message":"nope"}');
    await assert.rejects(dispatchWorkflow(ENV, "cf-x", f.impl), new RegExp(`HTTP ${status}`));
    assert.equal(f.calls.length, 1);
    assert.deepEqual(logs.at(-1), { event: "dispatch_rejected", dispatch_id: "cf-x", status, body: '{"message":"nope"}' });
  });
}

test("a network error is logged and thrown, never retried", async () => {
  let calls = 0;
  const impl = async () => { calls++; throw new TypeError("network down"); };
  await assert.rejects(dispatchWorkflow(ENV, "cf-x", impl), /request failed/);
  assert.equal(calls, 1);
  assert.equal(logs.at(-1).event, "dispatch_error");
});

test("a missing token fails before any request and never logs the token", async () => {
  const f = fakeFetch(204);
  await assert.rejects(dispatchWorkflow({ ...ENV, GITHUB_DISPATCH_TOKEN: "" }, "cf-x", f.impl), /GITHUB_DISPATCH_TOKEN/);
  assert.equal(f.calls.length, 0);
});

test("the token never appears in any log line", async () => {
  await dispatchWorkflow(ENV, "cf-x", fakeFetch(204).impl);
  await assert.rejects(dispatchWorkflow(ENV, "cf-x", fakeFetch(401, "bad").impl));
  assert.ok(!JSON.stringify(logs).includes("test-token"));
});

test("scheduled handler logs trigger_fired, then dispatches", async () => {
  const f = fakeFetch(204);
  const original = globalThis.fetch;
  globalThis.fetch = f.impl;
  try {
    await worker.scheduled({ cron: "9,24,39,54 * * * *", scheduledTime: Date.parse("2026-10-03T06:49:00Z") }, ENV, {});
  } finally {
    globalThis.fetch = original;
  }
  assert.deepEqual(logs.map((l) => l.event), ["trigger_fired", "dispatch_accepted"]);
  assert.equal(logs[0].dispatch_id, "cf-20261003T0649Z");
  assert.equal(f.calls.length, 1);
});

test("the HTTP endpoint cannot trigger a dispatch", async () => {
  const res = await worker.fetch(new Request("https://example.test/"), ENV, {});
  assert.equal(res.status, 404);
});

test("wrangler config: cron, no committed secret", () => {
  const toml = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  assert.match(toml, /crons = \["9,24,39,54 \* \* \* \*"\]/);
  assert.doesNotMatch(toml, /^\s*GITHUB_DISPATCH_TOKEN\s*=/m);
  assert.doesNotMatch(toml, /gh[ps]_|github_pat_/);
});
