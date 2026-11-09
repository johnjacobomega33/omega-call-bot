-- Free test run (Jacob approved Nov 9, 2026). Additive only.
-- passcode_required: per-caller switch. Default true keeps every existing/new caller on the passcode gate.
ALTER TABLE callers ADD COLUMN IF NOT EXISTS passcode_required boolean NOT NULL DEFAULT true;
ALTER TABLE callers ADD COLUMN IF NOT EXISTS full_name text;
ALTER TABLE callers ADD COLUMN IF NOT EXISTS email text;

-- Extra caller-ID numbers that map to one caller (e.g., the several Unit 11 outgoing dayroom lines).
CREATE TABLE IF NOT EXISTS caller_phone_aliases (
  alias_phone   text PRIMARY KEY,                     -- E.164 caller ID as AgentPhone reports it
  caller_phone  text NOT NULL REFERENCES callers(phone) ON DELETE CASCADE,
  source        text,                                 -- where the number came from
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS caller_phone_aliases_caller_idx ON caller_phone_aliases (caller_phone);

-- Voice email (test): every attempt is logged, sent or not.
CREATE TABLE IF NOT EXISTS email_log (
  id                  bigserial PRIMARY KEY,
  caller_phone        text NOT NULL,
  call_id             text,
  from_address        text,
  to_address          text NOT NULL,
  subject             text,
  body                text,
  status              text NOT NULL CHECK (status IN ('sent','failed','blocked')),
  provider_message_id text,
  error               text,
  is_test             boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_log_caller_idx ON email_log (caller_phone, created_at DESC);
