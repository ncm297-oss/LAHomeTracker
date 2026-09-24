# West Side LA Housing Tracker

Personal tool for deciding when a specific West LA listing is priced low enough that buying beats renting over a 5–10 year hold, and (later) for tracking listings, comps and rates.

Built in phases:

| Phase | What | Status |
|---|---|---|
| 1 | Buy vs rent breakeven calculator (static page) | done |
| 1B | Financing scenarios (mortgage, delayed financing, DTI, deductions) | done |
| 2 | SQLite listing tracker with deal scoring, market panel, rate monitoring | planned |
| 3 | Weekly automated triage + email digest | planned |

## Phase 1: the calculator

Everything is in `docs/` so GitHub Pages can serve it straight from the `main` branch.

- `docs/index.html`, `docs/style.css`, `docs/app.js` — the page. No build step, no dependencies, nothing leaves the browser.
- `docs/calc.js` — the engine. Pure functions, shared with the tests and reused by later phases.
- `tests/calc.test.js` — engine tests.

## Phase 1B: financing scenarios

`docs/finance.html` (+ `finance.js`, engine in `docs/mortgage.js`). Reached from the "Model financing" button; the URL hash carries the deal both ways.

- Five fixed scenarios (100% cash, 75% down, 50% down, DTI-max loan, cash-then-refi in year N) plus a custom down-payment slider, each with wealth difference vs renting, delta vs 100% cash, loan/rate (jumbo above the $1,249,125 conforming limit), cash at close, cash kept invested, year-1 monthly cost before and after deductions, and a one-line verdict. Scenarios whose loan exceeds the 43%-DTI limit are greyed.
- Retained cash earns its own return (equities 7% taxed at sale by default, or the Treasuries opportunity rate taxed yearly).
- Itemized deductions: SALT (property tax + CA income tax, capped at $40,400) and mortgage interest on the first $750K (federal) / $1M (CA), versus the standard deduction. A cash buyer gets the property-tax piece, which is why the all-cash figure here is a few thousand a year better than on the cash page unless you switch to the standard deduction.
- Crossover panel: after-tax cost of the loan vs after-tax return on retained cash, and the mortgage rate at which they tie.

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

## Assumptions (verified 2026-09-24)

Defaults live in `docs/calc.js` (`DEFAULTS`) and per-neighborhood data in `docs/assumptions.js`. Each is a placeholder until the tracker (Phase 2) can refresh it from data.

