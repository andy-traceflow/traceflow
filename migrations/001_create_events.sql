-- 001_create_events.sql
--
-- The durable event store. Every verified webhook becomes one row here
-- BEFORE the handler returns 200; the delivery worker reads from this
-- table, never from memory. This is what survives a Render restart.
--
-- Single tenant: no client_id, no RLS. One deployment, one client.
--
-- Status lifecycle:
--   received   -> claimed by the worker         -> processing
--   processing -> destination accepted           -> delivered
--   processing -> transient failure, attempts<5  -> received  (next_retry_at set)
--   processing -> permanent failure or 5th fail  -> dead      (alert fires)
--   dead       -> POST /events/{id}/replay       -> received
--
-- next_retry_at has two meanings, both read by the same index:
--   status='received'   : do not claim before this time (backoff)
--   status='processing' : the claim lease; if it passes, the worker that
--                         claimed the row died and another may reclaim it

BEGIN;

CREATE TABLE events (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source        text        NOT NULL,                 -- 'shopify'
    topic         text        NOT NULL,                 -- 'orders/create'
    webhook_id    text        NOT NULL,                 -- X-Shopify-Webhook-Id
    shop_domain   text,                                 -- X-Shopify-Shop-Domain
    payload       jsonb       NOT NULL,
    status        text        NOT NULL DEFAULT 'received'
                              CHECK (status IN ('received', 'processing', 'delivered', 'dead')),
    attempts      int         NOT NULL DEFAULT 0,
    last_error    text,
    next_retry_at timestamptz,
    external_id   text,                                 -- destination record id after delivery
    received_at   timestamptz NOT NULL DEFAULT now(),
    delivered_at  timestamptz,

    -- The dedupe mechanism. A Shopify redelivery of the same webhook id is
    -- absorbed by INSERT ... ON CONFLICT DO NOTHING; the original row keeps
    -- its own retry state.
    UNIQUE (source, webhook_id)
);

-- The worker's claim query: status + due time.
CREATE INDEX events_status_next_retry_at_idx ON events (status, next_retry_at);

COMMIT;
