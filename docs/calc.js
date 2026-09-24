/*
 * Buy-vs-rent engine. Pure functions, no DOM. Loaded by the page and by
 * tests/calc.test.js (Node), so it must not touch `window` or `document`.
 *
 * Model (one period = one year):
 *   Both households start with the same cash W0 = price * (1 + buyClose).
 *   Owner puts W0 into the house. Renter puts W0 into a portfolio earning the
 *   opportunity rate. Each year, whoever pays less invests the difference into
 *   the renter's portfolio (a negative contribution means the owner was cheaper
 *   that year and the renter draws down). At the end of any year the owner
 *   could sell: proceeds net of selling costs and capital-gains tax after the
 *   $500K exclusion. Wealth difference = owner net - renter net (positive means
 *   buying won).
 *
 * Opportunity-cost tax treatment (oppTaxMode):
 *   'pretax'   - returns untaxed (upper bound on the renter's outcome)
 *   'annual'   - gains taxed every year at the federal marginal rate only
 *                (Treasury interest: CA-exempt)
 *   'deferred' - gains compound untaxed and are taxed once at the end at the
 *                long-term capital-gains rate (equities held for the hold)
 *
 * Capital-gains tax on a realised gain = federal LTCG (15% up to the top of the
 * 15% bracket after ordinary income, 20% above) + 3.8% NIIT + CA marginal rate.
 *
 * Selling cost: either a flat fraction (sellClose) or, when the page passes a
 * neighborhood, sellCostFn(salePrice) from assumptions.js, which knows about
 * city transfer taxes and the LA Measure ULA / Santa Monica GS thresholds.
 *
 * Simplifications, on purpose:
 *   - Contributions land at year end; growth applies to the opening balance.
 *   - Deferred mode adjusts basis by each contribution, including negative
 *     ones, instead of realising gains on withdrawals.
 *   - No mortgage. Financing is Phase 1B.
 */
