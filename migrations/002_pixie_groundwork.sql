-- Pixie groundwork: units directory, minutes ledger, payments, contacts
-- Idempotent. Apply to Neon branch pixie-groundwork only (never production until Jacob approves).

CREATE TABLE IF NOT EXISTS units (
  code            text PRIMARY KEY,                 -- 'Unit 11', 'MA1', 'MA2'
  display_name    text NOT NULL,
  notes           text,
  incoming_e164   text[] NOT NULL DEFAULT '{}',      -- E.164 list (+1559...)
  outgoing_e164   text[] NOT NULL DEFAULT '{}',
  incoming_raw    text,                             -- as printed on the 2016 list
  outgoing_raw    text,
  directory_source text NOT NULL DEFAULT 'DSH Coalinga patient phone list',
  directory_date  date,                             -- printed revision date on PDF
  directory_saved_at timestamptz,                   -- when we captured the list
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE callers ADD COLUMN IF NOT EXISTS unit text REFERENCES units(code);
ALTER TABLE callers ADD COLUMN IF NOT EXISTS minutes_balance integer NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS callers_unit_idx ON callers (unit);

-- Ledger of every minutes change. Balance = SUM(delta_minutes); callers.minutes_balance is a cache.
CREATE TABLE IF NOT EXISTS minute_transactions (
  id              bigserial PRIMARY KEY,
  caller_phone    text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  delta_minutes   integer NOT NULL,                 -- + credit, - usage
  reason          text NOT NULL
                  CHECK (reason IN ('payment','call_usage','adjustment','bonus','refund')),
  reference       text,                             -- payment id, call_id, note
  created_by      text NOT NULL DEFAULT 'system',    -- 'jacob' | 'system' | bot key label
  balance_after   integer,                          -- cached balance after this row
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS minute_tx_caller_idx ON minute_transactions (caller_phone, created_at DESC);

CREATE TABLE IF NOT EXISTS payments (
  id                bigserial PRIMARY KEY,
  caller_phone      text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  method            text NOT NULL
                    CHECK (method IN ('check','money_order','card','other')),
  amount_cents      integer NOT NULL CHECK (amount_cents > 0),
  check_or_mo_number text,
  payer_name        text,
  received_date     date NOT NULL DEFAULT (CURRENT_DATE),
  status            text NOT NULL DEFAULT 'received'
                    CHECK (status IN ('received','deposited','bounced','refunded','void')),
  minutes_credited  integer NOT NULL DEFAULT 0,
  minute_tx_id      bigint REFERENCES minute_transactions(id),
  notes             text,
  logged_by         text NOT NULL DEFAULT 'jacob',
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payments_caller_idx ON payments (caller_phone, created_at DESC);
CREATE INDEX IF NOT EXISTS payments_status_idx ON payments (status);

-- Optional personal contacts per caller (cheap add)
CREATE TABLE IF NOT EXISTS contacts (
  id              bigserial PRIMARY KEY,
  caller_phone    text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  name            text NOT NULL,
  phone           text,
  relationship    text,
  notes           text,
  is_test         boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contacts_caller_idx ON contacts (caller_phone, name);

-- Rate constant documented in app: $10/hour => $1 = 6 minutes => amount_cents * 6 / 100
COMMENT ON TABLE minute_transactions IS 'Pixie prepaid minutes ledger. Rate: $10/hour ($1 = 6 minutes).';
COMMENT ON TABLE payments IS 'Logged mailed checks / money orders / future card top-ups.';
COMMENT ON TABLE units IS 'Coalinga State Hospital unit dayroom phones from 2016 DSH list (may be outdated).';

ALTER TABLE call_sessions ADD COLUMN IF NOT EXISTS verified_at timestamptz;
ALTER TABLE call_sessions ADD COLUMN IF NOT EXISTS billable_started_at timestamptz;
