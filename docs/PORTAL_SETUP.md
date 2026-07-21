# QSF investor portal activation

The brochure site can remain on GitHub Pages. The investor portal cannot be
securely activated with static files alone: GitHub Pages serves public HTML,
CSS, and JavaScript and has no private server-side password or portfolio store.
The checked-in portal therefore stays disabled until a protected backend is
configured.

The recommended first backend is Supabase Auth + Postgres Row Level Security
(RLS) + private Storage. The public `anon`/publishable key may be used by the
browser only after every table has been tested under RLS. A service-role key,
database password, quote-provider key, investor data, or generated report must
never be committed to this repository.

Official references:

- [GitHub Pages is static hosting](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [Supabase Auth](https://supabase.com/docs/guides/auth)
- [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Private Storage downloads](https://supabase.com/docs/guides/storage/serving/downloads)

## 1. Create and secure the backend

1. Create a Supabase project in the desired production region.
2. Disable open/public registration. Accounts should be administrator-invited.
3. Apply `supabase/migrations/001_initial.sql` in a staging project first.
4. Set each authorized operator's immutable `app_metadata.role` to
   `operations` or `administrator`. Do not put authorization roles in editable
   user metadata.
5. Create a private Storage bucket for reports. Do not make statement URLs
   public; return only short-lived signed URLs to the requesting member.
6. Implement and test a complete password-recovery callback and password-update
   flow before enabling self-service recovery. The checked-in client currently
   directs access problems to the fund administrator rather than exposing a
   partial reset flow.
7. Enable rate limits, recovery email delivery, MFA where practical, database
   backups, monitoring, and an audit-retention policy.

The passwords supplied in the original website request are weak and disclosed.
Do not deploy them. Invite each investor with a one-time strong credential or
reset link, force a password change, and obtain a real recovery email.

## 2. Verify isolation before adding client data

Create two synthetic users and two synthetic portfolios. Confirm all of these
tests with the publishable key—not the service-role key:

- Investor A can select only rows joined to Investor A's membership.
- Investor A cannot read Investor B's profile, ledger, NAV, reports, or quotes
  for instruments held only by Investor B.
- Neither investor can insert, update, or delete ledger rows.
- A direct URL to `dashboard.html` loads no data without a valid session.
- A direct URL to `admin/` cannot post a trade for an investor role.
- Repeating one idempotency key does not create a second transaction.
- Posted transactions and transaction legs cannot be updated or deleted.
- Report downloads expire and cannot be used by another user.

Only after these tests pass should real users and portfolios be imported.

## 3. Activate the checked-in client

Edit `assets/js/portal-config.js` only after the staging checks pass:

```js
window.QSF_PORTAL_CONFIG = Object.freeze({
  enabled: true,
  supabaseUrl: "https://YOUR_PROJECT.supabase.co",
  supabaseAnonKey: "YOUR_PUBLIC_PUBLISHABLE_KEY",
  reportDownloadFunction: "report-download",
  sessionStorageKey: "qsf.portal.session.v1"
});
```

This static interim client keeps its token in `sessionStorage`, so a dedicated
portal origin with a backend-for-frontend and Secure, HttpOnly, SameSite cookies
is preferable before broad production use. Keep all third-party scripts off the
portal pages and set Content Security Policy/security headers at that protected
host.

## 4. Import trades through the ledger

The source of truth is an immutable transaction ledger—not an editable holdings
table. Each event has balanced signed legs whose `amount` values sum to zero.
Corrections are a reversal transaction followed by the corrected transaction.
The posting RPC also verifies instrument arithmetic, leg count, currency,
reversal symmetry, trade dates, and an idempotency payload fingerprint.

Minimum option fields are: underlying, exact OCC contract/provider symbol,
expiration, strike, call/put, signed contract quantity, 100x multiplier (unless
the contract specifies otherwise), execution price, fees, timestamp, and a
broker confirmation/idempotency reference. Multi-leg spreads are stored as
separate linked position legs.

The local, git-ignored reconciliation seed is provisional and must be reviewed
before import. The attached report and later typed holdings list do not fully
agree, exact identifiers for the option contracts still require confirmation,
and the later cash change needs its underlying transaction or an explicit
cash-adjustment classification. Do not issue a statement from that seed as-is.

## 5. Quote and NAV jobs

Run quote ingestion centrally; browsers should read the cached observations.
Every mark records provider time, retrieval time, source, and one of `delayed`,
`prior_close`, `manual`, `estimated`, or `stale`.

For a zero-cost prototype, use end-of-day equity/ETF data only where the source
terms permit it, FRED's daily three-month Treasury series, and explicit manual
or prior-close option marks. A dependable investor-facing 15-minute U.S.
equity/options feed generally requires a licensed provider; options also need
complete contract identifiers and OPRA coverage.

Daily NAV should be computed server-side as cash plus signed marked positions,
with shorts/liabilities kept negative. Performance must remove deposits and
withdrawals (unitized NAV/time-weighted return); a change in AUM is not by itself
investment return.

## 6. Private LaTeX report worker

On a report click, the worker must:

1. Authenticate the caller and re-check portfolio membership.
2. Freeze one immutable valuation snapshot and quote-quality summary.
3. Render with `reporting/render_report.py`.
4. Copy `assets/images/qsf-mark.png` into the isolated compile directory as
   `qsf-mark.png`.
5. Compile with no network and shell escape disabled, for example in a locked
   container with `latexmk`/TeX Live and strict CPU, memory, and time limits.
6. Hash the PDF, store it in private object storage, update the report job, and
   return a short-lived signed URL only to the requesting member.

The browser queues through the checked `request_investor_report` RPC, which
deduplicates active jobs and rate-limits completed requests. Direct table
inserts are not granted to investor sessions.

Do not claim that a PDF is generated from LaTeX until this worker is actually
deployed and its output has been visually checked. The renderer deliberately
does not invoke a TeX process itself.

## 7. Publishing checklist

- Keep `_OLD` website copies for rollback, but never make `_OLD` copies of
  secrets or private investor data.
- Run the market-data workflow and site checks in staging.
- Review all public performance provenance, fee treatment, and disclosures with
  qualified compliance counsel before presenting results as fund performance.
- Rotate any credential accidentally shared in chat, source, logs, or email.
- Back up the database and test restoration before calling the portal production.
