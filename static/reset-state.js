'use strict';
// Clear only LabelDesk's old browser state once for this fresh installation.
try {
  const resetVersion = '2026-10-09-fresh-inventory-1';
  if (localStorage.getItem('labeldesk-reset-version') !== resetVersion) {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith('labeldesk-')) localStorage.removeItem(key);
    }
    localStorage.setItem('labeldesk-reset-version', resetVersion);
  }
} catch (_) { /* Backup and restore remain usable if browser storage is blocked. */ }
