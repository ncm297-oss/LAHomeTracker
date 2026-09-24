/*
 * Financing extension to the buy-vs-rent engine (Phase 1B). Pure functions;
 * depends on BuyRent (calc.js) for costs, sale proceeds and tax helpers.
 *
 * Model on top of Phase 1:
 *   The owner borrows `loan` and keeps the rest of W0 in a retained-cash
 *   portfolio R that earns `retainedRate` under `retainedTaxMode`. Owner
 *   outflow each year = Phase 1 own costs + P&I - itemized tax benefit; the
 *   differential (outflow - rent) still flows into the renter's portfolio as in
 *   Phase 1. At sale the owner nets home proceeds - loan balance + R after tax.
 *
 *   financeYear = 0: loan taken at purchase (points + closing paid from cash).
 *   financeYear = N: cash purchase, then delayed financing / cash-out refi at
 *   the end of year N at refiRate; proceeds net of refiCost land in R and P&I
 *   starts in year N+1.
 *
 * Tax benefit (itemize on): federal = max(0, min(propTax + stateIncomeTax,
 *   saltCap) + deductibleInterest - fedStd) * fedRate; California =
 *   max(0, propTax + deductibleInterest - caStd) * caRate. Interest is
 *   deductible on the first $750K (federal) / $1M (CA) of average balance.
 *   A 100% cash buyer still gets the property-tax piece.
 */
