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
- The corrected August 4 test reallocation records two SGOV fills: 23 shares at
  `$100.65` and one share at `$100.68`. Total proceeds are `$2,415.63`; 320 BULL
  shares are then purchased at `$7.20` for `$2,304.00`. Two SGOV shares remain,
  and the difference increases raw cash from `$413.83` to `$525.46`.
- The August 13, 10:00 a.m. CDT update sells three INFQ `$25` calls at `$1.10`,
  five INFQ `$10/$17.50` call verticals at `$2.42`, and the remaining fifteen
  verticals at `$2.43`. Gross proceeds are `$5,185.00`. It then purchases 51
  SGOV shares at `$100.53` for `$5,127.03`; net proceeds of `$57.97` increase
  the existing `$525.46` cash balance to `$583.43`. Together with the two
  existing shares, the account now holds 53 SGOV shares at a
  `$100.5318867925` weighted basis. One INFQ `$25` call remains and
  the vertical position is closed.
- The August 14 date-only update sells all 30 TSSI shares at `$9.65` with no
  reported fees. Gross and net proceeds are `$289.50`; the supplied `$9.94`
  per-share basis totals `$298.20`, producing an illustrative realized loss of
  `$8.70`. The proceeds increase raw cash from `$583.43` to `$872.93`, and the
  current TSSI position is closed. TSSI remains in the formation ledger and
  instrument catalog so prior nightly history can still be reconstructed.
- The August 18 update records two user-provided total cash dividends: `$0.61`
  from SGOV and `$12.00` from IVR. The combined `$12.61` credit increases raw
  cash from `$872.93` to `$885.54`; holdings and cost bases are unchanged.
- The August 19 date-only update sells 120 of the 320 BULL shares at `$8.45`
  with no reported fees. Gross and net proceeds are `$1,014.00`; the sold
  shares' `$7.20` basis totals `$864.00`, producing an illustrative realized
  gain of `$150.00`. Proceeds increase raw cash from `$885.54` to `$1,899.54`.
  The remaining 200 BULL shares retain their `$7.20` per-share basis.
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
   completed nightly value while the headline account value may be fresher. The
   same snapshot contains public SPY, GLD, and Bitcoin comparison series aligned
   to the account's calendar dates. SPY and GLD use Yahoo adjusted daily closes;
   Bitcoin uses Yahoo raw daily closes mapped to their UTC candle dates. The
   account's own position history continues to use raw closes.
4. `.github/workflows/refresh-market-data.yml` publishes intraday quote
   snapshots, while `.github/workflows/refresh-portfolio-history.yml` rebuilds
   and publishes the complete nightly series at 00:30 UTC, after the US market
   close and after the prior UTC Bitcoin daily candle has closed. Both write to
   the `market-data` branch. Main-branch copies are packaged fallbacks when a
   remote snapshot is unavailable.
5. The updater calculates the three seeded option strategy marks with a
   Black-Scholes estimate calibrated to the supplied opening premium. The
   model uses a 4% annual risk-free assumption, no dividend yield, the verified
   January 15, 2027 expiry, and the newest delayed underlier observation.
6. `assets/js/portal-demo.js` derives the dashboard, cash-equivalents metric,
   holdings, allocation, return, nightly-history chart, local scenario, and
   report inputs from one calculation path.

### Completed-night analytics

The history snapshot also publishes `accounts.ahub.analytics`, calculated only
through `last_completed_session` so a ledger event is never presented as
completed-night performance before that session closes.

- `realized_trades` uses average-cost lots and groups fills by ledger event and
  instrument. A closing long earns `(exit price - prior basis) × closed quantity
  × multiplier`; a closing short reverses that price difference. Reported fees
  are deducted proportionally from the closing portion. The displayed percent
  is realized P&L divided by the absolute closed basis. Same-direction trades
  update weighted basis, partial closes preserve it, and a position flip splits
  the transaction into closing and new-opening portions.
- `contributors` combines each security or underlying strategy group's realized
  P&L, tagged income, and completed-night unrealized P&L. Its return percent uses
  cumulative tracked basis (disposed basis plus the remaining position basis),
  while `portfolio_contribution_pct` divides the dollar contribution by the
  `$9,900` formation NAV. BULL shares and the BULL put share one group; both INFQ
  strategies share another. This is a security-and-strategy attribution, not a
  claim that every row is an operating company.
