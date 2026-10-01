// Serves the briefing (static assets in out/) and, every 5 minutes, asks
// GitHub Actions to rebuild it. Cloudflare cron fires on time; GitHub's own
// scheduler often runs hours late.
// WORKFLOW (set in each wrangler config) picks which region's workflow to run.
const dispatchUrl = (workflow) =>
  `https://api.github.com/repos/janaleman/ra-briefing/actions/workflows/${workflow}/dispatches`;

export default {
  async fetch(request, env) {
    return env.ASSETS.fetch(request);
  },

  async scheduled(event, env) {
    if (!env.GH_DISPATCH_TOKEN) {
      console.log("GH_DISPATCH_TOKEN not set; relying on GitHub's schedule");
      return;
    }
    const res = await fetch(dispatchUrl(env.WORKFLOW || "briefing.yml"), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ra-briefing-cron",
      },
      body: JSON.stringify({ ref: "main" }),
    });
    if (!res.ok) console.error(`workflow dispatch failed: ${res.status} ${await res.text()}`);
  },
};
