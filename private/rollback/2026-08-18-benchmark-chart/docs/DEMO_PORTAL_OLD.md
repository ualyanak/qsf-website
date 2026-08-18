# QSF public investor portal demonstration

This deployment is an intentionally public prototype. It does not provide
secure authentication, private investor access, official accounting, or broker
connectivity.

## Published test access

- Demo portfolio label: `ahub`
- Public demo code: `1234567`
- Demo portfolio label: `chue`
- Public demo code: `1234567`

These labels, the code, holdings, and values are stored in the public
repository. They must never be reused for real investor accounts.

## Seed interpretation

- The supplied IVR `$809.99` is treated as the total basis for 100 shares
  (`$8.0999` per share), rather than as a per-share price.
- The supplied `$75` INFQ call cost is treated as `$0.75` per option share,
  with the standard 100x contract multiplier.
- The August 4 test reallocation treats `$2,304` as the proceeds from selling
  23 of the original 26 SGOV shares and immediately purchases 320 BULL shares
  at `$7.20`. Three SGOV shares remain and the cash balance is unchanged.
- The August 13, 10:00 a.m. CDT update sells three INFQ `$25` calls at `$1.10`,
  five INFQ `$10/$17.50` call verticals at `$2.42`, and the remaining fifteen
  verticals at `$2.43`. Gross proceeds are `$5,185.00`. It then purchases 51
  SGOV shares at the interpreted `$100.53` price for `$5,127.03`; net proceeds
  of `$57.97` increase the existing `$413.83` cash balance to `$471.80`.
  Together with the three existing shares, the account now holds 54 SGOV
  shares at a `$100.5327777778` weighted basis. One INFQ `$25` call remains and
  the vertical position is closed.
- The August 14 date-only update sells all 30 TSSI shares at `$9.65` with no
  reported fees. Gross and net proceeds are `$289.50`; the supplied `$9.94`
  per-share basis totals `$298.20`, producing an illustrative realized loss of
  `$8.70`. The proceeds increase raw cash from `$471.80` to `$761.30`, and the
  current TSSI position is closed. TSSI remains in the formation ledger and
  instrument catalog so prior nightly history can still be reconstructed.
- The displayed `Cash & Cash Equivalents` metric is the raw cash
  balance plus the signed, latest marked value of instruments explicitly tagged
  as cash equivalents. SGOV is currently the only tagged holding. SGOV remains
  in total positions and NAV exactly once, and the gross-allocation view combines
  SGOV and positive cash into one `Cash & Cash Equivalents` category.
- The INFQ call vertical uses the supplied `$1.37` net mark per option share
  and a 100x multiplier.
- "JAN 27" is interpreted as January 2027. The seeded listed contracts are
  `BULL270115P00010000`, `INFQ270115C00010000`,
  `INFQ270115C00017500`, and `INFQ270115C00025000`, all expiring January 15,
  2027.
- A public no-key OPRA feed with redistribution permission is not available.
  The portal therefore publishes clearly labeled automatic model estimates,
  not listed-option quotes. Each estimate is anchored to the supplied July 17
  opening premium and moves with the delayed BULL or INFQ price and time decay.

## Data flow

1. `data/demo-accounts.json` contains the published test portfolios and only a
   formation-value fallback for dated history.
2. `scripts/update_demo_quotes.py` retrieves an allowlisted set of best-effort
   delayed equity and ETF snapshots, including the SGOV holding and the BULL
   and INFQ underliers.
3. `data/demo-portfolio-ledger.json` records the formation portfolio and each
   supplied cash or trade event. `scripts/update_demo_portfolio_history.py`
   replays that ledger against raw daily closes and writes prior completed-night
   values to `data/demo-portfolio-history.json`; the chart uses the latest
   completed nightly value while the headline account value may be fresher.
4. `.github/workflows/refresh-market-data.yml` publishes intraday quote
   snapshots, while `.github/workflows/refresh-portfolio-history.yml` rebuilds
   and publishes the complete nightly series after the market close. Both write
   to the `market-data` branch. Main-branch copies are packaged fallbacks when a
   remote snapshot is unavailable.
5. The updater calculates the three seeded option strategy marks with a
   Black-Scholes estimate calibrated to the supplied opening premium. The
   model uses a 4% annual risk-free assumption, no dividend yield, the verified
   January 15, 2027 expiry, and the newest delayed underlier observation.
6. `assets/js/portal-demo.js` derives the dashboard, cash-equivalents metric,
   holdings, allocation, return, nightly-history chart, local scenario, and
   report inputs from one calculation path.

An open dashboard or local editor rechecks the quote and history snapshots every
15 minutes, and report generation performs another forced check before deriving
the PDF view. The chart and PDF use the latest completed end-of-day history point;
they do not add an intraday point. Option rows distinguish the current
model-valuation time from the delayed underlier observation used as the model
input.

Scheduled GitHub Actions runs are best effort and can be delayed. The portal
therefore preserves each mark's source, timestamp, and quality label.
The workflow already requests Yahoo Finance chart snapshots four times per
hour, and open dashboards recheck the published snapshot every 15 minutes.
GitHub can delay or drop scheduled jobs, so this static demonstration cannot
guarantee a 20-minute market-data service without a separate scheduler and an
authorized data provider.
Observations older than four days are downgraded to a stale status rather than
presented as current. Model estimates are labeled `AUTO MODEL` and explicitly
state that they are not option-market quotes. The `chue` sample uses the
user-supplied 25 SPY shares at a $400 per-share cost basis; because no acquisition
date was supplied, the dashboard labels the gain against cost and does not
invent observations before generated nightly history begins.

## Local scenario editor

The editor under `/admin/` writes only to browser `localStorage`, namespaced
by demo account. It supports:

- simulated buys and sells;
- share or option multipliers;
- illustrative fees and cash effects;
- fallback marks for custom instruments;
- append-only reversals;
- JSON export; and
- reset to the published sample.

Local edits do not write to GitHub, sync across devices, place trades, or update
fund records. Transactions in registered instruments continue to use their
automatic mark. A newly typed custom symbol keeps its execution price as a
fallback until an administrator registers its exact contract/quote metadata in
the repository; a static GitHub Pages site cannot send browser-local symbols to
the scheduled quote worker.

## PDF reports

`assets/js/demo-pdf.js` uses the vendored `pdf-lib` build to create a PDF in the
browser. The report uses the same derived view model, packaged nightly history,
cash-equivalents calculation, and allocation as the dashboard, includes a
per-holding mark-provenance page, and contains a demonstration watermark and
non-statement disclosure on every page. The checked reference artifact remains
in the repository for review but `output/` is excluded from the published Pages
site.

Run the reference renderer and visual checks locally:

```bash
node reporting/build_demo_reference.mjs ahub
pdfinfo output/pdf/QSF-ahub-public-demo-reference.pdf
pdftoppm -png output/pdf/QSF-ahub-public-demo-reference.pdf tmp/pdfs/qsf-demo
```

The Supabase migration and production-oriented portal adapter remain in the
repository for future secure implementation, but they are disabled in this
public demo.
