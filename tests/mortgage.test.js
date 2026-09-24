// Run: node --test tests/mortgage.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const BR = require('../docs/calc.js');
const M = require('../docs/mortgage.js');

const near = (a, b, tol) => assert.ok(Math.abs(a - b) <= tol, `${a} not within ${tol} of ${b}`);

test('payment matches the standard amortization formula', () => {
  near(M.payment(1000000, 0.07, 30), 6653.02, 0.01);
  near(M.payment(1000000, 0.06, 15), 8438.57, 0.01);
  near(M.payment(120000, 0, 10), 1000, 1e-9);
});

test('loan years add up to the loan and interest declines', () => {
  const loan = 1250000, rate = 0.073, term = 30;
  let principal = 0, prev = Infinity;
  for (let k = 1; k <= term; k++) {
    const y = M.loanYear(loan, rate, term, k);
    principal += y.principal;
    assert.ok(y.interest < prev); prev = y.interest;
    if (k === term) near(y.balance, 0, 1);
  }
  near(principal, loan, 1);
  near(M.loanYear(loan, rate, term, 1).interest + M.loanYear(loan, rate, term, 1).principal, M.payment(loan, rate, term) * 12, 0.01);
});

test('100% cash with standard deduction reproduces Phase 1 to the dollar', () => {
  const base = { price: 2500000, rent: 7000, years: 7 };
  const p1 = BR.simulate(base);
  const s = M.simulateFinanced({ ...base, itemize: 0 }, { downPct: 1, financeYear: 0 });
  near(s.finalDiff, p1.finalDiff, 1e-6);
  near(s.rows[3].ownerNet, p1.rows[3].ownerNet, 1e-6);
  assert.equal(s.loan, 0);
});

test('itemizing gives the cash buyer the property-tax deduction above the standard deduction', () => {
  const p = BR.withDefaults({}), f = M.withFinDefaults({});
  const tb = M.taxBenefit(p, f, 30000, 0, 0);
  near(tb.fed, (40400 - 32200) * 0.24, 1e-6);   // SALT capped at 40,400
  near(tb.ca, (30000 - 11080) * 0.093, 1e-6);
  assert.equal(M.taxBenefit(p, { ...f, itemize: 0 }, 30000, 0, 0).total, 0);
});

test('interest deduction is capped at $750K federal / $1M CA of balance', () => {
  const p = BR.withDefaults({}), f = M.withFinDefaults({});
  const tb = M.taxBenefit(p, f, 0, 100000, 1500000);
  near(tb.fedInterest, 50000, 1e-6);
  near(tb.caInterest, 100000 * (1 / 1.5), 1e-6);
});

test('jumbo flag and rate selection follow the conforming limit', () => {
  const f = M.withFinDefaults({});
  assert.equal(M.rateFor(f, 1249125, false), 0.071);
  assert.equal(M.rateFor(f, 1249126, false), 0.073);
  const s = M.simulateFinanced({ price: 2500000 }, { downPct: 0.5, financeYear: 0 });
  assert.equal(s.jumbo, true);
  near(s.loan, 1250000, 1e-6);
});

test('DTI max loan rises with income and is capped at 80% LTV', () => {
  const a = M.dtiMaxLoan({ price: 2500000, income: 330000 });
  const b = M.dtiMaxLoan({ price: 2500000, income: 600000 });
  assert.ok(b > a && a > 0);
  assert.ok(M.dtiMaxLoan({ price: 1000000, income: 5000000 }) <= 800000 + 1e-6);
  // sanity: at $330K the room is 11,825/mo less ~3,333 carry => ~$1.2M at 7.1-7.3%
  assert.ok(a > 1000000 && a < 1400000, `dti max ${a}`);
});

test('financed scenario keeps cash: retained = loan - upfront costs', () => {
  const s = M.simulateFinanced({ price: 2000000 }, { downPct: 0.5, financeYear: 0 });
  near(s.retained, 1000000 - 1000000 * 0.01, 1e-6);
  near(s.rows[0].RStart, s.retained, 1e-6);
});

test('delayed financing injects proceeds in year N and starts P&I the year after', () => {
  const s = M.simulateFinanced({ price: 2000000, years: 5 }, { downPct: 0.5, financeYear: 2 });
  assert.equal(s.rows[0].pi, 0);
  assert.equal(s.rows[1].pi, 0);
  near(s.rows[1].refiProceeds, 1000000 * (1 - 0.015), 1e-6);
  assert.equal(s.rows[1].balanceEnd, 1000000);
  assert.ok(s.rows[2].pi > 0);
  assert.equal(s.retained, 0);
});

test('crossover: leverage wins only when after-tax loan cost is below after-tax retained return', () => {
  const hi = M.crossover({ retainedRate: 0.12, retainedTaxMode: 'pretax' });
  const lo = M.crossover({ retainedRate: 0.03, retainedTaxMode: 'pretax' });
  assert.equal(hi.leverageWins, true);
  assert.equal(lo.leverageWins, false);
  near(lo.crossoverRate * (1 - lo.shield), 0.03, 1e-9);
});

test('compareScenarios returns five scenarios with cash as baseline', () => {
  const c = M.compareScenarios({ price: 2500000 });
  assert.equal(c.scenarios.length, 5);
  assert.equal(c.scenarios[0].vsCash, 0);
  assert.ok(c.scenarios.every(s => typeof s.verdict === 'string' && s.verdict.length > 10));
  // 75% down on $2.5M is a $625K loan, under DTI; 50% down is $1.25M, near the limit
  assert.equal(c.scenarios[1].overDti, false);
});
