"""POST a signed test order to a running TraceFlow service, exactly as Shopify would.

Verifies HMAC + persistence end-to-end without touching Shopify. Each run
uses a fresh X-Shopify-Webhook-Id so it creates a new event; pass
--webhook-id to prove dedupe instead.

    python scripts/send_test_webhook.py http://localhost:8000
    python scripts/send_test_webhook.py https://<service>.onrender.com --topic orders/create

Reads SHOPIFY_WEBHOOK_SECRET from the environment (or .env).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from app.config import get_settings  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "shopify_order.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", help="e.g. http://localhost:8000")
    parser.add_argument("--topic", default="orders/create")
    parser.add_argument("--payload", type=Path, default=FIXTURE, help="JSON file to send")
    parser.add_argument("--webhook-id", default=None, help="reuse an id to test dedupe")
    parser.add_argument("--shop", default="example.myshopify.com")
    args = parser.parse_args()

    secret = get_settings().shopify_webhook_secret
    if not secret:
        print("SHOPIFY_WEBHOOK_SECRET is not set", file=sys.stderr)
        return 2

    body = args.payload.read_bytes()
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    webhook_id = args.webhook_id or str(uuid.uuid4())
    url = f"{args.base_url.rstrip('/')}/webhooks/shopify/{args.topic}"

    resp = httpx.post(
        url,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Webhook-Id": webhook_id,
            "X-Shopify-Topic": args.topic,
            "X-Shopify-Shop-Domain": args.shop,
        },
        timeout=15.0,
    )
    print(f"POST {url}\n  webhook_id: {webhook_id}\n  → {resp.status_code} {resp.text[:200]}")
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
