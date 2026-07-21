-- Quantum Strategy Fund investor portal: initial protected ledger schema.
-- Run in a new Supabase project before enabling assets/js/portal-config.js.
-- Financial rows are private by default; all investor reads are constrained by
-- portfolio membership and all ledger writes pass through one checked RPC.

create extension if not exists pgcrypto;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null check (char_length(display_name) between 1 and 120),
  role text not null default 'investor'
    check (role in ('investor', 'operations', 'administrator')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.portfolios (
  id uuid primary key default gen_random_uuid(),
  code text not null unique check (code ~ '^[A-Z0-9_-]{2,32}$'),
  name text not null check (char_length(name) between 1 and 160),
  base_currency text not null default 'USD' check (base_currency ~ '^[A-Z]{3}$'),
  inception_date date not null,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table public.portfolio_members (
  portfolio_id uuid not null references public.portfolios(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'investor' check (role in ('investor', 'manager')),
  created_at timestamptz not null default now(),
  primary key (portfolio_id, user_id)
);

create table public.instruments (
  id uuid primary key default gen_random_uuid(),
  symbol text not null unique check (char_length(symbol) between 1 and 40),
  provider_symbol text,
  name text not null check (char_length(name) between 1 and 180),
  instrument_type text not null
    check (instrument_type in ('cash', 'equity', 'etf', 'bond', 'option', 'crypto', 'fund', 'other')),
  asset_class text not null default 'other',
  currency text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  multiplier numeric(24,8) not null default 1 check (multiplier > 0),
  underlying_id uuid references public.instruments(id),
  expiration_date date,
  strike numeric(24,8),
  option_right text check (option_right is null or option_right in ('call', 'put')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  check (
    instrument_type <> 'option'
    or (underlying_id is not null and expiration_date is not null and strike is not null and option_right is not null)
  )
);

create table public.transactions (
  id uuid primary key default gen_random_uuid(),
  portfolio_id uuid not null references public.portfolios(id),
  trade_date timestamptz not null,
  transaction_type text not null
    check (transaction_type in (
      'trade', 'deposit', 'withdrawal', 'dividend', 'interest', 'fee',
      'option_expiry', 'assignment', 'cash_adjustment', 'reversal'
    )),
  memo text check (memo is null or char_length(memo) <= 500),
  reversal_of uuid references public.transactions(id),
  idempotency_key text not null check (char_length(idempotency_key) between 1 and 120),
  request_fingerprint text not null check (char_length(request_fingerprint) = 32),
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  unique (portfolio_id, idempotency_key),
  check ((transaction_type = 'reversal') = (reversal_of is not null))
);

create table public.transaction_legs (
  id bigint generated always as identity primary key,
  transaction_id uuid not null references public.transactions(id),
  instrument_id uuid references public.instruments(id),
  leg_type text not null check (leg_type in ('position', 'cash', 'fee', 'income', 'tax', 'other')),
  quantity numeric(30,10),
  unit_price numeric(30,10),
  amount numeric(30,10) not null,
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  created_at timestamptz not null default now(),
  check (
    (leg_type = 'position' and instrument_id is not null and quantity is not null and unit_price is not null)
    or (leg_type <> 'position' and quantity is null and unit_price is null)
  )
);

create table public.quotes (
  id bigint generated always as identity primary key,
  instrument_id uuid not null references public.instruments(id) on delete cascade,
  price numeric(30,10) not null check (price >= 0),
  bid numeric(30,10) check (bid is null or bid >= 0),
  ask numeric(30,10) check (ask is null or ask >= 0),
  source text not null check (char_length(source) between 1 and 80),
  quality text not null check (quality in ('delayed', 'prior_close', 'manual', 'estimated', 'stale')),
  as_of timestamptz not null,
  fetched_at timestamptz not null default now(),
  unique (instrument_id, source, as_of)
);

create table public.daily_nav (
  portfolio_id uuid not null references public.portfolios(id) on delete cascade,
  nav_date date not null,
  aum numeric(30,10) not null,
  cash_balance numeric(30,10) not null,
  gross_assets numeric(30,10) not null default 0,
  liabilities numeric(30,10) not null default 0,
  nav_per_unit numeric(30,12),
  return_since_inception numeric(20,8),
  net_external_flow numeric(30,10) not null default 0,
  quote_as_of timestamptz,
  calculated_at timestamptz not null default now(),
  primary key (portfolio_id, nav_date)
);

create table public.report_jobs (
  id uuid primary key default gen_random_uuid(),
  portfolio_id uuid not null references public.portfolios(id) on delete cascade,
  requested_by uuid not null references auth.users(id),
  requested_at timestamptz not null default now(),
  valuation_as_of timestamptz,
  status text not null default 'queued'
    check (status in ('queued', 'rendering', 'ready', 'failed', 'expired')),
  template_version text not null default 'v1',
  storage_path text,
  document_sha256 text,
  completed_at timestamptz,
  error_code text
);

create table public.audit_events (
  id bigint generated always as identity primary key,
  actor_id uuid references auth.users(id),
  portfolio_id uuid references public.portfolios(id),
  event_type text not null,
  entity_type text not null,
  entity_id text,
  metadata jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now()
);

create index portfolio_members_user_idx on public.portfolio_members(user_id);
create index transactions_portfolio_date_idx on public.transactions(portfolio_id, trade_date, id);
create unique index transactions_one_reversal_idx on public.transactions(reversal_of) where reversal_of is not null;
create index transaction_legs_transaction_idx on public.transaction_legs(transaction_id);
create index transaction_legs_instrument_idx on public.transaction_legs(instrument_id);
create index quotes_instrument_asof_idx on public.quotes(instrument_id, as_of desc);
create index daily_nav_portfolio_date_idx on public.daily_nav(portfolio_id, nav_date desc);
create index report_jobs_requester_idx on public.report_jobs(requested_by, requested_at desc);
create unique index report_jobs_one_active_idx on public.report_jobs(portfolio_id, requested_by)
where status in ('queued', 'rendering');

create or replace function public.is_portfolio_member(p_portfolio_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.portfolio_members pm
    where pm.portfolio_id = p_portfolio_id
      and pm.user_id = auth.uid()
  );
$$;

create or replace function public.is_portal_operator()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '')
    in ('operations', 'administrator');
$$;

create or replace function public.reject_ledger_mutation()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  raise exception 'posted ledger rows are immutable';
end;
$$;

create trigger transactions_immutable
before update or delete on public.transactions
for each row execute function public.reject_ledger_mutation();

create trigger transaction_legs_immutable
before update or delete on public.transaction_legs
for each row execute function public.reject_ledger_mutation();

create or replace function public.post_ledger_transaction(
  p_portfolio_id uuid,
  p_trade_date timestamptz,
  p_transaction_type text,
  p_memo text,
  p_reversal_of uuid,
  p_idempotency_key text,
  p_legs jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_transaction_id uuid;
  v_base_currency text;
  v_inception_date date;
  v_balance numeric(30,10);
  v_leg_count integer;
  v_existing_fingerprint text;
  v_request_fingerprint text;
  v_expected_reversal jsonb;
  v_supplied_reversal jsonb;
begin
  if not public.is_portal_operator() then
    raise exception 'operations authorization required' using errcode = '42501';
  end if;

  if p_transaction_type not in (
    'trade', 'deposit', 'withdrawal', 'dividend', 'interest', 'fee',
    'option_expiry', 'assignment', 'cash_adjustment', 'reversal'
  ) then
    raise exception 'invalid transaction type';
  end if;

  if p_idempotency_key is null or char_length(p_idempotency_key) not between 1 and 120 then
    raise exception 'invalid idempotency key';
  end if;

  select base_currency, inception_date into v_base_currency, v_inception_date
  from public.portfolios
  where id = p_portfolio_id and active = true;

  if v_base_currency is null then
    raise exception 'portfolio not found';
  end if;

  if p_trade_date is null
    or p_trade_date < v_inception_date::timestamptz
    or p_trade_date > now() + interval '5 minutes'
  then
    raise exception 'invalid trade date';
  end if;

  if jsonb_typeof(p_legs) <> 'array' or jsonb_array_length(p_legs) not between 2 and 32 then
    raise exception 'legs must be a JSON array containing between 2 and 32 entries';
  end if;

  select count(*), coalesce(sum(x.amount), 0)
    into v_leg_count, v_balance
  from jsonb_to_recordset(p_legs) as x(
    instrument_id uuid,
    leg_type text,
    quantity numeric,
    unit_price numeric,
    amount numeric,
    currency text
  );

  if v_leg_count < 2 or abs(v_balance) > 0.00000001 then
    raise exception 'ledger entry must contain at least two balanced legs';
  end if;

  if exists (
    select 1
    from jsonb_to_recordset(p_legs) as x(
      instrument_id uuid,
      leg_type text,
      quantity numeric,
      unit_price numeric,
      amount numeric,
      currency text
    )
    left join public.instruments i on i.id = x.instrument_id
    where x.amount is null
      or x.currency is distinct from v_base_currency
      or x.leg_type is null
      or x.leg_type not in ('position', 'cash', 'fee', 'income', 'tax', 'other')
      or (
        x.leg_type = 'position'
        and (
          i.id is null
          or not i.active
          or x.quantity is null
          or x.quantity = 0
          or x.unit_price is null
          or x.unit_price < 0
          or abs(x.amount - (x.quantity * x.unit_price * i.multiplier)) > 0.00000001
        )
      )
      or (
        x.leg_type <> 'position'
        and (x.instrument_id is not null or x.quantity is not null or x.unit_price is not null)
      )
  ) then
    raise exception 'one or more ledger legs are invalid or inconsistent';
  end if;

  if p_transaction_type = 'reversal' then
    if not exists (
      select 1 from public.transactions
      where id = p_reversal_of and portfolio_id = p_portfolio_id
    ) then
      raise exception 'reversal target must belong to the same portfolio';
    end if;

    select jsonb_agg(
      jsonb_build_object(
        'instrument_id', tl.instrument_id,
        'leg_type', tl.leg_type,
        'quantity', -tl.quantity,
        'unit_price', tl.unit_price,
        'amount', -tl.amount,
        'currency', tl.currency
      ) order by
        coalesce(tl.instrument_id::text, ''), tl.leg_type,
        coalesce(-tl.quantity, 0), coalesce(tl.unit_price, 0), -tl.amount
    ) into v_expected_reversal
    from public.transaction_legs tl
    where tl.transaction_id = p_reversal_of;

    select jsonb_agg(
      jsonb_build_object(
        'instrument_id', x.instrument_id,
        'leg_type', x.leg_type,
        'quantity', x.quantity,
        'unit_price', x.unit_price,
        'amount', x.amount,
        'currency', x.currency
      ) order by
        coalesce(x.instrument_id::text, ''), x.leg_type,
        coalesce(x.quantity, 0), coalesce(x.unit_price, 0), x.amount
    ) into v_supplied_reversal
    from jsonb_to_recordset(p_legs) as x(
      instrument_id uuid,
      leg_type text,
      quantity numeric,
      unit_price numeric,
      amount numeric,
      currency text
    );

    if coalesce(v_expected_reversal, '[]'::jsonb)
      is distinct from coalesce(v_supplied_reversal, '[]'::jsonb)
    then
      raise exception 'reversal legs must exactly negate the original transaction';
    end if;
  elsif p_reversal_of is not null then
    raise exception 'only reversal transactions may reference a prior transaction';
  end if;

  v_request_fingerprint := md5(jsonb_build_object(
    'portfolio_id', p_portfolio_id,
    'trade_date', p_trade_date,
    'transaction_type', p_transaction_type,
    'memo', nullif(p_memo, ''),
    'reversal_of', p_reversal_of,
    'legs', p_legs
  )::text);

  select id, request_fingerprint into v_transaction_id, v_existing_fingerprint
  from public.transactions
  where portfolio_id = p_portfolio_id
    and idempotency_key = p_idempotency_key;

  if v_transaction_id is not null then
    if v_existing_fingerprint is distinct from v_request_fingerprint then
      raise exception 'idempotency key was already used for a different request';
    end if;
    return v_transaction_id;
  end if;

  begin
    insert into public.transactions (
      portfolio_id, trade_date, transaction_type, memo, reversal_of,
      idempotency_key, request_fingerprint, created_by
    ) values (
      p_portfolio_id, p_trade_date, p_transaction_type, nullif(p_memo, ''),
      p_reversal_of, p_idempotency_key, v_request_fingerprint, auth.uid()
    ) returning id into v_transaction_id;
  exception when unique_violation then
    select id, request_fingerprint into v_transaction_id, v_existing_fingerprint
    from public.transactions
    where portfolio_id = p_portfolio_id
      and idempotency_key = p_idempotency_key;
    if v_transaction_id is null then
      raise;
    end if;
    if v_existing_fingerprint is distinct from v_request_fingerprint then
      raise exception 'idempotency key was already used for a different request';
    end if;
    return v_transaction_id;
  end;

  insert into public.transaction_legs (
    transaction_id, instrument_id, leg_type, quantity, unit_price, amount, currency
  )
  select
    v_transaction_id, x.instrument_id, x.leg_type, x.quantity,
    x.unit_price, x.amount, x.currency
  from jsonb_to_recordset(p_legs) as x(
    instrument_id uuid,
    leg_type text,
    quantity numeric,
    unit_price numeric,
    amount numeric,
    currency text
  );

  insert into public.audit_events (
    actor_id, portfolio_id, event_type, entity_type, entity_id, metadata
  ) values (
    auth.uid(), p_portfolio_id, 'ledger.posted', 'transaction',
    v_transaction_id::text, jsonb_build_object('idempotency_key', p_idempotency_key)
  );

  return v_transaction_id;
end;
$$;

create or replace function public.request_investor_report(p_portfolio_id uuid)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_job_id uuid;
  v_last_request timestamptz;
begin
  if auth.uid() is null or not public.is_portfolio_member(p_portfolio_id) then
    raise exception 'portfolio authorization required' using errcode = '42501';
  end if;

  select id into v_job_id
  from public.report_jobs
  where portfolio_id = p_portfolio_id
    and requested_by = auth.uid()
    and status in ('queued', 'rendering')
  order by requested_at desc
  limit 1;

  if v_job_id is not null then
    return v_job_id;
  end if;

  select max(requested_at) into v_last_request
  from public.report_jobs
  where portfolio_id = p_portfolio_id
    and requested_by = auth.uid();

  if v_last_request is not null and v_last_request > now() - interval '5 minutes' then
    raise exception 'report requests are limited to one every five minutes';
  end if;

  insert into public.report_jobs (portfolio_id, requested_by)
  values (p_portfolio_id, auth.uid())
  returning id into v_job_id;

  insert into public.audit_events (
    actor_id, portfolio_id, event_type, entity_type, entity_id
  ) values (
    auth.uid(), p_portfolio_id, 'report.requested', 'report_job', v_job_id::text
  );

  return v_job_id;
end;
$$;

alter table public.profiles enable row level security;
alter table public.portfolios enable row level security;
alter table public.portfolio_members enable row level security;
alter table public.instruments enable row level security;
alter table public.transactions enable row level security;
alter table public.transaction_legs enable row level security;
alter table public.quotes enable row level security;
alter table public.daily_nav enable row level security;
alter table public.report_jobs enable row level security;
alter table public.audit_events enable row level security;

create policy profiles_read_own_or_operator on public.profiles
for select to authenticated
using (id = auth.uid() or public.is_portal_operator());

create policy portfolios_read_member_or_operator on public.portfolios
for select to authenticated
using (public.is_portfolio_member(id) or public.is_portal_operator());

create policy memberships_read_own_or_operator on public.portfolio_members
for select to authenticated
using (user_id = auth.uid() or public.is_portal_operator());

create policy instruments_read_authenticated on public.instruments
for select to authenticated using (
  public.is_portal_operator()
  or exists (
    select 1
    from public.transaction_legs tl
    join public.transactions t on t.id = tl.transaction_id
    where tl.instrument_id = instruments.id
      and public.is_portfolio_member(t.portfolio_id)
  )
);

create policy transactions_read_member_or_operator on public.transactions
for select to authenticated
using (public.is_portfolio_member(portfolio_id) or public.is_portal_operator());

create policy transaction_legs_read_member_or_operator on public.transaction_legs
for select to authenticated
using (
  exists (
    select 1 from public.transactions t
    where t.id = transaction_id
      and (public.is_portfolio_member(t.portfolio_id) or public.is_portal_operator())
  )
);

create policy quotes_read_authorized on public.quotes
for select to authenticated
using (
  public.is_portal_operator()
  or exists (
    select 1
    from public.transaction_legs tl
    join public.transactions t on t.id = tl.transaction_id
    where tl.instrument_id = quotes.instrument_id
      and public.is_portfolio_member(t.portfolio_id)
  )
);

create policy daily_nav_read_member_or_operator on public.daily_nav
for select to authenticated
using (public.is_portfolio_member(portfolio_id) or public.is_portal_operator());

create policy report_jobs_read_requester_or_operator on public.report_jobs
for select to authenticated
using (
  (requested_by = auth.uid() and public.is_portfolio_member(portfolio_id))
  or public.is_portal_operator()
);

create policy audit_read_operator on public.audit_events
for select to authenticated using (public.is_portal_operator());

revoke all on public.profiles, public.portfolios, public.portfolio_members,
  public.instruments, public.transactions, public.transaction_legs,
  public.quotes, public.daily_nav, public.report_jobs, public.audit_events
  from anon, authenticated;

grant select on public.profiles, public.portfolios, public.portfolio_members,
  public.instruments, public.transactions, public.transaction_legs,
  public.quotes, public.daily_nav, public.report_jobs
  to authenticated;
revoke all on function public.post_ledger_transaction(
  uuid, timestamptz, text, text, uuid, text, jsonb
) from public, anon, authenticated;
revoke all on function public.request_investor_report(uuid) from public, anon, authenticated;
revoke all on function public.is_portfolio_member(uuid) from public, anon, authenticated;
revoke all on function public.is_portal_operator() from public, anon, authenticated;
revoke all on function public.reject_ledger_mutation() from public, anon, authenticated;
grant execute on function public.post_ledger_transaction(
  uuid, timestamptz, text, text, uuid, text, jsonb
) to authenticated;
grant execute on function public.request_investor_report(uuid) to authenticated;
grant execute on function public.is_portfolio_member(uuid) to authenticated;
grant execute on function public.is_portal_operator() to authenticated;

-- Prevent direct API access to the audit log and posted-ledger mutations.
revoke insert, update, delete on public.transactions, public.transaction_legs from authenticated;
revoke insert, update, delete on public.audit_events from authenticated;

comment on table public.transactions is
  'Immutable transaction headers. Corrections are new reversal transactions.';
comment on table public.transaction_legs is
  'Balanced signed ledger legs. Amounts sum to zero within each transaction.';
comment on column public.daily_nav.return_since_inception is
  'Percentage points, e.g. 5.25 means +5.25%, after external-flow adjustment.';