- Each attribution group has an administrator-assigned `risk_level` restricted
  to `low`, `medium`, or `high`. The dashboard displays the category directly
  below every contributor name using green, yellow, or red text and background,
  while retaining the words “LOW RISK,” “MEDIUM RISK,” or “HIGH RISK” for users
  who cannot distinguish color. The category is illustrative and qualitative;
  it is not calculated from P&L, volatility, liquidity, option delta, or investor
  suitability. The approved map is INFQ/TSSI/IVR high; BULL/PLTR/QBTS/IBM/SPY/
  NVDA/WMT medium; and PHYS/SGOV low.
- `risk_history` replays those same administrator-assigned categories across
  every completed-night portfolio state. Each point is a 100% gross marked-value
  mix split into Low, Medium, and High risk; positive cash is Low risk, SGOV
  remains Low risk while held long, negative cash or short SGOV is conservatively
  placed in High risk, and other short positions contribute their absolute marked
  value to the applicable administrator category. The dashboard renders the
  categories as green, yellow, and red stacked bands with a selected-date dollar
  and percentage breakdown. Its outlined `Now` endpoint uses the latest delayed
  marks and browser-local scenario when present; the preceding points remain the
  immutable published nightly record. Historical categories are restated whenever
  the current administrator classification map changes.
- The unassigned July 20 `$24.00` strategy-P&L adjustment remains separate in
  `unattributed_pnl`. Tagged SGOV and IVR dividends belong to their contributor
  groups. External-flow classifications are excluded from P&L. The published
  reconciliation requires attributed P&L plus unattributed P&L and external
  flows to explain the change from formation NAV to the completed-night NAV.
- `exposure_history` replays the same ledger and marks used for nightly NAV.
  Each point is a 100% gross marked-value mix: absolute position market values
  plus absolute cash. Positive cash and long SGOV are combined as `Cash & Cash
  Equivalents`; negative cash or a short cash-equivalent position is financing.
  Formation uses supplied basis values, completed sessions use raw closes and
  the seeded option models, and non-trading days carry the preceding mix exactly.
  This is market-value exposure, not option delta or notional exposure.

At the August 19 completed close, the five realized-trade rows are the combined
INFQ vertical sale (`+$2,115.00`, `+77.19%`), the BULL partial sale (`+$150.00`,
`+17.36%`), the INFQ `$25` call sale (`+$105.00`, `+46.67%`), the combined SGOV
sale fills (`+$1.71`, `+0.07%`), and the TSSI close (`-$8.70`, `-2.92%`). SGOV's
completed-night contribution is `+$5.40` after its recorded `$0.61` dividend
and current unrealized result. INFQ is the largest contributor at `+$2,295.67`;
IVR is the smallest at `-$39.99`. Attributed P&L of `$3,122.02` plus the separate
`$24.00` adjustment reconciles exactly to the `$3,146.02` rise from `$9,900.00`
to `$13,046.02` using Yahoo's final August 19 raw closes.

An open dashboard or local editor rechecks the quote and history snapshots every
15 minutes, and report generation performs another forced check before deriving
the PDF view. The performance-comparison chart and PDF use the latest completed
end-of-day history point; they do not add an intraday performance point. The two
advanced mix charts append a clearly outlined `Now` endpoint from the latest
available delayed marks. Option rows distinguish the current model-valuation time
from the delayed underlier observation used as the model input.

Each comparison starts at the `$9,900.00` formation NAV on July 17, 2026 and is
normalized as `formation NAV × daily price ÷ formation-date price`; the first
point is forced to exactly the formation NAV. This presents hypothetical growth
of the same starting value, not an actual holding or investable return. GLD is a
publicly traded gold proxy rather than spot gold. SPY and GLD carry their last
close across non-trading days, while Bitcoin can move every UTC calendar day.
Runs triggered before UTC midnight ignore the still-open Bitcoin candle and
carry the last completed candle instead.
If a comparison download fails, the account NAV still publishes: a previously
validated comparison is retained and carried forward with degraded quality, or
that comparison is marked unavailable when no valid formation baseline exists.

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
