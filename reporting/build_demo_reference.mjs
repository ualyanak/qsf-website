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
const accountId = process.argv[2] || "ahub";
const account = data.accounts[accountId];
if (!account) throw new Error("Unknown demo account: " + accountId);

const holdings = account.positions.map((position) => {
  const instrument = data.instruments[position.instrument];
  const publicQuote = quoteSnapshot.quotes[instrument.quote_symbol || position.instrument];
  const manual = Number.isFinite(Number(instrument.manual_mark));
  const price = manual
    ? Number(instrument.manual_mark)
    : publicQuote && Number.isFinite(Number(publicQuote.price))
      ? Number(publicQuote.price)
      : Number(position.basis_price);
  const markQuality = manual
    ? "manual_demo"
    : publicQuote
      ? String(publicQuote.quality || "public_delayed")
      : "fallback_opening_mark";
  const markAsOf = manual ? instrument.manual_as_of : publicQuote ? publicQuote.as_of : account.opening_as_of;
  return {
    id: position.instrument,
    symbol: instrument.symbol,
    name: instrument.name,
    assetClass: instrument.asset_class,
    quantity: Number(position.quantity),
    multiplier: Number(instrument.multiplier || 1),
    price,
    marketValue: Number(position.quantity) * Number(instrument.multiplier || 1) * price,
    markAsOf,
    markSource: manual ? instrument.manual_source : publicQuote ? publicQuote.source : "Published opening-basis fallback",
    markQuality
  };
}).sort((a, b) => Math.abs(b.marketValue) - Math.abs(a.marketValue));

const positionsValue = holdings.reduce((sum, holding) => sum + holding.marketValue, 0);
const nav = Number(account.cash) + positionsValue;
const groups = new Map();
for (const holding of holdings) groups.set(holding.assetClass, (groups.get(holding.assetClass) || 0) + Math.abs(holding.marketValue));
if (Number(account.cash) > 0) groups.set("Cash", (groups.get("Cash") || 0) + Number(account.cash));
const gross = [...groups.values()].reduce((sum, value) => sum + value, 0);
const allocation = [...groups.entries()]
  .map(([name, value]) => ({ name, value, percent: gross ? value / gross * 100 : 0 }))
  .sort((a, b) => b.value - a.value);

const history = [...account.history, {
  date: new Date().toISOString().slice(0, 10),
  value: nav,
  kind: "reference_render"
}];

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
  nav,
  returnPct: (nav / Number(account.opening_nav) - 1) * 100,
  positionsValue,
  holdings,
  allocation,
  history,
  latestMarkAsOf: holdings.map((item) => item.markAsOf).filter(Boolean).sort().at(-1) || null,
  quoteSnapshotGeneratedAt: quoteSnapshot.generated_at,
  staleCount: holdings.filter((item) => item.markQuality !== "public_delayed").length,
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
