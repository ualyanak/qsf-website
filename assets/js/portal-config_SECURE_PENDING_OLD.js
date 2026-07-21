/*
 * Public deployment configuration only.
 *
 * Supabase project URLs and publishable/anon keys are designed to be public;
 * service-role keys, passwords, holdings, and investor identifiers never belong
 * in this file. Keep the portal disabled until the backend and RLS migration
 * have been verified. See docs/PORTAL_SETUP.md.
 */
window.QSF_PORTAL_CONFIG = Object.freeze({
  enabled: false,
  supabaseUrl: "",
  supabaseAnonKey: "",
  reportDownloadFunction: "",
  sessionStorageKey: "qsf.portal.session.v1"
});