(function (root) {
  'use strict';
  var BR = root.BuyRent || (typeof require === 'function' ? require('./calc.js') : null);

  var FIN_DEFAULTS = {
    downPct: 0.5,             // custom scenario; the fixed set is in compareScenarios
    term: 30,
    conformingRate: 0.071,
    jumboRate: 0.073,
    conformingLimit: 1249125, // 2026 LA County high-cost limit
    points: 0,                // fraction of loan paid up front
    loanClosing: 0.01,        // lender/title/escrow on the loan, fraction of loan
    financeYear: 0,           // 0 = at purchase; N = refi at end of year N
    refiYear: 2,              // year used by the cash-then-refi scenario
    refiPct: 0.5,             // loan as a share of purchase price for that scenario
    refiRate: 0.073,
    refiCost: 0.015,          // fraction of loan
    income: 330000,
    maxDti: 0.43,
    retainedRate: 0.07,
    retainedTaxMode: 'deferred',
    itemize: 1,               // 1 = itemize, 0 = standard deduction (numbers so the hash stays simple)
    saltCap: 40400,
    stateIncomeTax: 22000,    // CA income tax on ~$330K MFJ, already in the SALT bucket
    fedStd: 32200,
    caStd: 11080,
    fedInterestCap: 750000,
    caInterestCap: 1000000
  };

  function withFinDefaults(input) {
    var f = {};
    for (var k in FIN_DEFAULTS) {
      var v = input ? input[k] : undefined;
      f[k] = typeof v === typeof FIN_DEFAULTS[k] && (typeof v !== 'number' || !isNaN(v)) ? v : FIN_DEFAULTS[k];
    }
    return f;
  }

  function assign(a, b) { var o = {}; for (var k in a) o[k] = a[k]; for (var j in b) o[j] = b[j]; return o; }

  // Monthly payment on a fully amortizing loan.
  function payment(loan, rate, termYears) {
    if (loan <= 0) return 0;
    var r = rate / 12, n = termYears * 12;
    if (r === 0) return loan / n;
    return loan * r / (1 - Math.pow(1 + r, -n));
  }

  // Interest, principal and ending balance for loan-year k (1-based).
  function loanYear(loan, rate, termYears, k) {
    var pmt = payment(loan, rate, termYears), r = rate / 12;
    var bal = loan, interest = 0, principal = 0;
    var start = (k - 1) * 12, end = Math.min(k * 12, termYears * 12);
    // Advance to the start of the year, then accumulate 12 months.
    for (var m = 0; m < end; m++) {
      var i = bal * r, pr = Math.min(pmt - i, bal);
      if (m >= start) { interest += i; principal += pr; }
      bal -= pr;
    }
    return { interest: interest, principal: principal, balance: Math.max(0, bal), payment: pmt, months: end - start };
  }

  function rateFor(f, loan, refi) {
    if (refi) return f.refiRate;
    return loan > f.conformingLimit ? f.jumboRate : f.conformingRate;
  }

  // Largest loan whose P&I keeps housing costs within maxDti of gross income,
  // capped at 80% LTV. Property tax uses the purchase price.
  function dtiMaxLoan(input) {
    var p = BR.withDefaults(input), f = withFinDefaults(input);
    var carry = (p.price * p.propTaxRate + p.insurance + p.earthquake) / 12 + p.hoa;
    var room = f.maxDti * f.income / 12 - carry;
    if (room <= 0) return 0;
    var loan = room / payment(1, f.conformingRate, f.term);
    if (loan > f.conformingLimit) loan = room / payment(1, f.jumboRate, f.term);
    return Math.min(loan, p.price * 0.8);
  }

  function taxBenefit(p, f, propTax, interest, avgBalance) {
    if (!f.itemize) return { fed: 0, ca: 0, total: 0 };
    var fedInt = avgBalance > 0 ? interest * Math.min(1, f.fedInterestCap / avgBalance) : 0;
    var caInt = avgBalance > 0 ? interest * Math.min(1, f.caInterestCap / avgBalance) : 0;
    var fedItem = Math.min(propTax + f.stateIncomeTax, f.saltCap) + fedInt;
    var caItem = propTax + caInt;
    var fed = Math.max(0, fedItem - f.fedStd) * p.fedRate;
    var ca = Math.max(0, caItem - f.caStd) * p.caRate;
    return { fed: fed, ca: ca, total: fed + ca, fedInterest: fedInt, caInterest: caInt };
  }

  // scenario: { downPct, financeYear } overrides on top of input.
  function simulateFinanced(input, scenario) {
    var p = BR.withDefaults(input), f = withFinDefaults(assign(input, scenario || {}));
    var W0 = p.price * (1 + p.buyClose);
    var loan = Math.max(0, p.price * (1 - f.downPct));
    var refi = f.financeYear > 0 && loan > 0;
    var rate = rateFor(f, loan, refi);
    var upfront = refi ? 0 : loan * (f.points + f.loanClosing);
    var cashAtClose = refi ? W0 : W0 - loan + upfront;

    var pr = assign(p, { oppRate: f.retainedRate, oppTaxMode: f.retainedTaxMode });
    var gR = BR.portfolioGrowth(pr);
    var R = W0 - cashAtClose, basisR = R;
    var netR = function (v, b) { return f.retainedTaxMode === 'deferred' ? v - BR.capitalGainsTax(p, v - b) : v; };

    var g = BR.portfolioGrowth(p);
    var value = p.price, assessed = p.price, port = W0, basis = W0;
    var balance = refi ? 0 : loan, loanStartYear = refi ? f.financeYear + 1 : 1;
    var rows = [], cumRent = 0, cumOut = 0;

    for (var y = 1; y <= p.years; y++) {
      var infl = Math.pow(1 + p.expenseInflation, y - 1);
      var rentY = p.rent * 12 * Math.pow(1 + p.rentGrowth, y - 1);
      var propTax = assessed * p.propTaxRate;
      var ins = (p.insurance + p.earthquake) * infl;
      var maint = value * p.maintRate;
      var hoaY = p.hoa * 12 * infl;
      var own = propTax + ins + maint + hoaY;

      var ly = { interest: 0, principal: 0, balance: balance, payment: 0, months: 0 };
      var balStart = balance;
      if (loan > 0 && y >= loanStartYear) {
        ly = loanYear(loan, rate, f.term, y - loanStartYear + 1);
        balance = ly.balance;
      }
      var pi = ly.payment * ly.months;
      var tb = taxBenefit(p, f, propTax, ly.interest, (balStart + balance) / 2);
      var outflow = own + pi - tb.total;
      var contribution = outflow - rentY;

      var portStart = port;
      port = port * (1 + g) + contribution;
      basis += contribution;

      var RStart = R;
      R = R * (1 + gR);
      var refiProceeds = 0;
      if (refi && y === f.financeYear) {
        refiProceeds = loan * (1 - f.refiCost - f.points);
        R += refiProceeds; basisR += refiProceeds;
        balance = loan;
      }

      cumRent += rentY; cumOut += outflow;
      var valueEnd = value * (1 + p.appreciation);
      var sale = BR.saleNet(p, valueEnd);
      var RNet = netR(R, basisR);
      var renterNet = p.oppTaxMode === 'deferred' ? port - BR.capitalGainsTax(p, port - basis) : port;
      var ownerNet = sale.net - balance + RNet;

      rows.push({
        year: y, rent: rentY, propTax: propTax, insurance: ins, maintenance: maint, hoa: hoaY, own: own,
        assessed: assessed, valueStart: value, valueEnd: valueEnd,
        interest: ly.interest, principal: ly.principal, pi: pi, balanceStart: balStart, balanceEnd: balance,
        taxBenefit: tb, outflow: outflow, contribution: contribution,
        portStart: portStart, portEnd: port, portGrowth: portStart * g, renterNet: renterNet,
        RStart: RStart, REnd: R, RGrowth: RStart * gR, refiProceeds: refiProceeds, RNet: RNet,
        sale: sale, ownerNet: ownerNet, diff: ownerNet - renterNet,
        cumRent: cumRent, cumOutflow: cumOut
      });

      value = valueEnd;
      assessed = Math.min(assessed * (1 + p.prop13Cap), value);
    }

    var last = rows[rows.length - 1];
    var first = rows[0];
    return {
      params: p, fin: f, loan: loan, rate: rate, refi: refi, jumbo: !refi && loan > f.conformingLimit,
      cashAtClose: cashAtClose, retained: W0 - cashAtClose, upfront: upfront,
      rows: rows, finalDiff: last.diff, ownerNet: last.ownerNet, renterNet: last.renterNet,
      monthlyYear1: (first.own + first.pi) / 12,
      monthlyAfterTaxYear1: first.outflow / 12,
      payment: refi ? payment(loan, rate, f.term) : first.payment || payment(loan, rate, f.term)
    };
  }

  // After-tax cost of borrowing vs after-tax return on the retained cash.
  function crossover(input, loanSize) {
    var p = BR.withDefaults(input), f = withFinDefaults(input);
    var loan = loanSize || p.price * (1 - f.downPct);
    var rate = rateFor(f, loan, false);
    var dFed = f.itemize ? Math.min(1, f.fedInterestCap / Math.max(loan, 1)) * p.fedRate : 0;
    var dCa = f.itemize ? Math.min(1, f.caInterestCap / Math.max(loan, 1)) * p.caRate : 0;
    var shield = dFed + dCa;
    var afterTaxLoan = rate * (1 - shield);
    var afterTaxRetained;
    if (f.retainedTaxMode === 'annual') afterTaxRetained = f.retainedRate * (1 - p.fedRate);
    else if (f.retainedTaxMode === 'deferred') {
      var n = p.years, cg = BR.cgRate(p), gross = Math.pow(1 + f.retainedRate, n);
      afterTaxRetained = Math.pow(gross - (gross - 1) * cg, 1 / n) - 1;
    } else afterTaxRetained = f.retainedRate;
    return {
      rate: rate, shield: shield, afterTaxLoan: afterTaxLoan, afterTaxRetained: afterTaxRetained,
      crossoverRate: afterTaxRetained / (1 - shield),   // pre-tax mortgage rate at which leverage ties
      leverageWins: afterTaxLoan < afterTaxRetained
    };
  }

  function fmtM(n) { n = Math.abs(n); return n >= 1e6 ? '$' + (n / 1e6).toFixed(2) + 'M' : '$' + Math.round(n / 1e3) + 'K'; }

  function compareScenarios(input) {
    var p = BR.withDefaults(input), f = withFinDefaults(input);
    var dtiMax = dtiMaxLoan(input);
    var defs = [
      { key: 'cash', name: '100% cash', downPct: 1, financeYear: 0 },
      { key: 'down75', name: '75% down', downPct: 0.75, financeYear: 0 },
      { key: 'down50', name: '50% down', downPct: 0.5, financeYear: 0 },
      { key: 'dti', name: 'DTI-max loan', downPct: 1 - dtiMax / p.price, financeYear: 0 },
      { key: 'refi', name: 'Cash, refi yr ' + f.refiYear, downPct: 1 - f.refiPct, financeYear: f.refiYear }
    ];
    var out = defs.map(function (d) {
      var sim = simulateFinanced(input, { downPct: d.downPct, financeYear: d.financeYear });
      return { key: d.key, name: d.name, downPct: d.downPct, financeYear: d.financeYear, sim: sim,
        overDti: sim.loan > dtiMax + 1 };
    });
    var cash = out[0].sim.finalDiff;
    out.forEach(function (s) {
      s.vsCash = s.sim.finalDiff - cash;
      var liq = s.sim.retained;
      if (s.key === 'cash') s.verdict = 'Baseline: all ' + fmtM(s.sim.cashAtClose) + ' goes into the house.';
      else if (s.overDti) s.verdict = 'Likely not approvable: ' + fmtM(s.sim.loan) + ' loan exceeds the ' + fmtM(dtiMax) + ' DTI limit.';
      else if (s.vsCash >= 0) s.verdict = 'Cheaper than 100% cash by ' + fmtM(s.vsCash) + ' over ' + p.years + ' yrs, and keeps ' + fmtM(liq) + ' liquid.';
      else s.verdict = 'Costs ' + fmtM(-s.vsCash) + ' more than 100% cash over ' + p.years + ' yrs; buys ' + fmtM(liq) + ' of liquidity' + (s.financeYear ? ' from year ' + s.financeYear : '') + '.';
    });
    return { scenarios: out, dtiMaxLoan: dtiMax, crossover: crossover(input, p.price * 0.5) };
  }

  var api = {
    FIN_DEFAULTS: FIN_DEFAULTS, withFinDefaults: withFinDefaults, payment: payment, loanYear: loanYear,
    dtiMaxLoan: dtiMaxLoan, taxBenefit: taxBenefit, simulateFinanced: simulateFinanced,
    crossover: crossover, compareScenarios: compareScenarios, rateFor: rateFor
  };
  root.Mortgage = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : this);
