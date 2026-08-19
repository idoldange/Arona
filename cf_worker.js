/**
 * Cloudflare Worker — transparent reverse proxy in front of the Gemini API.
 *
 * Deployed so Arona Bot's Gemini traffic can be routed through Cloudflare's edge
 * (e.g. to change the egress IP away from the VPS's, or route around a regional
 * block) without main.py needing to know or care about proxying — it just points
 * base_url at this worker instead of generativelanguage.googleapis.com. Whatever
 * path/query/method/headers/body main.py sends, this worker forwards as-is to the
 * real API and streams the response straight back, unmodified.
 *
 * main.py side (already wired up):
 *   base_url = CF_WORKER_URL + "/v1beta"
 *   POST {base_url}/models/{model}:generateContent   (streamed or not)
 *   header: x-goog-api-key: <the actual Gemini key>
 *
 * So this worker never needs to see or hold a Gemini key itself — main.py sends the
 * real key with every request via x-goog-api-key, exactly like it would to Google
 * directly, and this worker just passes that header straight through.
 *
 * ── Deploy ──────────────────────────────────────────────────────────────────
 *   npm install -g wrangler        (if you don't have it)
 *   wrangler login
 *   wrangler deploy cf_worker.js --name arona-gemini-proxy --compatibility-date 2026-08-19
 * This prints the worker's URL — put that in .env as:
 *   CF_WORKER_URL=https://arona-gemini-proxy.<your-subdomain>.workers.dev
 * and flip USE_CF_WORKER_PROXY = True in config.py.
 *
 * ── Optional hardening ──────────────────────────────────────────────────────
 * Set a WORKER_SHARED_SECRET in the Worker's environment (wrangler secret put
 * WORKER_SHARED_SECRET) and uncomment the check block below, then send the same
 * value from main.py as an extra header (e.g. "x-worker-secret") so randoms who
 * find the *.workers.dev URL can't use it as an open Gemini relay on your dime.
 */

const GEMINI_UPSTREAM = "https://generativelanguage.googleapis.com";

// Hop-by-hop / connection-specific headers that must never be forwarded verbatim
// between a client and an upstream (per the HTTP spec) — Cloudflare Workers reject
// or mishandle some of these if passed straight through.
const STRIPPED_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
  "cf-connecting-ip",
  "cf-ray",
  "cf-visitor",
  "x-forwarded-for",
  "x-forwarded-proto",
]);

export default {
  async fetch(request, env, ctx) {
    // Optional shared-secret gate — see "Optional hardening" above.
    // if (env.WORKER_SHARED_SECRET && request.headers.get("x-worker-secret") !== env.WORKER_SHARED_SECRET) {
    //   return new Response("Forbidden", { status: 403 });
    // }

    const inUrl = new URL(request.url);
    const upstreamUrl = new URL(GEMINI_UPSTREAM);
    upstreamUrl.pathname = inUrl.pathname;
    upstreamUrl.search = inUrl.search;

    const headers = new Headers();
    for (const [key, value] of request.headers) {
      if (!STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) {
        headers.set(key, value);
      }
    }

    const upstreamRequest = new Request(upstreamUrl.toString(), {
      method: request.method,
      headers,
      // GET/HEAD can't carry a body; everything else (generateContent POSTs) does.
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "follow",
    });

    let upstreamResponse;
    try {
      upstreamResponse = await fetch(upstreamRequest);
    } catch (err) {
      return new Response(
        JSON.stringify({ error: { code: 502, message: `Worker proxy fetch failed: ${err.message}` } }),
        { status: 502, headers: { "content-type": "application/json" } }
      );
    }

    // Stream the response straight back (works for both plain JSON and
    // streamGenerateContent's chunked/SSE-style responses) without buffering.
    const respHeaders = new Headers(upstreamResponse.headers);
    respHeaders.delete("content-encoding"); // fetch() already decoded it; avoid double-decoding on the client
    respHeaders.delete("content-length");   // length no longer matches after the above

    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
      headers: respHeaders,
    });
  },
};
