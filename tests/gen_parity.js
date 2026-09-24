// Generates tests/fixtures/buyrent_parity.json from the JS engine so the Python port can be checked.
// Run: node tests/gen_parity.js
const fs = require('fs');
const BR = require('../docs/calc.js');
const cases = [
  {},
  { price: 2000000, rent: 6000, years: 5 },
  { price: 3200000, rent: 8000, years: 10, appreciation: 0.05 },
  { price: 2500000, rent: 7000, oppRate: 0.045, oppTaxMode: 'annual' },
  { price: 2800000, rent: 9000, propTaxRate: 0.0129, insurance: 15000, hoa: 400, earthquake: 5000 },
  { price: 1500000, rent: 7000, oppTaxMode: 'pretax', sellClose: 0.066 }
];
const out = cases.map(c => ({ input: c, finalDiff: BR.simulate(c).finalDiff, breakevenPrice: BR.breakevenPrice(c), rentEquivalent: BR.rentEquivalent(c) }));
fs.writeFileSync(__dirname + '/fixtures/buyrent_parity.json', JSON.stringify(out, null, 1));
console.log('wrote', out.length, 'cases');
