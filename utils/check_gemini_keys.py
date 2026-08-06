#!/usr/bin/env python3
"""Check Gemini API keys for HTTP status and common issues.

Usage:
  python utils/check_gemini_keys.py --model models/gemini-2.5-flash

Reads GEMINI_API_KEY from environment (JSON list) or .env.
Prints a short diagnostic for each key and suggested actions.
"""
import argparse
import asyncio
import aiohttp
import os
import json
import time
from dotenv import load_dotenv
from textwrap import shorten

load_dotenv()

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

async def check_key(session, api_key, model, index, timeout=30):
    url = f"{BASE_URL}/{model}:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": api_key}
    payload = {
        "contents": [{"parts": [{"text": "Health check: hello"}], "role": "user"}],
        "generationConfig": {"temperature": 0.0}
    }
    start = time.time()
    try:
        async with session.post(url, json=payload, headers=headers, params=params, timeout=timeout) as resp:
            elapsed = time.time() - start
            try:
                body = await resp.text()
            except Exception:
                body = "<unreadable>"
            summary = shorten(body, width=800, placeholder="...")
            return {
                "index": index,
                "status": resp.status,
                "time": round(elapsed, 2),
                "body": summary,
            }
    except asyncio.TimeoutError:
        return {"index": index, "status": "timeout", "time": None, "body": "Request timed out"}
    except Exception as e:
        return {"index": index, "status": "exception", "time": None, "body": str(e)}

async def main(args):
    api_keys_str = os.getenv("GEMINI_API_KEY")
    if not api_keys_str:
        print("GEMINI_API_KEY not set in environment. Put a JSON list of keys in .env or environment variable.")
        return 1

    # parse JSON list or single key
    try:
        keys = json.loads(api_keys_str)
        if isinstance(keys, str):
            keys = [keys]
    except Exception:
        # fallback: newline or comma separated
        keys = [k.strip() for k in api_keys_str.replace('\n', ',').split(',') if k.strip()]

    if not keys:
        print("No API keys found in GEMINI_API_KEY")
        return 1

    print(f"Found {len(keys)} keys; testing model: {args.model}")

    timeout = aiohttp.ClientTimeout(total=args.timeout)
    connector = aiohttp.TCPConnector(limit=args.concurrency if args.concurrency>0 else 1)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        results = []
        # run sequentially to avoid producing more 429s from the checker itself
        for i, k in enumerate(keys):
            print(f"Checking key {i+1}/{len(keys)}...")
            res = await check_key(session, k, args.model, i+1, timeout=args.timeout)
            results.append(res)
            # brief pause to reduce rapid-fire requests
            await asyncio.sleep(args.delay)

    # Report
    print('\nResults:')
    for r in results:
        status = r.get('status')
        body = r.get('body', '')
        t = r.get('time')
        print(f"Key {r['index']}: status={status} time={t}s")
        if isinstance(status, int) and status == 200:
            # look for empty body or promptFeedback
            if not body or 'candidates' not in body and len(body) < 10:
                print("  -> 200 but empty or unexpected body; model may be disabled or response schema changed.")
        elif status == 429:
            print("  -> Rate limited (429). Possible causes: too many requests, project quota exceeded, or key abused.")
        elif status == 403:
            print("  -> Forbidden (403). Likely the Generative Language API is not enabled for the project or billing is disabled.")
        elif status == 400:
            print("  -> Bad request (400). Check payload and model name; the model may not be available for this key.")
        elif status == 'timeout':
            print("  -> Request timed out. Try increasing timeout or checking network connectivity.")
        elif status == 'exception':
            print(f"  -> Exception: {body}")
        else:
            print(f"  -> Response body: {shorten(body, width=300)}")

    # Suggestions
    print('\nSuggestions:')
    print('- If you see many 403s: enable the Generative Language API and ensure billing is enabled for those projects.')
    print('- If you see many 429s: reduce concurrency, add delays, implement exponential backoff, or request higher quota from Google Cloud.')
    print('- If responses are 200 but empty/invalid: verify the model name (`--model`) matches available models and that the key has access.')
    print('- Double-check that GEMINI_API_KEY contains valid API keys (not expired) and belongs to projects with the API enabled.')

    return 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Check Gemini API keys status')
    parser.add_argument('--model', default='models/gemini-2.5-flash', help='Model identifier (use same format as main.py)')
    parser.add_argument('--concurrency', type=int, default=1, help='Max concurrent requests (default 1)')
    parser.add_argument('--delay', type=float, default=0.5, help='Delay between checks in seconds (default 0.5)')
    parser.add_argument('--timeout', type=int, default=30, help='Request timeout seconds')
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args)))
