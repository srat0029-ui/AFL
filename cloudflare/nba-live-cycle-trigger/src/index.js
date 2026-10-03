// Cloudflare cron -> GitHub workflow_dispatch for the NBA Live Cycle.
//
// One scheduled event makes exactly one dispatch request. There are no
// retries: a failed dispatch is logged and thrown (so Cloudflare marks the
// invocation failed), and GitHub's own `schedule:` trigger remains the
// fallback. Nothing here touches the database.
//
// Log lines are single-line JSON with an "event" field:
//   trigger_fired      the cron fired (or a test invocation)
//   dispatch_accepted  GitHub accepted the dispatch (NOT proof the run
//                      started or succeeded - check the run itself)
//   dispatch_rejected  GitHub answered with a non-success status
//   dispatch_error     no answer (network error, timeout, missing config)

const DISPATCH_TIMEOUT_MS = 15_000;
const GITHUB_API = "https://api.github.com";

function log(event, fields) {
  console.log(JSON.stringify({ event, ...fields }));
}

// "2026-10-03T06:34:00.000Z" -> "cf-20261003T0634Z": unique per cron tick,
// readable in the GitHub run name, and searchable in both systems' logs.
export function dispatchIdFor(scheduledTime) {
  const iso = new Date(scheduledTime).toISOString();
  return `cf-${iso.slice(0, 16).replace(/[-:]/g, "")}Z`;
}

export async function dispatchWorkflow(env, dispatchId, fetchImpl = fetch) {
  for (const name of ["GITHUB_DISPATCH_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "GITHUB_WORKFLOW", "GITHUB_REF"]) {
    if (!env[name]) {
      log("dispatch_error", { dispatch_id: dispatchId, error: `missing ${name}` });
      throw new Error(`NBA dispatch ${dispatchId}: ${name} is not configured`);
    }
  }

  const url = `${GITHUB_API}/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`;
  const body = {
    ref: env.GITHUB_REF,
    inputs: {
      command: "run-live-cycle",
      force: "false",
      trigger: "cloudflare-cron",
      dispatch_id: dispatchId,
    },
  };

  let response;
  try {
    response = await fetchImpl(url, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "nba-live-cycle-trigger",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(DISPATCH_TIMEOUT_MS),
    });
  } catch (err) {
    log("dispatch_error", { dispatch_id: dispatchId, error: String(err) });
    throw new Error(`NBA dispatch ${dispatchId}: request failed: ${err}`);
  }

  const text = await response.text();

  if (response.status !== 200 && response.status !== 204) {
    // GitHub error bodies are short JSON messages; cap it anyway.
    log("dispatch_rejected", { dispatch_id: dispatchId, status: response.status, body: text.slice(0, 500) });
    throw new Error(`NBA dispatch ${dispatchId}: GitHub returned HTTP ${response.status}`);
  }

  // GitHub currently answers 200 with the new run's id; older behaviour was
  // 204 with no body. Either is acceptance; the run id is logged when given.
  let run = {};
  if (response.status === 200 && text) {
    try {
      run = JSON.parse(text);
    } catch {
      // Accepted all the same; the run can be found by its dispatch id.
    }
  }
  const accepted = {
    dispatch_id: dispatchId,
    status: response.status,
    workflow_run_id: run.workflow_run_id ?? null,
    html_url: run.html_url ?? null,
  };
  log("dispatch_accepted", accepted);
  return accepted;
}

export default {
  async scheduled(controller, env, ctx) {
    const dispatchId = dispatchIdFor(controller.scheduledTime);
    log("trigger_fired", {
      dispatch_id: dispatchId,
      cron: controller.cron,
      scheduled_time: new Date(controller.scheduledTime).toISOString(),
    });
    await dispatchWorkflow(env, dispatchId);
  },

  // No HTTP interface: nobody can trigger a dispatch through a URL.
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
