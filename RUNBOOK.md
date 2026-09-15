# Runbook — your Shopify integration

_This document ships with every engagement. Keep it where the person who
notices a problem will find it._

## What this service does

Every time an order is created in your Shopify store, Shopify sends the
order to a small service that we run for you. The service saves the order
immediately (so nothing is ever lost), then a background process turns it
into a record in **<DESTINATION — e.g. your Notion "Orders" database>**
using the field mapping we agreed on.

It does one thing, in one direction: Shopify → your destination. It never
changes anything in Shopify.

Typical delay from "order placed" to "record appears": a few seconds
(or up to 5 minutes if we set you up on the low-cost schedule — we will have
told you which).

## How to tell it is healthy

Open this page in a browser (no login needed):

    https://<service>.onrender.com/health

You are looking for three things:

| Field | Healthy | Means |
|---|---|---|
| `"status"` | `"ok"` | Everything is reachable |
| `"dead_events"` | `0` | No order has permanently failed to deliver |
| `"last_delivered_at"` | recent | The last order that went through successfully |

`"status": "degraded"` means the service is up and saving orders, but cannot
currently reach the destination (Notion, Monday, etc.). Orders are safe and
will be delivered automatically once the destination is reachable again.
If it stays degraded for more than an hour, tell us.

If the page does not load at all, tell us right away.

## What the alerts mean

When an order cannot be delivered after repeated attempts — or fails in a
way that retrying will not fix — a message is posted to the Slack channel
we set up (**<#channel>**). It looks like:

> 🚨 **Event dead-lettered — needs attention**
> Topic · Webhook id · Attempts · Last error · replay instructions

"Dead-lettered" means: *this order is saved but not delivered; a person needs
to look.* It is **not** lost.

### The three usual causes

| The "Last error" says something like | What happened | Who fixes it |
|---|---|---|
| `no property named "X"` / `no column titled "X"` / `no header column "X"` | A field in the destination was renamed or deleted | Rename it back, or ask us to update the mapping |
| `401` / `403` / `invalid_auth` / `unauthorized` | The destination credential was rotated or revoked | Tell us; we update the deployment |
| `required field "X" resolved to nothing` | An order arrived without a value the mapping requires | Ask us — usually a mapping tweak |

Anything else: send us the alert text.

## What to do when an alert fires

1. **Don't panic and don't re-enter the order by hand yet.** The order is
   stored and can be re-delivered with one command once the cause is fixed.
2. Check the table above. If it is something on your side (a renamed
   column, a revoked token), fix it or let us know.
3. Tell us — reply in the alert thread or email (below). Include the alert
   text.
4. We fix the cause and **replay** the event; it is delivered as if it had
   just arrived. You will see `dead_events` go back to `0` on the health page.

## Things that will break the integration

- Renaming or deleting a column/property/header that the mapping writes to.
  Adding new ones is fine.
- Revoking or regenerating the API token / integration secret we use, or
  removing the integration's access to the database/board/sheet.
- Deleting or disabling the webhook in Shopify (Settings → Notifications →
  Webhooks).
- Changing the Shopify webhook signing secret (this happens if the store's
  webhooks are recreated) — orders will be rejected until we update it.

If you need to do any of these, tell us first and we will make the change
together — it takes minutes when planned and hours when discovered.

## Contact

**<Your name>** — <email> — <phone / Slack>
Maintenance retainer: we monitor `/health` and the alert channel, keep the
service updated, and handle replays and mapping changes.

Response targets: acknowledgement within <N> business hours; dead-lettered
orders redelivered the same business day once the cause is resolved.