| Input | Default | What the data says | Source |
|---|---|---|---|
| Property tax (new purchase) | 1.2% custom; per neighborhood: LA City 1.19%, Santa Monica 1.29%, Culver City 1.15%, Marina del Rey 1.17% | 1% levy + school/college bonds from the county's 2025-26 guide: LAUSD 0.1196 + LACC 0.0485; SMMUSD 0.1912 + SMC 0.0786; Culver City USD 0.0832 + LACC 0.0485; plus ~0.02–0.03 city bonds and MWD. Verify a specific parcel with the TRA lookup. | [LA County Auditor-Controller 2025-26 Taxpayers' Guide](https://auditor.lacounty.gov/wp-content/uploads/2026/05/2025-2026-Taxpayers-Guide.pdf), [TRA lookup](https://onlineapps.auditor.lacounty.gov/TRA/) |
| Prop 13 cap | 2%/yr, never above market | Statute; Prop 8 lets assessed value fall in a downturn | — |
| Insurance (fire/HO) | $10,000/yr | Non-brush Westside on a $1.2–1.8M rebuild: roughly $6–12K. VHFHSZ parcels (Brentwood canyons): FAIR Plan + DIC $8–25K+; FAIR Plan +29.1% from Oct 15 2026, dwelling cap raised to $3M. National $1M-dwelling average $6,253. | [Latent: CA cost by region](https://www.latentinsure.com/california-homeowners-insurance/cost), [KQED on FAIR Plan hike](https://www.kqed.org/news/12094860/california-fair-plan-announces-29-1-rate-hike-for-homeowners-this-fall), [insurance.com $1M home](https://www.insurance.com/average-home-insurance-rates/how-much-is-homeowners-insurance-on-a-million-dollar-home/) |
| Earthquake | $0 (optional) | CEA ≈ $3.50 per $1,000 dwelling; ~$5K on a $1.5M rebuild | [CEA premium calculator](https://www.earthquakeauthority.com/california-earthquake-insurance-policies/earthquake-insurance-premium-calculator) |
| Maintenance | 1% of market value | Rule of thumb; older homes 2–4% of *structure* value. Westside lots are 60–70% land, so 1% of price ≈ 2.5–3% of structure. Reasonable for a 1940s–60s house that has been kept up. | [ConsumerAffairs](https://www.consumeraffairs.com/homeowners/home-maintenance-cost-breakdown.html) |
| Buy closing (cash) | 1% | Cash buyers pay title, escrow half, recording: ~0.5–1% | [JVM Lending](https://www.jvmlending.com/blog/what-are-the-average-closing-costs-in-california/) |
| Sell costs | 5.8% custom; per neighborhood computed | 5% commission (LA avg 5.0–5.7% post-NAR) + 0.25% fees + county $1.10/$1K + city: LA $4.50/$1K, Santa Monica $3/$1K (<$5M), Culver City tiered (0.98% at $2.5M), MdR none. LA Measure ULA 4% at ≥$5.4M and 5.5% at ≥$10.9M (Jul 2026 thresholds); SM Measure GS 5.6% at ≥$8M. | [Clever LA commission](https://listwithclever.com/average-real-estate-commission-rate/california/los-angeles/), [LA Office of Finance ULA](https://finance.lacity.gov/faq/measure-ula), [Santa Monica DTT](https://www.santamonica.gov/documentary-transfer-tax-real-property-transfer-tax), [Culver City RPTT](https://www.culvercity.gov/Services/Make-a-Payment/Real-Property-Transfer-Tax) |
| Appreciation | 3%/yr nominal | Case-Shiller LA (Jun 2026 = 445.2): 5.2%/yr since 1987, 5.5% over 25 yrs, 6.1% over 10 yrs, but 2.5% over 20 yrs (from the 2006 peak) and 1.7%/yr since May 2022. 3% is below the long-run average and above the last four years. | [FRED LXXRSA](https://fred.stlouisfed.org/series/LXXRSA) |
| Rent growth | 3%/yr | LA CPI rent of primary residence: 3.4%/yr 1987–2017; another source puts it at 4.9% since 2000. Currently flat to negative YoY (LA city −2.7% Jul 2026). | [FRED CUURA421SEHA](https://fred.stlouisfed.org/series/CUURA421SEHA), [Zillow July 2026 rent report](https://www.zillow.com/research/july-2026-rent-report-36631/) |
| Opportunity cost | Equities 7% pre-tax, taxed at sale; Treasuries 4.5% taxed yearly | Your cash is in broad equity ETFs, so equities is the default baseline. Treasuries preset ≈ current 10-yr less a bit. | — |
| Capital gains | 15% federal to $613,700 MFJ taxable, 20% above; 3.8% NIIT; CA 9.3% | 2026 brackets. With ~$298K ordinary taxable income, ~$316K of gain fits at 15%. $500K MFJ exclusion. | [Tax Foundation 2026](https://taxfoundation.org/data/all/federal/2026-tax-brackets/) |
| Marginal rates | 24% federal, 9.3% CA | $330K MFJ gross | — |
| SALT / mortgage deduction | not yet (Phase 1B) | 2026 SALT cap $40,400, phases down above $505K MAGI; matters for a cash buyer's property tax too | [Taxstra SALT](https://taxstra.com/salt-deduction/) |

## Later phases (setup notes will land here)

- RentCast API key, Gmail read-only OAuth, FRED API key
- Where to drop Redfin CSV exports and Zillow Research / Redfin Data Center files
- Running the weekly job locally before scheduling it
