/*
 * Location-specific cost assumptions. One entry per target neighborhood.
 * Everything here is data, not logic, so it can be refreshed from the tracker
 * later (Phase 2 stores the same fields per listing) without touching calc.js.
 *
 * Sources and dates are in README.md under "Assumptions". Verified 2026-09-24.
 *
 * propTaxRate: total secured rate for a NEW purchase = 1% general levy + voted
 *   debt. Built from the LA County Auditor-Controller 2025-26 Taxpayers' Guide
 *   school/college bond rates plus ~0.02-0.03% for city bonds and Metropolitan
 *   Water District. Ignore "effective rate" figures on Ownwell/Zillow-type
 *   sites: those average long-held Prop 13 owners and understate a new buyer.
 * transfer: seller-side documentary transfer tax as a function of sale price.
 *   LA County charges $1.10 per $1,000 everywhere; cities add their own.
 * fireRisk: 'low' = flats away from brush; 'mixed' = parts of the area are in
 *   Very High Fire Hazard Severity Zones (check the specific parcel);
 *   'high' = mostly VHFHSZ.
 * insuranceBand: rough annual premium for a standard/HNW policy on a $1.2-1.8M
 *   rebuild (typical for a $2-3M Westside house), excluding earthquake.
 */
(function (root) {
  'use strict';

  var COUNTY_TRANSFER = 0.0011; // $1.10 per $1,000, all of LA County

  // City of Los Angeles: $4.50/$1,000 base. Measure ULA adds 4% of the FULL price
  // at >= $5.4M and 5.5% at >= $10.9M (thresholds indexed each July 1; these are
  // the July 2026 - June 2027 values).
  function laCity(price) {
    var t = COUNTY_TRANSFER + 0.0045;
    if (price >= 10900000) t += 0.055;
    else if (price >= 5400000) t += 0.04;
    return t * price;
  }

  // Santa Monica: $3/$1,000 under $5M, $6/$1,000 at $5M+, plus Measure GS 5.6% at $8M+.
  function santaMonica(price) {
    var t = COUNTY_TRANSFER + (price >= 5000000 ? 0.006 : 0.003);
    if (price >= 8000000) t += 0.056;
    return t * price;
  }

  // Culver City: tiered marginal rates (0.45% to $1.5M; 1.5% $1.5-3M; 3% $3-10M; 4% above).
  function culverCity(price) {
    var tax;
    if (price <= 1500000) tax = price * 0.0045;
    else if (price <= 3000000) tax = 6750 + (price - 1500000) * 0.015;
    else if (price <= 10000000) tax = 29250 + (price - 3000000) * 0.03;
    else tax = 239250 + (price - 10000000) * 0.04;
    return tax + price * COUNTY_TRANSFER;
  }

  // Unincorporated LA County (Marina del Rey): county rate only.
  function countyOnly(price) { return price * COUNTY_TRANSFER; }

  // LAUSD 0.119605 + LA Community College 0.048543 + city/MWD ≈ 0.025
  var LA_CITY_TAX = 0.0119;
  // Santa Monica-Malibu USD 0.191203 + Santa Monica College 0.078622 + city/MWD ≈ 0.02
  var SANTA_MONICA_TAX = 0.0129;
  // Culver City USD 0.083234 + LACC 0.048543 + city/MWD ≈ 0.02
  var CULVER_TAX = 0.0115;
  // Unincorporated, LAUSD + LACC + MWD
  var MDR_TAX = 0.0117;

  var NEIGHBORHOODS = [
    { key: 'santa_monica', name: 'Santa Monica', city: 'Santa Monica', propTaxRate: SANTA_MONICA_TAX, transfer: santaMonica, fireRisk: 'low', insuranceBand: [6000, 12000] },
    { key: 'ocean_park', name: 'Ocean Park', city: 'Santa Monica', propTaxRate: SANTA_MONICA_TAX, transfer: santaMonica, fireRisk: 'low', insuranceBand: [6000, 12000] },
    { key: 'marina_del_rey', name: 'Marina del Rey', city: 'Unincorporated LA County', propTaxRate: MDR_TAX, transfer: countyOnly, fireRisk: 'low', insuranceBand: [6000, 12000], note: 'Mostly condos and HOAs; leasehold land on the marina side.' },
    { key: 'sawtelle', name: 'Sawtelle', city: 'Los Angeles', propTaxRate: LA_CITY_TAX, transfer: laCity, fireRisk: 'low', insuranceBand: [6000, 12000] },
    { key: 'westwood', name: 'Westwood', city: 'Los Angeles', propTaxRate: LA_CITY_TAX, transfer: laCity, fireRisk: 'low', insuranceBand: [6000, 12000] },
    { key: 'west_la', name: 'West LA', city: 'Los Angeles', propTaxRate: LA_CITY_TAX, transfer: laCity, fireRisk: 'low', insuranceBand: [6000, 12000] },
    { key: 'brentwood', name: 'Brentwood', city: 'Los Angeles', propTaxRate: LA_CITY_TAX, transfer: laCity, fireRisk: 'mixed', insuranceBand: [8000, 25000], note: 'North of Sunset and the canyons are largely Very High Fire Hazard Severity Zone; expect FAIR Plan + DIC or an HNW carrier. Flats south of Sunset are standard.' },
    { key: 'culver_city', name: 'Culver City', city: 'Culver City', propTaxRate: CULVER_TAX, transfer: culverCity, fireRisk: 'low', insuranceBand: [6000, 12000], note: 'Highest transfer tax of the nine: ~0.98% on a $2.5M sale.' },
    { key: 'mar_vista', name: 'Mar Vista', city: 'Los Angeles', propTaxRate: LA_CITY_TAX, transfer: laCity, fireRisk: 'low', insuranceBand: [6000, 12000] }
  ];

  // Non-transfer seller costs: commission + escrow/title/misc.
  var SELL_COMMISSION = 0.05;   // LA average 5.0-5.7% post-NAR-settlement; luxury often 4-5%
  var SELL_FEES = 0.0025;       // escrow half, owner's title policy, NHD, misc on a $2-3M sale

  // Earthquake (CEA): roughly $3.50 per $1,000 of dwelling coverage for a
  // wood-frame house on slab; older raised-foundation homes cost more.
  var EQ_RATE_PER_DOLLAR = 0.0035;

  function byKey(k) {
    for (var i = 0; i < NEIGHBORHOODS.length; i++) if (NEIGHBORHOODS[i].key === k) return NEIGHBORHOODS[i];
    return null;
  }

  // Total selling cost in dollars for a given sale price in a neighborhood.
  function sellCost(n, price) {
    return price * (SELL_COMMISSION + SELL_FEES) + n.transfer(price);
  }

  var api = {
    NEIGHBORHOODS: NEIGHBORHOODS, byKey: byKey, sellCost: sellCost,
    SELL_COMMISSION: SELL_COMMISSION, SELL_FEES: SELL_FEES, COUNTY_TRANSFER: COUNTY_TRANSFER,
    EQ_RATE_PER_DOLLAR: EQ_RATE_PER_DOLLAR
  };
  root.Assumptions = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : this);
