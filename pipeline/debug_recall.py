"""Simple reproduction script to POST /api/v1/recall and print response details.

Run from the repo root inside the activated venv:

  python pipeline/debug_recall.py

Ensure your `.env` or environment has `MEMCLAW_API_URL`, `MEMCLAW_API_KEY`,
`MEMCLAW_TENANT_ID`, and `MEMCLAW_FLEET_ID` set.
"""
import os
import json
import requests


def main():
    base = os.environ.get("MEMCLAW_API_URL", "https://memclaw.net").rstrip("/")
    url = f"{base}/api/v1/recall"
    headers = {
        "X-API-Key": os.environ.get("MEMCLAW_API_KEY", ""),
        "Content-Type": "application/json",
    }
    body = {
        "fleet_id": os.environ.get("MEMCLAW_FLEET_ID", "fleet"),
        "tenant_id": os.environ.get("MEMCLAW_TENANT_ID", ""),
        "agent_id": "debug-agent",
        "query": "architecture CSS layout performance bundle constraint images schema SEO",
        "top_k": 10,
    }

    print("POST", url)
    print("Headers:", {k: ("****" if k == "X-API-Key" and v else v) for k, v in headers.items()})
    print("Body:", json.dumps(body, indent=2))

    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        print("Status:", r.status_code)
        print("Content-Type:", r.headers.get("Content-Type"))
        # Try JSON first
        try:
            print(json.dumps(r.json(), indent=2))
        except Exception:
            print(r.text)
    except Exception as exc:
        print("Request failed:", exc)


if __name__ == "__main__":
    main()
