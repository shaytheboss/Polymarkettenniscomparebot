/**
 * Cloudflare Worker — Polymarket API Relay
 *
 * Polymarket's APIs (gamma-api, clob) block all cloud hosting IPs
 * (Railway, AWS, GCP) via Cloudflare WAF. This worker runs on Cloudflare's
 * edge and forwards requests from a Cloudflare IP, which Polymarket allows.
 *
 * Routes:
 *   /gamma/*  →  gamma-api.polymarket.com/*   (market search & metadata)
 *   /clob/*   →  clob.polymarket.com/*         (real-time CLOB prices)
 *   /*        →  gamma-api.polymarket.com/*    (default fallback)
 *
 * Deploy (free, 2 minutes):
 *   1. cloudflare.com → Workers & Pages → Create Application → Create Worker
 *   2. Paste this entire file, click Save and Deploy
 *   3. Copy the worker URL: https://polymarket-relay.YOUR-NAME.workers.dev
 *   4. Railway → Settings → Variables → add:
 *        POLYMARKET_RELAY_URL = https://polymarket-relay.YOUR-NAME.workers.dev
 *   5. Redeploy Railway
 *
 * Free tier: 100,000 requests/day — well within budget.
 */

const ROUTES = {
  "/gamma": "gamma-api.polymarket.com",
  "/clob":  "clob.polymarket.com",
};

const HEADERS = {
  Accept: "application/json",
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
  Referer: "https://polymarket.com/",
  Origin:  "https://polymarket.com",
};

export default {
  async fetch(request) {
    const url = new URL(request.url);
    let targetHost = "gamma-api.polymarket.com"; // default
    let targetPath = url.pathname + url.search;

    // Check prefix routing: /gamma/* or /clob/*
    for (const [prefix, host] of Object.entries(ROUTES)) {
      if (url.pathname.startsWith(prefix + "/") || url.pathname === prefix) {
        targetHost = host;
        targetPath = url.pathname.slice(prefix.length) + url.search;
        if (!targetPath.startsWith("/")) targetPath = "/" + targetPath;
        break;
      }
    }

    const targetUrl = `https://${targetHost}${targetPath}`;

    let response;
    try {
      response = await fetch(targetUrl, {
        method: "GET",
        headers: HEADERS,
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
        "Content-Type": response.headers.get("Content-Type") || "application/json",
        "Access-Control-Allow-Origin": "*",
      },
    });
  },
};
