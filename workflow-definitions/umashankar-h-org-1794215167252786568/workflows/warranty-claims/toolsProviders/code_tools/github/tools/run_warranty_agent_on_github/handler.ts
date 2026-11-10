import type { ToolHandlerFn } from "@/types";

type Input = { wo_id: string };
type HttpResult = { status: number; headers: Record<string, string>; body: unknown };
type GitHubRun = {
  id: number;
  status: string;
  conclusion: string | null;
  html_url: string;
  display_title?: string;
  jobs_url: string;
};
type GitHubJob = { id: number; name: string };
type Annotation = { title?: string; message?: string };
type AgentResult = {
  event_id: string | null;
  wo_id: string;
  status: string;
  component: string | null;
  policy_id: string | null;
  claim_number: string | null;
  claim_value: number | null;
  reason: string;
  narrative_3c: string;
  needed_approval: boolean;
  needed_tech_info: boolean;
};

const REPO_API = "https://api.github.com/repos/UmashankarKH/rb";
const WORKFLOW_FILE = "warranty-agent.yml";
const AGENT_JOB = "run-agent";
const POLL_MS = 15000;
const MAX_POLLS = 24; // 24 x 15 s = 6 minutes, inside the tool's 9-minute timeout

export default (async ({ input, ctx }) => {
  const woId = String(input.wo_id ?? "").trim().toUpperCase();
  if (!/^WO-\d{4}$/.test(woId)) throw new Error(`"${input.wo_id}" is not a work order id such as WO-1001.`);

  // One dispatch per work order per run, reused if the tool is called again.
  const storeKey = `dispatch:${woId}`;
  let eventId = await ctx.store.get<string>(storeKey);
  if (!eventId) {
    eventId = `rb-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    await ctx.store.set(storeKey, eventId);
  }

  const creds = await ctx.executeIntegration<Record<string, unknown>>({
    connectionConfigId: "github-token",
    actionId: "get-credentials",
    params: {},
  });
  const raw = creds.GITHUB_TOKEN as unknown;
  // A stored value may arrive as a string or as an object holding it.
  const token =
    typeof raw === "string"
      ? raw.trim()
      : raw && typeof raw === "object" && typeof (raw as { value?: unknown }).value === "string"
        ? ((raw as { value: string }).value).trim()
        : "";
  if (!token) {
    // Describe the shape only, never the value.
    const shape = raw === null ? "null" : Array.isArray(raw) ? "array" : typeof raw;
    const keys = raw && typeof raw === "object" ? Object.keys(raw as object).join(",") : "";
    throw new Error(
      `The github-token connection's GITHUB_TOKEN is empty (type ${shape}${keys ? `, keys ${keys}` : ""}; connection keys: ${Object.keys(creds).join(",")}). Check its value in the Runbook app.`,
    );
  }
  const headers = {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "runbook-warranty-claims",
  };

  const sent = await ctx.executeIntegration<HttpResult>({
    connectionConfigId: "github",
    actionId: "post",
    params: {
      url: `${REPO_API}/dispatches`,
      headers,
      body: { event_type: "work_order.closed", client_payload: { wo_id: woId, event_id: eventId, source: "runbook" } },
    },
  });
  if (sent.status !== 204) throw new Error(`GitHub did not accept the work_order.closed event: HTTP ${sent.status}.`);
  await ctx.log(`Dispatched work_order.closed for ${woId} as ${eventId}`);

  let run: GitHubRun | null = null;
  for (let poll = 0; poll < MAX_POLLS; poll++) {
    await ctx.wait(POLL_MS);
    const list = await ctx.executeIntegration<HttpResult>({
      connectionConfigId: "github",
      actionId: "get",
      params: {
        url: `${REPO_API}/actions/workflows/${WORKFLOW_FILE}/runs`,
        headers,
        queryParams: { event: "repository_dispatch", per_page: "20" },
      },
    });
    const runs = ((list.body as { workflow_runs?: GitHubRun[] })?.workflow_runs ?? []) as GitHubRun[];
    run = runs.find((r) => String(r.display_title ?? "").includes(eventId as string)) ?? null;
    if (run && run.status === "completed") break;
  }

  if (!run) return { status: "not_started", wo_id: woId, event_id: eventId };
  if (run.status !== "completed") return { status: "still_running", wo_id: woId, event_id: eventId, run_url: run.html_url };
  if (run.conclusion !== "success") {
    return { status: "github_failed", wo_id: woId, event_id: eventId, conclusion: run.conclusion, run_url: run.html_url };
  }

  const jobsResp = await ctx.executeIntegration<HttpResult>({
    connectionConfigId: "github",
    actionId: "get",
    params: { url: run.jobs_url, headers },
  });
  const jobs = ((jobsResp.body as { jobs?: GitHubJob[] })?.jobs ?? []) as GitHubJob[];
  const job = jobs.find((j) => j.name === AGENT_JOB);
  if (!job) return { status: "github_failed", wo_id: woId, event_id: eventId, conclusion: "no run-agent job", run_url: run.html_url };

  const annResp = await ctx.executeIntegration<HttpResult>({
    connectionConfigId: "github",
    actionId: "get",
    params: { url: `${REPO_API}/check-runs/${job.id}/annotations`, headers },
  });
  const annotations = (Array.isArray(annResp.body) ? annResp.body : []) as Annotation[];
  let result: AgentResult | null = null;
  for (const a of annotations) {
    if (a.title !== "WARRANTY_RESULT" || !a.message) continue;
    let parsed: AgentResult | null = null;
    try {
      parsed = JSON.parse(a.message) as AgentResult;
    } catch {
      parsed = null;
    }
    if (parsed && parsed.wo_id === woId) result = parsed;
  }
  if (!result) {
    return { status: "github_failed", wo_id: woId, event_id: eventId, conclusion: "no WARRANTY_RESULT annotation", run_url: run.html_url };
  }
  return { status: "completed", run_url: run.html_url, result };
}) satisfies ToolHandlerFn<Input>;
