# CACSMS ONE — Unified Intelligence Platform

A React/Vite CACSMS ONE hub with an integrated `/sports/*` Sports AI portal. The landing page and Traders portal are preserved. The Sports AI frontend is mounted in `src/sports`; its FastAPI/SQLite module, migrations, tests, and worker are in `C:\Users\Cacsms Limited\Downloads\CACSMS_Sports_AI_Expanded_SQLite_Integration\CACSMS_SPORTS_AI`.

## Run

Requires Node.js 20.19+ or 22.12+.

```bash
npm install
npm run dev
```

Open `http://localhost:5173`. For deployment, run `npm run build` and serve `dist/` using your existing frontend hosting.

## Sports AI integration

The Sports AI portal is reachable from the existing Sports AI card or directly at `/sports/dashboard`. It returns to the hub at `/hub`. The development Vite server proxies `/api/sports-ai/*` to `http://127.0.0.1:8011`; the backend must be started separately:

```powershell
Set-Location 'C:\Users\Cacsms Limited\Downloads\CACSMS_Sports_AI_Expanded_SQLite_Integration\CACSMS_SPORTS_AI\backend'
pip install -r requirements.txt
$env:SPORTS_LOCAL_PREVIEW='1'
# For isolated development, omit SPORTS_SQLITE_PATH.
# For integration, set SPORTS_SQLITE_PATH to the verified existing CACSMS ONE SQLite file.
uvicorn main:app --host 127.0.0.1 --port 8011
```

`SPORTS_LOCAL_PREVIEW=1` is strictly for loopback development, never production. Production must mount the Sports AI FastAPI router in the existing backend, replace its development auth dependency with CACSMS ONE session/RBAC, and serve the frontend and API on the same origin. See the Sports AI module README for database backup/migration guidance and the external integration limitations.

## Integrate with CACSMS Traders

1. Copy `src/Hub.jsx`, `src/styles.css`, `src/portals.js` and `src/assets/` into your existing React frontend.
2. Import `Hub` and add a route to `/hub` using the **existing** React Router. Do not nest a second `BrowserRouter` inside the existing application.
3. Replace the sample portal routes (`/traders`, `/sports`, `/operator`) with the actual routes in the existing application. Make the existing login redirect to `/hub` only after authentication.
4. Supply `config` to `<Hub config={hubConfig} />` using the logged-in user, authorized portals, notification counts and server-derived system status. `defaultHubConfig` is demonstration data and must not be interpreted as live status.
5. Provide `onOpenPortal`, `onRequestAccess`, `onNotifyMe`, `onLogout` callbacks to connect to your backend workflows. A fallback is provided for navigation and explanatory messages, but no access-request or notification backend is bundled.
6. Enforce authorization on the backend and portal routes; hiding cards in the UI is not access control.
7. Configure `/hub` and other SPA routes to serve `index.html` on refresh.

Example:

```jsx
import Hub from './Hub';
<Route path="/hub" element={<Hub config={hubConfig} onOpenPortal={p => navigate(p.route)} onLogout={logout} />} />
```

## Scope and fidelity

The CACSMS ONE landing page remains the hub; the integrated Sports AI module is separate and does not replace the Traders portal. The design is visually close to the generated concept, but exact pixel-level equivalence is not guaranteed across browsers, fonts and screen sizes. The globe and card art are assets; navigation, text, buttons, dates, cards and responsive behavior are real UI.

Portal statuses, user identity, sessions and health are demo defaults until wired to your existing APIs. The date/time display is live in the Africa/Lagos timezone, not hard-coded.
