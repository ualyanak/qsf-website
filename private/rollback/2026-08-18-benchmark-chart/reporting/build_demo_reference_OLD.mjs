#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..");
const require = createRequire(import.meta.url);

globalThis.PDFLib = require(path.join(repo, "assets/vendor/pdf-lib.min.js"));
await import(path.join(repo, "assets/js/demo-pdf.js"));

const data = JSON.parse(fs.readFileSync(path.join(repo, "data/demo-accounts.json"), "utf8"));
const quoteSnapshot = JSON.parse(fs.readFileSync(path.join(repo, "data/demo-quotes.json"), "utf8"));
const historyPath = path.join(repo, "data/demo-portfolio-history.json");
const historySnapshot = fs.existsSync(historyPath)
  ? JSON.parse(fs.readFileSync(historyPath, "utf8"))
  : { schema_version: 1, demo: true, generated_at: null, accounts: {} };
const accountId = process.argv[2] || "ahub";
const account = data.accounts[accountId];
if (!account) throw new Error("Unknown demo account: " + accountId);

const holdings = account.positions.map((position) => {
  const instrument = data.instruments[position.instrument];
  const publicQuote = quoteSnapshot.quotes[instrument.quote_symbol || position.instrument];
  const manual = Number.isFinite(Number(instrument.manual_mark));
  const automatic = ["public_delayed", "model_delayed"].includes(instrument.mark_mode)
    && publicQuote
    && Number.isFinite(Number(publicQuote.price));
  const price = automatic
    ? Number(publicQuote.price)
    : manual
      ? Number(instrument.manual_mark)
      : Number(position.basis_price);
  const markQuality = automatic
    ? String(publicQuote.quality || instrument.mark_mode)
    : manual
      ? "manual_demo"
      : "fallback_opening_mark";
  const markAsOf = automatic && instrument.mark_mode === "model_delayed" && publicQuote.valuation_as_of
    ? publicQuote.valuation_as_of
    : automatic
      ? publicQuote.as_of
      : manual
        ? instrument.manual_as_of
        : account.opening_as_of;
  const markInputAsOf = automatic ? publicQuote.as_of : markAsOf;
  return {
    id: position.instrument,
    symbol: instrument.symbol,
    name: instrument.name,
    assetClass: instrument.asset_class,
    cashEquivalent: instrument.cash_equivalent === true,
    quantity: Number(position.quantity),
    multiplier: Number(instrument.multiplier || 1),
    price,
    marketValue: Number(position.quantity) * Number(instrument.multiplier || 1) * price,
    markAsOf,
    markInputAsOf,
    markSource: automatic ? publicQuote.source : manual ? instrument.manual_source : "Published opening-basis fallback",
    markQuality
  };
}).sort((a, b) => Math.abs(b.marketValue) - Math.abs(a.marketValue));

const positionsValue = holdings.reduce((sum, holding) => sum + holding.marketValue, 0);
const cashEquivalentMarketValue = holdings.reduce((sum, holding) => sum + (holding.cashEquivalent ? holding.marketValue : 0), 0);
const cashAndCashEquivalents = Number(account.cash) + cashEquivalentMarketValue;
const nav = Number(account.cash) + positionsValue;
const groups = new Map();
for (const holding of holdings) {
  const group = holding.cashEquivalent ? "Cash & Cash Equivalents" : holding.assetClass;
  groups.set(group, (groups.get(group) || 0) + Math.abs(holding.marketValue));
}
if (Number(account.cash) > 0) groups.set("Cash & Cash Equivalents", (groups.get("Cash & Cash Equivalents") || 0) + Number(account.cash));
const gross = [...groups.values()].reduce((sum, value) => sum + value, 0);
const allocation = [...groups.entries()]
  .map(([name, value]) => ({ name, value, percent: gross ? value / gross * 100 : 0 }))
  .sort((a, b) => b.value - a.value);

const today = new Date().toISOString().slice(0, 10);
const historyByDate = new Map();
for (const point of account.history || []) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(point.date)) && point.date <= today && Number.isFinite(Number(point.value))) {
    historyByDate.set(point.date, { date: point.date, value: Number(point.value), kind: point.kind || "published_test" });
  }
}
const packagedAccountHistory = historySnapshot.accounts && historySnapshot.accounts[accountId];
const packagedPoints = Array.isArray(packagedAccountHistory)
  ? packagedAccountHistory
  : packagedAccountHistory && Array.isArray(packagedAccountHistory.points)
    ? packagedAccountHistory.points
    : packagedAccountHistory && Array.isArray(packagedAccountHistory.history)
      ? packagedAccountHistory.history
      : [];
for (const point of packagedPoints) {
  const value = Number(point.value == null ? point.nav : point.value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(point.date)) && point.date <= today && Number.isFinite(value)) {
    historyByDate.set(point.date, { date: point.date, value, kind: point.kind || "nightly_close" });
  }
}
const history = [...historyByDate.values()].sort((left, right) => left.date.localeCompare(right.date));

const view = {
  demo: true,
  accountId,
  accountName: account.display_name,
  portfolioName: account.portfolio_name,
  currency: account.currency || "USD",
  openingAsOf: account.opening_as_of,
  openingNav: Number(account.opening_nav),
  baselineLabel: account.baseline_label || "Published test baseline",
  returnBasisLabel: account.return_basis_label || "Illustrative return",
  returnBasisNote: account.return_basis_note || null,
  cash: Number(account.cash),
  cashEquivalentMarketValue,
  cashAndCashEquivalents,
  nav,
  returnPct: (nav / Number(account.opening_nav) - 1) * 100,
  positionsValue,
  holdings,
  allocation,
  history,
  historySnapshotGeneratedAt: historySnapshot.generated_at || null,
  historyStatus: packagedPoints.length ? "ready" : "formation-fallback",
  latestMarkAsOf: holdings.map((item) => item.markAsOf).filter(Boolean).sort().at(-1) || null,
  quoteSnapshotGeneratedAt: quoteSnapshot.generated_at,
  staleCount: holdings.filter((item) => !["public_delayed", "model_delayed"].includes(item.markQuality)).length,
  modifiedAt: null,
  generatedAt: new Date().toISOString()
};

const logo = new Uint8Array(fs.readFileSync(path.join(repo, "assets/images/qsf-mark.png")));
const bytes = await globalThis.QSFDemoPdf.build(view, logo);
const outputDir = path.join(repo, "output/pdf");
fs.mkdirSync(outputDir, { recursive: true });
const output = path.join(outputDir, "QSF-" + accountId + "-public-demo-reference.pdf");
fs.writeFileSync(output, bytes);
console.log(JSON.stringify({ output, bytes: bytes.length, nav: Number(nav.toFixed(2)), holdings: holdings.length }));