(function (root) {
  'use strict';

  var DEFAULTS = {
    price: 2500000,
    rent: 7000,              // monthly, comparable
    years: 7,
    neighborhood: '',        // key from assumptions.js, or '' for custom
    oppRate: 0.07,           // pre-tax return on the cash if not spent on the house
    oppTaxMode: 'deferred',  // see header
    fedRate: 0.24,
    caRate: 0.093,
    ltcgFedRate: 0.15,
    ltcgHighRate: 0.20,
    ltcgTop: 613700,         // 2026 MFJ: taxable income above this pays 20% on LTCG
    ordinaryTaxable: 298000, // ~$330K gross less the 2026 MFJ standard deduction
    niitRate: 0.038,
    propTaxRate: 0.012,
    prop13Cap: 0.02,
    insurance: 10000,        // annual $, fire/standard policy
    earthquake: 0,           // annual $, CEA or private EQ policy
    maintRate: 0.01,         // of current market value, per year
    hoa: 0,                  // monthly $
    buyClose: 0.01,
    sellClose: 0.058,        // 5% commission + 0.25% fees + LA City 0.45% + county 0.11%
    rentGrowth: 0.03,
    appreciation: 0.03,
    expenseInflation: 0.03,  // applied to insurance, earthquake and HOA
    cgExclusion: 500000
  };

  var PRESETS = {
    equities: { oppRate: 0.07, oppTaxMode: 'deferred' },
    treasuries: { oppRate: 0.045, oppTaxMode: 'annual' }
  };

  function withDefaults(p) {
    var out = {};
    for (var k in DEFAULTS) {
      var v = p ? p[k] : undefined;
      var ok = typeof v === typeof DEFAULTS[k] && (typeof v !== 'number' || !isNaN(v));
      out[k] = ok ? v : DEFAULTS[k];
    }
    if (p && typeof p.sellCostFn === 'function') out.sellCostFn = p.sellCostFn;
    return out;
  }

  // Tax on a realised long-term gain, stacked on top of ordinary income.
  function capitalGainsTax(p, gain) {
    if (gain <= 0) return 0;
    var room = Math.max(0, p.ltcgTop - p.ordinaryTaxable);
    var low = Math.min(gain, room), high = gain - low;
    return low * p.ltcgFedRate + high * p.ltcgHighRate + gain * (p.niitRate + p.caRate);
  }

  // Marginal combined rate on the first dollar of gain; used for display only.
  function cgRate(p) {
    return p.ltcgFedRate + p.niitRate + p.caRate;
  }

  // Annual after-tax growth of the portfolio for the chosen tax mode.
  function portfolioGrowth(p) {
    if (p.oppTaxMode === 'annual') return p.oppRate * (1 - p.fedRate);
    return p.oppRate; // pretax or deferred: taxed (if at all) at the end
  }

  function portfolioNet(p, value, basis) {
    if (p.oppTaxMode !== 'deferred') return value;
    return value - capitalGainsTax(p, value - basis);
  }

  function sellingCost(p, value) {
    return p.sellCostFn ? p.sellCostFn(value) : value * p.sellClose;
  }

  function saleNet(p, value) {
    var cost = sellingCost(p, value);
    var proceeds = value - cost;
    var costBasis = p.price * (1 + p.buyClose);
    var gain = proceeds - costBasis;
    var taxable = Math.max(0, gain - p.cgExclusion);
    var tax = capitalGainsTax(p, taxable);
    return { sellCost: cost, proceeds: proceeds, gain: gain, taxable: taxable, tax: tax, net: proceeds - tax };
  }

  function simulate(input) {
    var p = withDefaults(input);
    var W0 = p.price * (1 + p.buyClose);
    var value = p.price;          // market value at start of year
    var assessed = p.price;       // Prop 13 assessed value for the year
    var port = W0, basis = W0;
    var g = portfolioGrowth(p);
    var rows = [];
    var cumRent = 0, cumOwn = 0;

    for (var y = 1; y <= p.years; y++) {
      var infl = Math.pow(1 + p.expenseInflation, y - 1);
      var rentY = p.rent * 12 * Math.pow(1 + p.rentGrowth, y - 1);
      var propTax = assessed * p.propTaxRate;
      var ins = (p.insurance + p.earthquake) * infl;
      var maint = value * p.maintRate;
      var hoaY = p.hoa * 12 * infl;
      var own = propTax + ins + maint + hoaY;
      var contribution = own - rentY;

      var portStart = port;
      port = port * (1 + g) + contribution;
      basis += contribution;
      cumRent += rentY;
      cumOwn += own;

      var valueEnd = value * (1 + p.appreciation);
      var sale = saleNet(p, valueEnd);
      var renterNet = portfolioNet(p, port, basis);
      var diff = sale.net - renterNet;

      rows.push({
        year: y,
        rent: rentY,
        propTax: propTax, insurance: ins, maintenance: maint, hoa: hoaY,
        own: own,
        assessed: assessed,
        contribution: contribution,
        valueStart: value, valueEnd: valueEnd,
        portStart: portStart, portEnd: port, portBasis: basis, portGrowth: portStart * g,
        renterNet: renterNet,
        sale: sale,
        ownerNet: sale.net,
        diff: diff,
        cumRent: cumRent, cumOwn: cumOwn
      });

      // Roll forward. Prop 13: assessed grows at most the cap, never above market (Prop 8).
      value = valueEnd;
      assessed = Math.min(assessed * (1 + p.prop13Cap), value);
    }

    var last = rows[rows.length - 1];
    return {
      params: p,
      W0: W0,
      rows: rows,
      finalDiff: last.diff,
      ownerNet: last.ownerNet,
      renterNet: last.renterNet,
      cumRent: cumRent,
      cumOwn: cumOwn,
      year1Monthly: rows[0].own / 12,
      // annual opportunity cost of the capital in year 1, after tax where taxed annually
      year1OppCost: W0 * g
    };
  }

  function finalDiff(input) { return simulate(input).finalDiff; }

  // Find x in [lo, hi] where f(x) = 0. Scans for a sign change first so
  // non-monotonic curves still resolve; returns null if none found.
  function solve(f, lo, hi, steps) {
    steps = steps || 60;
    var prevX = lo, prevY = f(lo);
    for (var i = 1; i <= steps; i++) {
      var x = lo + (hi - lo) * i / steps;
      var yv = f(x);
      if (prevY === 0) return prevX;
      if ((prevY < 0) !== (yv < 0)) {
        var a = prevX, b = x, fa = prevY;
        for (var j = 0; j < 60; j++) {
          var m = (a + b) / 2, fm = f(m);
          if ((fm < 0) === (fa < 0)) { a = m; fa = fm; } else { b = m; }
        }
        return (a + b) / 2;
      }
      prevX = x; prevY = yv;
    }
    return null;
  }

  function assign(base, extra) {
    var out = {};
    for (var k in base) out[k] = base[k];
    for (var k2 in extra) out[k2] = extra[k2];
    return out;
  }

  // Price at which buying ties renting, holding rent and everything else fixed.
  function breakevenPrice(input) {
    var p = withDefaults(input);
    return solve(function (x) { return finalDiff(assign(p, { price: x })); }, 200000, 20000000, 200);
  }

  // Annual appreciation at which buying ties renting.
  function breakevenAppreciation(input) {
    var p = withDefaults(input);
    return solve(function (x) { return finalDiff(assign(p, { appreciation: x })); }, -0.10, 0.25, 140);
  }

  // Monthly rent at which renting ties buying at the entered price.
  function rentEquivalent(input) {
    var p = withDefaults(input);
    return solve(function (x) { return finalDiff(assign(p, { rent: x })); }, 500, 60000, 240);
  }

  // Year the wealth difference first turns positive, if any.
  function breakevenYear(sim) {
    for (var i = 0; i < sim.rows.length; i++) if (sim.rows[i].diff >= 0) return sim.rows[i].year;
    return null;
  }

  function sensitivity(input, apprs, holds) {
    var p = withDefaults(input);
    apprs = apprs || [0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06];
    holds = holds || [5, 7, 10];
    return {
      apprs: apprs, holds: holds,
      cells: apprs.map(function (a) {
        return holds.map(function (h) { return finalDiff(assign(p, { appreciation: a, years: h })); });
      })
    };
  }

  var api = {
    DEFAULTS: DEFAULTS, PRESETS: PRESETS,
    withDefaults: withDefaults, simulate: simulate, saleNet: saleNet, cgRate: cgRate,
    capitalGainsTax: capitalGainsTax, sellingCost: sellingCost,
    portfolioGrowth: portfolioGrowth, breakevenPrice: breakevenPrice,
    breakevenAppreciation: breakevenAppreciation, rentEquivalent: rentEquivalent,
    breakevenYear: breakevenYear, sensitivity: sensitivity, solve: solve
  };

  root.BuyRent = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : this);
