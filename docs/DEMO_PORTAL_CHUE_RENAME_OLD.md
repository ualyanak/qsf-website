# QSF public investor portal demonstration

This deployment is an intentionally public prototype. It does not provide
secure authentication, private investor access, official accounting, or broker
connectivity.

## Published test access

- Demo portfolio label: `ahub`
- Public demo code: `1234567`
- Demo portfolio label: `chuenergard`
- Public demo code: `1234567`

These labels, the code, holdings, and values are stored in the public
repository. They must never be reused for real investor accounts.

## Seed interpretation

- The supplied IVR `$809.99` is treated as the total basis for 100 shares
  (`$8.0999` per share), rather than as a per-share price.
- The supplied `$75` INFQ call cost is treated as `$0.75` per option share,
  with the standard 100x contract multiplier.
- The INFQ call vertical uses the supplied `$1.37` net mark per option share
  and a 100x multiplier.
- The BULL and INFQ option marks remain manual until exact tradable contract
  identifiers and an appropriate data source are available.

## Data flow

1. `data/demo-accounts.json` contains the published test portfolios.
2. `scripts/update_demo_quotes.py` retrieves an allowlisted set of best-effort
   delayed equity and ETF snapshots.
3. `.github/workflows/refresh-market-data.yml` publishes those snapshots to
   `data/demo-quotes.json` on the `market-data` branch.
4. Options use manual test marks until a specific contract identifier and
   suitable licensed source are available.
5. `assets/js/portal-demo.js` derives the dashboard, holdings, allocation,
   return, local scenario, and report inputs from one calculation path.

An open dashboard rechecks the quote snapshot every 15 minutes, and report
generation performs another forced quote check before deriving the PDF view.

Scheduled GitHub Actions runs are best effort and can be delayed. The portal
therefore preserves each mark's source, timestamp, and quality label.
Observations older than four days are downgraded to a stale status rather than
presented as current. The `chuenergard` sample uses the user-supplied 25 SPY
shares at a $400 per-share cost basis; because no acquisition date was supplied,
the dashboard labels the gain against cost and does not invent dated history.

## Local scenario editor

The editor under `/admin/` writes only to browser `localStorage`, namespaced
by demo account. It supports:

- simulated buys and sells;
- share or option multipliers;
- illustrative fees and cash effects;
- manual mark overrides;
- append-only reversals;
- JSON export; and
- reset to the published sample.

Local edits do not write to GitHub, sync across devices, place trades, or update
fund records.

## PDF reports

`assets/js/demo-pdf.js` uses the vendored `pdf-lib` build to create a
two-page PDF in the browser. The report uses the same derived view model as the
dashboard and contains a demonstration watermark and non-statement disclosure
on every page. The checked reference artifact remains in the repository for
review but `output/` is excluded from the published Pages site.

Run the reference renderer and visual checks locally:

```bash
node reporting/build_demo_reference.mjs ahub
pdfinfo output/pdf/QSF-ahub-public-demo-reference.pdf
pdftoppm -png output/pdf/QSF-ahub-public-demo-reference.pdf tmp/pdfs/qsf-demo
```

The Supabase migration and production-oriented portal adapter remain in the
repository for future secure implementation, but they are disabled in this
public demo.
