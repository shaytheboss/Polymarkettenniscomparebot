/**
 * Cloudflare Worker — Polymarket API Relay
 *
 * Polymarket's gamma-api.polymarket.com blocks all cloud hosting IPs
 * (Railway, AWS, GCP) via Cloudflare WAF. Cloudflare Workers come from
 * Cloudflare's own IP range, which Polymarket doesn't block.
 *
 * Deploy:
 *   1. cloudflare.com → Workers & Pages → Create Application → Create Worker
 *   2. Paste this entire file into the editor
 *   3. Click "Save and Deploy"
 *   4. Copy the worker URL (e.g. https://polymarket-relay.YOUR-NAME.workers.dev)
 *   5. In Railway: Settings → Variables → add:
 *        POLYMARKET_RELAY_URL = https://polymarket-relay.YOUR-NAME.workers.dev
 *   6. Redeploy Railway app
 *
 * Free tier: 100,000 requests/day — more than enough.
 */

const ALLOWED_HOST = "gamma-api.polymarket.com";

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // Only forward to gamma-api.polymarket.com — no open-proxy abuse
    const target = `https://${ALLOWED_HOST}${url.pathname}${url.search}`;

    let response;
    try {
      response = await fetch(target, {
        method: "GET",
        headers: {
          Accept: "application/json",
          "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
          Referer: "https://polymarket.com/",
          Origin: "https://polymarket.com",
        },
        redirect: "follow",
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: String(err) }), {
        status: 502,
        headers: { "Content-Type": "application/json" },
      });
    }

    const body = await response.arrayBuffer();
    return new Response(body, {
      status: response.status,
      headers: {
        "Content-Type":
          response.headers.get("Content-Type") || "application/json",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};
