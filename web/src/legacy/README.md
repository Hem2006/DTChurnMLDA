# Legacy UI (not built)

Two earlier front ends, kept for reference. Neither is imported by `main.jsx`, so Vite does not bundle them; the API endpoints they
used are unchanged.

- `galaxy/` — the 3D "Customer Galaxy" (three.js): every hold-out customer as a star, per-customer twin, campaigns, illustrative
  timeline, n8n actions panel. Replaced by the retention-agent app because a business audience needs the process, not the scene.
  To bring it back, point `main.jsx` at `legacy/galaxy/App.jsx` + `legacy/galaxy/styles.css` and fix the relative imports
  (`../api.js`, `../lib.js`, `../hooks.js` become `../../…`).
- the multi-page app (login, overview, score, what-if, recommend, batch, autopilot, incidents, review queue, model): point `main.jsx`
  at `legacy/App.jsx` + `legacy/state.jsx` + `legacy/styles.css`/`autopilot.css`. `legacy/lib.test.js` still runs under `npm test`.
