/* Financing page glue. State = Phase 1 params + financing params, all in the URL hash
 * so a link from index.html carries the deal over and back. */
(function () {
  'use strict';
  var BR = window.BuyRent, M = window.Mortgage, AS = window.Assumptions;
  var $ = function (s) { return document.querySelector(s); };
  var ALL = {};
  for (var k in BR.DEFAULTS) ALL[k] = BR.DEFAULTS[k];
  for (var j in M.FIN_DEFAULTS) ALL[j] = M.FIN_DEFAULTS[j];
  var inputs = Array.prototype.slice.call(document.querySelectorAll('#inputs [data-k]'));
  var state = {};   // full param set, including keys with no input on this page (neighborhood, hoa, ...)

  function money(n, opts) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var sign = n < 0 ? '−' : ''; n = Math.abs(n);
    if (opts && opts.short) {
      if (n >= 1e6) return sign + '$' + (n / 1e6).toFixed(2) + 'M';
      if (n >= 1e3) return sign + '$' + Math.round(n / 1e3) + 'K';
    }
    return sign + '$' + Math.round(n).toLocaleString('en-US');
  }
  function pct(x, d) { return x === null || isNaN(x) ? '—' : (x * 100).toFixed(d === undefined ? 1 : d) + '%'; }

  function fromHash() {
    var p = {};
    location.hash.replace(/^#/, '').split('&').forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf('='), k = kv.slice(0, i), v = decodeURIComponent(kv.slice(i + 1));
      if (!(k in ALL)) return;
      p[k] = typeof ALL[k] === 'string' ? v : parseFloat(v);
    });
    var out = {};
    for (var k2 in ALL) out[k2] = (k2 in p) && (typeof p[k2] !== 'number' || !isNaN(p[k2])) ? p[k2] : ALL[k2];
    return out;
  }
  function toHash(p) {
    var parts = [];
    for (var k in ALL) {
      if (k === 'sellClose' && p.neighborhood) continue;
      if (p[k] !== ALL[k]) parts.push(k + '=' + encodeURIComponent(p[k]));
    }
    return parts.length ? '#' + parts.join('&') : '';
  }

  function readInputs() {
    inputs.forEach(function (el) {
      var k = el.dataset.k, t = el.dataset.t;
      if (el.tagName === 'SELECT') { state[k] = t === 'int' ? parseInt(el.value, 10) : el.value; return; }
      var v = parseFloat(String(el.value).replace(/[^0-9.\-]/g, ''));
      if (isNaN(v)) return;
      state[k] = t === 'pct' ? Math.round(v * 10000) / 1e6 : t === 'int' ? Math.round(v) : v;
    });
  }
  function writeInputs() {
    inputs.forEach(function (el) {
      var k = el.dataset.k, t = el.dataset.t, v = state[k];
      if (el.tagName === 'SELECT') { el.value = String(v); return; }
      if (t === 'pct') el.value = +(v * 100).toFixed(3);
      else if (t === 'money') el.value = Math.round(v).toLocaleString('en-US');
      else el.value = v;
    });
    $('#down-label').textContent = pct(state.downPct, 0);
  }

  function withSellFn(p) {
    var n = AS.byKey(p.neighborhood);
    var out = {}; for (var k in p) out[k] = p[k];
    if (n) out.sellCostFn = function (price) { return AS.sellCost(n, price); };
    return out;
  }

  // ---------- rendering ----------
  var current = null; // { scenarios, dtiMaxLoan, custom }

  function card(s, cash) {
    var sim = s.sim, d = sim.finalDiff;
    var cls = 'scen' + (s.overDti ? ' grey' : '') + (s.key === 'cash' ? ' base' : '');
    var monthlyCls = sim.monthlyYear1 <= 8000 ? 'pos' : 'neg';
    return '<div class="' + cls + '">' +
      '<div class="name">' + s.name + (sim.jumbo ? ' <span class="tag">jumbo</span>' : '') + (s.overDti ? ' <span class="tag bad">over DTI</span>' : '') + '</div>' +
      '<div class="big ' + (d >= 0 ? 'pos' : 'neg') + '">' + (d >= 0 ? '+' : '−') + money(Math.abs(d), { short: true }) + '</div>' +
      '<div class="k">vs renting after ' + sim.params.years + ' yrs</div>' +
      '<div class="line"><span>vs 100% cash</span><b class="' + (s.vsCash >= 0 ? 'pos' : 'neg') + '">' + (s.key === 'cash' ? '—' : (s.vsCash >= 0 ? '+' : '−') + money(Math.abs(s.vsCash), { short: true })) + '</b></div>' +
      '<div class="line"><span>Loan</span><b>' + (sim.loan ? money(sim.loan, { short: true }) + ' @ ' + pct(sim.rate, 2) : 'none') + '</b></div>' +
      '<div class="line"><span>Cash at close</span><b>' + money(sim.cashAtClose, { short: true }) + '</b></div>' +
      '<div class="line"><span>Kept invested</span><b>' + money(sim.retained, { short: true }) + (s.financeYear ? ' (yr ' + s.financeYear + ')' : '') + '</b></div>' +
      '<div class="line"><span>Monthly, yr 1</span><b class="' + monthlyCls + '">' + money(sim.monthlyYear1) + '</b></div>' +
      '<div class="line"><span>After deductions</span><b>' + money(sim.monthlyAfterTaxYear1) + '</b></div>' +
      '<div class="verdict-line">' + s.verdict + '</div>' +
      '</div>';
  }

  function render() {
    readInputs();
    var p = withSellFn(state);
    var cmp = M.compareScenarios(p);
    var customSim = M.simulateFinanced(p, { downPct: state.downPct, financeYear: 0 });
    var custom = { key: 'custom', name: 'Custom ' + pct(state.downPct, 0) + ' down', downPct: state.downPct, financeYear: 0, sim: customSim,
      overDti: customSim.loan > cmp.dtiMaxLoan + 1 };
    custom.vsCash = customSim.finalDiff - cmp.scenarios[0].sim.finalDiff;
    custom.verdict = custom.overDti ? 'Likely not approvable at this income.' :
      custom.vsCash >= 0 ? 'Cheaper than 100% cash by ' + money(custom.vsCash, { short: true }) + '.' : 'Costs ' + money(-custom.vsCash, { short: true }) + ' more than 100% cash.';
    var list = cmp.scenarios.concat([custom]);
    current = { list: list, cmp: cmp };

    var W0 = p.price * (1 + p.buyClose);
    $('#d-deal').textContent = money(p.price, { short: true }) + ' vs ' + money(p.rent) + '/mo, ' + p.years + ' yrs' + (p.neighborhood ? ', ' + (AS.byKey(p.neighborhood) || {}).name : '');
    $('#d-w0').textContent = money(W0, { short: true });
    $('#d-dti').textContent = money(cmp.dtiMaxLoan, { short: true }) + ' at ' + money(state.income, { short: true }) + ' income';
    $('#d-jumbo').textContent = money(state.conformingLimit, { short: true });

    $('#scenarios').innerHTML = list.map(function (s) { return card(s, cmp.scenarios[0]); }).join('');

    var x = M.crossover(p, customSim.loan || p.price * 0.5);
    $('#x-loan').textContent = pct(x.afterTaxLoan, 2) + ' (' + pct(x.rate, 2) + ' less ' + pct(x.shield, 1) + ' shield)';
    $('#x-ret').textContent = pct(x.afterTaxRetained, 2);
    $('#x-rate').textContent = pct(x.crossoverRate, 2);
    $('#x-verdict').textContent = x.leverageWins ? 'Borrowing beats keeping cash invested' : 'Keeping cash invested beats borrowing';
    $('#x-verdict').className = 'v ' + (x.leverageWins ? 'pos' : 'neg');
    $('#x-note').textContent = 'For a ' + money(customSim.loan || p.price * 0.5, { short: true }) + ' loan. The shield is the share of interest that is deductible times your marginal rates' +
      (state.itemize ? '' : ' (zero: standard deduction)') + '. Retained return is annualized after tax over the hold. Below the tie rate, financing is a return decision; above it, only a liquidity one.';

    var sel = $('#detail-scenario');
    var prev = sel.value;
    sel.innerHTML = list.map(function (s) { return '<option value="' + s.key + '">' + s.name + '</option>'; }).join('');
    sel.value = list.some(function (s) { return s.key === prev; }) ? prev : 'custom';
    renderYearly();

    var h = toHash(state);
    history.replaceState(null, '', location.pathname + location.search + h);
    $('#back').href = $('#back2').href = 'index.html' + h;
    markPreset();
  }

  function renderYearly() {
    var s = null;
    current.list.forEach(function (x) { if (x.key === $('#detail-scenario').value) s = x; });
    if (!s) return;
    var cols = ['Yr', 'Rent', 'Own costs', 'P&I', 'Tax benefit', 'Loan balance', 'Retained R', 'Renter portfolio', 'Owner net', 'Difference'];
    var h = '<thead><tr>' + cols.map(function (c) { return '<th>' + c + '</th>'; }).join('') + '</tr></thead><tbody>';
    s.sim.rows.forEach(function (r) {
      h += '<tr class="y" data-y="' + r.year + '"><td>' + r.year + '</td><td>' + money(r.rent) + '</td><td>' + money(r.own) + '</td><td>' + money(r.pi) +
        '</td><td>' + money(r.taxBenefit.total) + '</td><td>' + money(r.balanceEnd) + '</td><td>' + money(r.RNet) + '</td><td>' + money(r.renterNet) +
        '</td><td>' + money(r.ownerNet) + '</td><td class="' + (r.diff >= 0 ? 'pos' : 'neg') + '">' + money(r.diff) + '</td></tr>';
      h += '<tr class="detail" hidden data-for="' + r.year + '"><td colspan="10"><div>' + detail(s, r) + '</div></td></tr>';
    });
    $('#yearly').innerHTML = h + '</tbody>';
  }

  function detail(s, r) {
    var p = s.sim.params, f = s.sim.fin;
    var lines = [
      '<b>Own costs ' + money(r.own) + '</b> = property tax ' + money(r.propTax) + ' + insurance ' + money(r.insurance) + ' + maintenance ' + money(r.maintenance) + (r.hoa ? ' + HOA ' + money(r.hoa) : ''),
      r.pi ? '<b>P&I ' + money(r.pi) + '</b> = interest ' + money(r.interest) + ' + principal ' + money(r.principal) + ' at ' + pct(s.sim.rate, 2) + '; balance ' + money(r.balanceStart) + ' → ' + money(r.balanceEnd) : '<b>No loan payments</b> this year',
      '<b>Tax benefit ' + money(r.taxBenefit.total) + '</b> = federal ' + money(r.taxBenefit.fed) + ' (SALT capped at ' + money(f.saltCap) + ' + deductible interest ' + money(r.taxBenefit.fedInterest || 0) + ', less standard ' + money(f.fedStd) + ', × ' + pct(p.fedRate) + ') + CA ' + money(r.taxBenefit.ca) + ' (property tax + interest ' + money(r.taxBenefit.caInterest || 0) + ', less standard ' + money(f.caStd) + ', × ' + pct(p.caRate) + ')',
      '<b>Owner outflow ' + money(r.outflow) + '</b> = own costs + P&I − tax benefit; vs rent ' + money(r.rent) + ' → renter ' + (r.contribution >= 0 ? 'saves ' + money(r.contribution) : 'draws ' + money(-r.contribution)),
      '<b>Retained R ' + money(r.REnd) + '</b> = ' + money(r.RStart) + ' + growth ' + money(r.RGrowth) + (r.refiProceeds ? ' + refi proceeds ' + money(r.refiProceeds) : '') + ' → after tax ' + money(r.RNet),
      '<b>Renter portfolio ' + money(r.portEnd) + '</b> = ' + money(r.portStart) + ' + growth ' + money(r.portGrowth) + ' + contribution ' + money(r.contribution) + ' → after tax ' + money(r.renterNet),
      '<b>Owner net ' + money(r.ownerNet) + '</b> = sale proceeds ' + money(r.sale.proceeds) + ' − tax ' + money(r.sale.tax) + ' − balance ' + money(r.balanceEnd) + ' + R ' + money(r.RNet),
      '<b>Difference ' + money(r.diff) + '</b> = owner net − renter net'
    ];
    return lines.join('<br>');
  }

  function markPreset() {
    document.querySelectorAll('[data-rpreset]').forEach(function (b) {
      var eq = b.dataset.rpreset === 'equities';
      var on = eq ? (Math.abs(state.retainedRate - 0.07) < 1e-9 && state.retainedTaxMode === 'deferred')
                  : (Math.abs(state.retainedRate - state.oppRate) < 1e-9 && state.retainedTaxMode === 'annual');
      b.classList.toggle('active', on);
    });
  }

  // ---------- events ----------
  var timer = null;
  function schedule() { clearTimeout(timer); timer = setTimeout(render, 30); }
  inputs.forEach(function (el) {
    el.addEventListener('input', function () { if (el.dataset.k === 'downPct') $('#down-label').textContent = el.value + '%'; schedule(); });
    el.addEventListener('blur', function () { readInputs(); writeInputs(); });
  });
  document.querySelectorAll('[data-rpreset]').forEach(function (b) {
    b.addEventListener('click', function () {
      readInputs();
      if (b.dataset.rpreset === 'equities') { state.retainedRate = 0.07; state.retainedTaxMode = 'deferred'; }
      else { state.retainedRate = state.oppRate; state.retainedTaxMode = 'annual'; }
      writeInputs(); render();
    });
  });
  $('#reset').addEventListener('click', function () {
    for (var k in M.FIN_DEFAULTS) state[k] = M.FIN_DEFAULTS[k];
    writeInputs(); render();
  });
  $('#detail-scenario').addEventListener('change', renderYearly);
  $('#yearly').addEventListener('click', function (e) {
    var tr = e.target.closest('tr.y'); if (!tr) return;
    var d = $('#yearly tr.detail[data-for="' + tr.dataset.y + '"]'); d.hidden = !d.hidden;
  });
  window.addEventListener('hashchange', function () { state = fromHash(); writeInputs(); render(); });

  state = fromHash();
  writeInputs();
  render();
})();
