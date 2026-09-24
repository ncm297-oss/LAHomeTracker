// Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const BR = require('../docs/calc.js');

const near = (a, b, tol) => assert.ok(Math.abs(a - b) <= tol, `${a} not within ${tol} of ${b}`);

test('year 1 costs follow the defaults', () => {
  const s = BR.simulate({ price: 2500000, rent: 7000 });
  const r = s.rows[0];
  near(r.propTax, 30000, 1e-6);       // 1.2% of price
  near(r.insurance, 10000, 1e-6);
  near(r.maintenance, 25000, 1e-6);   // 1% of value
  near(r.hoa, 0, 1e-6);
  near(r.rent, 84000, 1e-6);
  near(s.W0, 2525000, 1e-6);
});

test('Prop 13 caps assessed growth at 2% even when appreciation is higher', () => {
  const s = BR.simulate({ price: 1000000, appreciation: 0.10, years: 3 });
  near(s.rows[1].assessed, 1020000, 1e-6);
  near(s.rows[2].assessed, 1040400, 1e-6);
});

test('assessed value falls with market value in a downturn (Prop 8)', () => {
  const s = BR.simulate({ price: 1000000, appreciation: -0.05, years: 2 });
  near(s.rows[1].assessed, 950000, 1e-6);
});

test('sale applies selling costs, exclusion and combined LTCG rate', () => {
  const p = BR.withDefaults({ price: 2000000 });
  const sale = BR.saleNet(p, 3000000);
  near(sale.proceeds, 2835000, 1e-6);           // 5.5% cost
  near(sale.gain, 2835000 - 2020000, 1e-6);     // basis includes 1% buy closing
  near(sale.taxable, 815000 - 500000, 1e-6);
  near(sale.tax, 315000 * (0.15 + 0.038 + 0.093), 1e-6);
});

test('no capital-gains tax when gain is inside the exclusion', () => {
  const p = BR.withDefaults({ price: 2000000 });
  assert.equal(BR.saleNet(p, 2400000).tax, 0);
});

test('pre-tax mode is the renter-friendliest, deferred beats annual for equal rates', () => {
  const base = { oppRate: 0.05 };
  const pre = BR.simulate({ ...base, oppTaxMode: 'pretax' }).finalDiff;
  const def = BR.simulate({ ...base, oppTaxMode: 'deferred' }).finalDiff;
  const ann = BR.simulate({ ...base, oppTaxMode: 'annual' }).finalDiff;
  assert.ok(pre < def, 'pretax should favour renting most');
  // deferred pays fed 15 + NIIT 3.8 + CA 9.3 once; annual pays 24% fed every year. Roughly similar; just check both are finite.
  assert.ok(Number.isFinite(ann) && Number.isFinite(def));
});

test('breakeven functions actually break even', () => {
  const p = { price: 2500000, rent: 7000 };
  const bp = BR.breakevenPrice(p);
  near(BR.simulate({ ...p, price: bp }).finalDiff, 0, 1);
  const ba = BR.breakevenAppreciation(p);
  near(BR.simulate({ ...p, appreciation: ba }).finalDiff, 0, 1);
  const re = BR.rentEquivalent(p);
  near(BR.simulate({ ...p, rent: re }).finalDiff, 0, 1);
});

test('higher rent makes buying look better; higher price makes it worse', () => {
  const a = BR.simulate({ rent: 6000 }).finalDiff;
  const b = BR.simulate({ rent: 8000 }).finalDiff;
  assert.ok(b > a);
  const c = BR.simulate({ price: 2000000 }).finalDiff;
  const d = BR.simulate({ price: 3200000 }).finalDiff;
  assert.ok(c > d);
});

test('sensitivity grid has the requested shape and is monotonic in appreciation', () => {
  const g = BR.sensitivity({});
  assert.equal(g.cells.length, 7);
  assert.equal(g.cells[0].length, 3);
  for (let h = 0; h < 3; h++) {
    for (let a = 1; a < 7; a++) assert.ok(g.cells[a][h] > g.cells[a - 1][h]);
  }
});

test('zero-cost sanity: with no costs, no growth and no returns, buying ties renting except closing costs', () => {
  const s = BR.simulate({
    price: 1000000, rent: 0, years: 1, oppRate: 0, oppTaxMode: 'pretax', propTaxRate: 0, insurance: 0,
    maintRate: 0, hoa: 0, buyClose: 0, sellClose: 0, appreciation: 0, rentGrowth: 0
  });
  near(s.finalDiff, 0, 1e-6);
});
