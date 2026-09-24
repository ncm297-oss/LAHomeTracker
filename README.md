# West Side LA Housing Tracker

Personal tool for deciding when a specific West LA listing is priced low enough that buying beats renting over a 5–10 year hold, and (later) for tracking listings, comps and rates.

Built in phases:

| Phase | What | Status |
|---|---|---|
| 1 | Buy vs rent breakeven calculator (static page) | done |
| 1B | Financing scenarios (mortgage, delayed financing, DTI, deductions) | next |
| 2 | SQLite listing tracker with deal scoring, market panel, rate monitoring | planned |
| 3 | Weekly automated triage + email digest | planned |

## Phase 1: the calculator

Everything is in `docs/` so GitHub Pages can serve it straight from the `main` branch.

- `docs/index.html`, `docs/style.css`, `docs/app.js` — the page. No build step, no dependencies, nothing leaves the browser.
- `docs/calc.js` — the engine. Pure functions, shared with the tests and reused by later phases.
- `tests/calc.test.js` — engine tests.

### Run locally

Open `docs/index.html` in a browser, or serve the folder:

```bash
python -m http.server 8000 --directory docs
```

then visit http://localhost:8000.

### Test

```bash
node --test tests/calc.test.js
```

### Publish on GitHub Pages

1. Push this repo to GitHub.
2. Repository → Settings → Pages → Source: **Deploy from a branch**, Branch: `main`, Folder: `/docs`.
3. The page appears at `https://<user>.github.io/<repo>/`. Every input is encoded in the URL hash, so a scenario can be shared by copying the link.

### Model

Both households start with the same cash (price + buy closing). The owner buys the house; the renter invests the cash at the expected return. Each year the cheaper side invests the difference. At the end the owner sells (5.5% costs, capital-gains tax over the $500K MFJ exclusion) and the two net-worth figures are compared. Breakeven price, appreciation and rent solve for the input that makes the difference zero. The "How the math works" section on the page and the header comment in `calc.js` spell out every assumption.

Default opportunity cost is the **equities** preset (7% pre-tax, gains taxed once at sale), because that is where the cash sits today. The **Treasuries** preset (4.5%, taxed yearly at the federal rate, CA-exempt) is one tap away.

## Later phases (setup notes will land here)

- RentCast API key, Gmail read-only OAuth, FRED API key
- Where to drop Redfin CSV exports and Zillow Research / Redfin Data Center files
- Running the weekly job locally before scheduling it
