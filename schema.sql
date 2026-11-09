-- omega-call-bot calendar + passcode schema (idempotent)
CREATE TABLE IF NOT EXISTS callers (
  id               bigserial PRIMARY KEY,
  phone            text NOT NULL UNIQUE,            -- E.164, e.g. +15595550123
  passcode_hash    text,                            -- scrypt (+ server pepper); never plaintext
  failed_attempts  integer NOT NULL DEFAULT 0,
  locked_until     timestamptz,
  last_verified_at timestamptz,
  is_test          boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS appointments (
  id                    bigserial PRIMARY KEY,
  caller_phone          text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  title                 text NOT NULL,
  starts_at             timestamptz NOT NULL,
  timezone              text NOT NULL DEFAULT 'America/Los_Angeles',
  remind_at             timestamptz,
  reminded              boolean NOT NULL DEFAULT false,
  reminder_status       text,          -- claimed | dry_run | called | failed:<why> | skipped:<why>
  reminder_attempts     integer NOT NULL DEFAULT 0,
  reminder_attempted_at timestamptz,
  reminder_call_id      text,
  notes                 text,
  source_text           text,          -- what the caller said
  cancelled_at          timestamptz,
  is_test               boolean NOT NULL DEFAULT false,
  created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS appointments_due_idx
  ON appointments (remind_at) WHERE NOT reminded AND cancelled_at IS NULL;
CREATE INDEX IF NOT EXISTS appointments_caller_idx ON appointments (caller_phone, starts_at);

-- per-turn chat log, linked to caller phone (call_summaries keeps per-call summaries)
CREATE TABLE IF NOT EXISTS chat_logs (
  id           bigserial PRIMARY KEY,
  caller_phone text NOT NULL,
  call_id      text,
  role         text NOT NULL CHECK (role IN ('user','assistant','system')),
  content      text NOT NULL,           -- passcode turns are stored as "[passcode entered]"
  is_test      boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_logs_caller_idx ON chat_logs (caller_phone, created_at DESC);

-- live voice-call state (one row per AgentPhone callId)
CREATE TABLE IF NOT EXISTS call_sessions (
  call_id         text PRIMARY KEY,
  caller_phone    text NOT NULL,
  stage           text NOT NULL,        -- set_code | confirm_code | verify | ready | locked
  verified        boolean NOT NULL DEFAULT false,
  pending_hash    text,                 -- hash of first entry while confirming a new code
  pending_request text,                 -- what they asked before verifying
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE callers ADD COLUMN IF NOT EXISTS name text;   -- used for the personal greeting

-- to-do list
CREATE TABLE IF NOT EXISTS todos (
  id           bigserial PRIMARY KEY,
  caller_phone text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  title        text NOT NULL,
  due_at       timestamptz,
  done         boolean NOT NULL DEFAULT false,
  done_at      timestamptz,
  is_test      boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS todos_caller_idx ON todos (caller_phone) WHERE NOT done;

-- unfinished projects
CREATE TABLE IF NOT EXISTS projects (
  id           bigserial PRIMARY KEY,
  caller_phone text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  name         text NOT NULL,
  status       text NOT NULL DEFAULT 'in_progress'
               CHECK (status IN ('idea','in_progress','paused','done','dropped')),
  next_step    text,
  is_test      boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS projects_caller_idx ON projects (caller_phone, status);

-- STUB: per-caller emails/texts for the briefing. Nothing feeds this yet; a future
-- AgentMail / AgentPhone-SMS / Gmail sync would INSERT here.
CREATE TABLE IF NOT EXISTS inbox_items (
  id           bigserial PRIMARY KEY,
  caller_phone text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('email','text')),
  source       text NOT NULL DEFAULT 'stub',   -- agentmail | agentphone_sms | gmail | stub
  sender       text,
  subject      text,
  snippet      text,
  received_at  timestamptz NOT NULL DEFAULT now(),
  is_read      boolean NOT NULL DEFAULT false,
  is_test      boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS inbox_items_caller_idx ON inbox_items (caller_phone, is_read, received_at DESC);

-- -----------------------------------------------------------------
-- Pixie groundwork (units, minutes ledger, payments, contacts)
-- Also in migrations/002_pixie_groundwork.sql for incremental apply.
-- Rate: $10/hour => $1 = 6 minutes.
-- -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS units (
  code            text PRIMARY KEY,
  display_name    text NOT NULL,
  notes           text,
  incoming_e164   text[] NOT NULL DEFAULT '{}',
  outgoing_e164   text[] NOT NULL DEFAULT '{}',
  incoming_raw    text,
  outgoing_raw    text,
  directory_source text NOT NULL DEFAULT 'DSH Coalinga patient phone list',
  directory_date  date,
  directory_saved_at timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE callers ADD COLUMN IF NOT EXISTS unit text REFERENCES units(code);
ALTER TABLE callers ADD COLUMN IF NOT EXISTS minutes_balance integer NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS callers_unit_idx ON callers (unit);

CREATE TABLE IF NOT EXISTS minute_transactions (
  id              bigserial PRIMARY KEY,
  caller_phone    text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  delta_minutes   integer NOT NULL,
  reason          text NOT NULL
                  CHECK (reason IN ('payment','call_usage','adjustment','bonus','refund')),
  reference       text,
  created_by      text NOT NULL DEFAULT 'system',
  balance_after   integer,
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

ALTER TABLE call_sessions ADD COLUMN IF NOT EXISTS verified_at timestamptz;
ALTER TABLE call_sessions ADD COLUMN IF NOT EXISTS billable_started_at timestamptz;
