/*
 * PUBLIC DEMONSTRATION CONFIGURATION.
 *
 * The demo account labels, access code, holdings, and values are intentionally
 * public test data. The demo is not authentication and must never be reused for
 * real clients. The future Supabase configuration remains disabled below.
 */
window.QSF_PORTAL_CONFIG = Object.freeze({
  mode: "public-demo",
  demoDataUrl: "../data/demo-accounts.json",
  demoQuoteUrls: [
    "https://raw.githubusercontent.com/ualyanak/qsf-website/market-data/data/demo-quotes.json",
    "../data/demo-quotes.json"
  ],
  demoSessionKey: "qsf.publicDemo.session.v1",
  demoStoragePrefix: "qsf.publicDemo.account.",
  enabled: false,
  supabaseUrl: "",
  supabaseAnonKey: "",
  reportDownloadFunction: "",
  sessionStorageKey: "qsf.portal.session.v1"
});
