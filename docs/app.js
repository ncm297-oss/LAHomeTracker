/* Page glue: reads inputs, calls BuyRent, renders. State lives in the URL hash. */
(function () {
  'use strict';
  var BR = window.BuyRent;
  var $ = function (s) { return document.querySelector(s); };
  var inputs = Array.prototype.slice.call(document.querySelectorAll('#inputs [data-k]'));

  // ---------- formatting ----------
  function money(n, opts) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var sign = n < 0 ? '−' : '';
    n = Math.abs(n);
    if (opts && opts.short) {
      if (n >= 1e6) return sign + '$' + (n / 1e6).toFixed(2) + 'M';
      if (n >= 1e3) return sign + '$' + Math.round(n / 1e3) + 'K';
    }
    return sign + '$' + Math.round(n).toLocaleString('en-US');
  }
  function pct(x, d) { return x === null || isNaN(x) ? '—' : (x * 100).toFixed(d === undefined ? 1 : d) + '%'; }

  // ---------- state <-> inputs ----------
  function readParams() {
    var p = {};
    inputs.forEach(function (el) {
      var k = el.dataset.k, t = el.dataset.t;
      if (el.tagName === 'SELECT') { p[k] = el.value; return; }
      var v = parseFloat(String(el.value).replace(/[^0-9.\-]/g, ''));
      if (isNaN(v)) return;
      p[k] = t === 'pct' ? v / 100 : t === 'int' ? Math.round(v) : v;
    });
    return BR.withDefaults(p);
  }

  function writeParams(p) {
    inputs.forEach(function (el) {
      var k = el.dataset.k, t = el.dataset.t, v = p[k];
      if (el.tagName === 'SELECT') { el.value = v; return; }
      if (t === 'pct') el.value = +(v * 100).toFixed(3);
      else if (t === 'money') el.value = Math.round(v).toLocaleString('en-US');
      else el.value = v;
    });
  }

  function toHash(p) {
    var parts = [];
    for (var k in BR.DEFAULTS) if (p[k] !== BR.DEFAULTS[k]) parts.push(k + '=' + encodeURIComponent(p[k]));
    return parts.length ? '#' + parts.join('&') : '';
  }
  function fromHash() {
    var p = {};
    location.hash.replace(/^#/, '').split('&').forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf('='), k = kv.slice(0, i), v = decodeURIComponent(kv.slice(i + 1));
      if (!(k in BR.DEFAULTS)) return;
      p[k] = k === 'oppTaxMode' ? v : parseFloat(v);
    });
    return BR.withDefaults(p);
  }

  // ---------- rendering ----------
  function renderVerdict(p, sim) {
    var d = sim.finalDiff;
    var card = $('#verdict');
    card.classList.toggle('good', d >= 0);
    card.classList.toggle('bad', d < 0);
    $('#v-diff').textContent = (d >= 0 ? 'Buying wins by ' : 'Renting wins by ') + money(Math.abs(d), { short: true });
    $('#v-diff-label').textContent = 'after ' + p.years + ' years at ' + money(p.price, { short: true }) +
      ' vs ' + money(p.rent) + '/mo rent, ' + pct(p.appreciation) + ' appreciation';

    var bePrice = BR.breakevenPrice(p);
    var beAppr = BR.breakevenAppreciation(p);
    var rentEq = BR.rentEquivalent(p);
    $('#v-be-price').textContent = bePrice === null ? 'none in range' : money(bePrice, { short: true });
    $('#v-be-appr').textContent = beAppr === null ? 'none in range' : pct(beAppr) + ' / yr';
    $('#v-rent-eq').textContent = rentEq === null ? 'none in range' : money(rentEq) + '/mo';
    $('#v-monthly').innerHTML = money(sim.year1Monthly) + '/mo<br><small>+ ' + money(sim.year1OppCost / 12) + '/mo forgone return</small>';

    var notes = [];
    if (bePrice !== null) notes.push('At ' + money(p.rent) + '/mo rent this house needs to be at or below ' + money(bePrice, { short: true }) + ' to tie renting.');
    if (rentEq !== null) notes.push('At ' + money(p.price, { short: true }) + ' you would need to be paying ' + money(rentEq) + '/mo in rent for buying to tie.');
    var by = BR.breakevenYear(sim);
    if (by !== null && by > 1) notes.push('Buying pulls ahead if you sell after year ' + by + '.');
    else if (by === null) notes.push('Buying never pulls ahead within the hold.');
    $('#v-note').textContent = notes.join(' ');
  }

  function renderSensitivity(p) {
    var g = BR.sensitivity(p);
    var h = '<thead><tr><th>Appreciation</th>' + g.holds.map(function (y) { return '<th>' + y + ' yrs</th>'; }).join('') + '</tr></thead><tbody>';
    g.apprs.forEach(function (a, i) {
      h += '<tr><td>' + pct(a, 0) + '</td>' + g.cells[i].map(function (d) {
        return '<td class="' + (d >= 0 ? 'pos' : 'neg') + '">' + money(d, { short: true }) + '</td>';
      }).join('') + '</tr>';
    });
    $('#sens').innerHTML = h + '</tbody>';
  }

  function renderYearly(p, sim) {
    var cols = ['Yr', 'Rent', 'Own costs', 'Home value', 'Renter portfolio', 'Owner net if sold', 'Difference if sold'];
    var h = '<thead><tr>' + cols.map(function (c) { return '<th>' + c + '</th>'; }).join('') + '</tr></thead><tbody>';
    sim.rows.forEach(function (r) {
      h += '<tr class="y" data-y="' + r.year + '"><td>' + r.year + '</td><td>' + money(r.rent) + '</td><td>' + money(r.own) +
        '</td><td>' + money(r.valueEnd) + '</td><td>' + money(r.renterNet) + '</td><td>' + money(r.ownerNet) +
        '</td><td class="' + (r.diff >= 0 ? 'pos' : 'neg') + '">' + money(r.diff) + '</td></tr>';
      h += '<tr class="detail" hidden data-for="' + r.year + '"><td colspan="7"><div>' + detail(p, r) + '</div></td></tr>';
    });
    h += '</tbody>';
    $('#yearly').innerHTML = h;
  }

  function detail(p, r) {
    var taxMode = p.oppTaxMode === 'annual' ? 'after ' + pct(p.fedRate) + ' federal tax on the return'
      : p.oppTaxMode === 'deferred' ? 'pre-tax; gains taxed at ' + pct(BR.cgRate(p)) + ' on sale' : 'pre-tax';
    var lines = [
      '<b>Own costs ' + money(r.own) + '</b> = property tax ' + money(r.propTax) + ' (' + pct(p.propTaxRate, 2) + ' × assessed ' + money(r.assessed) +
        ') + insurance ' + money(r.insurance) + ' + maintenance ' + money(r.maintenance) + ' (' + pct(p.maintRate) + ' × ' + money(r.valueStart) + ')' +
        (r.hoa ? ' + HOA ' + money(r.hoa) : ''),
      '<b>Rent ' + money(r.rent) + '</b> = ' + money(p.rent) + ' × 12 × (1 + ' + pct(p.rentGrowth) + ')^' + (r.year - 1),
      '<b>Renter portfolio ' + money(r.portEnd) + '</b> = ' + money(r.portStart) + ' + growth ' + money(r.portGrowth) + ' (' + taxMode + ') ' +
        (r.contribution >= 0 ? '+ saved ' + money(r.contribution) : '− drawn ' + money(-r.contribution)) + ' (own costs − rent)' +
        (p.oppTaxMode === 'deferred' ? ' → net of tax on gain: ' + money(r.renterNet) : ''),
      '<b>Home value ' + money(r.valueEnd) + '</b> = ' + money(r.valueStart) + ' × (1 + ' + pct(p.appreciation) + ')',
      '<b>Owner net ' + money(r.ownerNet) + '</b> = proceeds ' + money(r.sale.proceeds) + ' (after ' + pct(p.sellClose) + ' selling costs) − tax ' + money(r.sale.tax) +
        ' on gain ' + money(r.sale.gain) + ' over basis ' + money(p.price * (1 + p.buyClose)) + ', less ' + money(p.cgExclusion) + ' exclusion',
      '<b>Difference ' + money(r.diff) + '</b> = owner net − renter net. Cumulative: rent paid ' + money(r.cumRent) + ', own costs paid ' + money(r.cumOwn) + '.'
    ];
    return lines.join('<br>');
  }

  function markPreset(p) {
    document.querySelectorAll('[data-preset]').forEach(function (b) {
      var pr = BR.PRESETS[b.dataset.preset];
      b.classList.toggle('active', Math.abs(pr.oppRate - p.oppRate) < 1e-9 && pr.oppTaxMode === p.oppTaxMode);
    });
  }

  var raf = null;
  function render() {
    var p = readParams();
    if (p.years < 1) p.years = 1;
    if (p.years > 40) p.years = 40;
    var sim = BR.simulate(p);
    renderVerdict(p, sim);
    renderSensitivity(p);
    renderYearly(p, sim);
    markPreset(p);
    history.replaceState(null, '', location.pathname + location.search + toHash(p));
  }
  function schedule() { if (raf) cancelAnimationFrame(raf); raf = requestAnimationFrame(render); }

  // ---------- events ----------
  inputs.forEach(function (el) {
    el.addEventListener('input', schedule);
    el.addEventListener('blur', function () { writeParams(readParams()); }); // re-format money fields
  });
  document.querySelectorAll('[data-preset]').forEach(function (b) {
    b.addEventListener('click', function () {
      var p = readParams(), pr = BR.PRESETS[b.dataset.preset];
      p.oppRate = pr.oppRate; p.oppTaxMode = pr.oppTaxMode;
      writeParams(p); render();
    });
  });
  $('#reset').addEventListener('click', function () { writeParams(BR.DEFAULTS); render(); });
  $('#yearly').addEventListener('click', function (e) {
    var tr = e.target.closest('tr.y');
    if (!tr) return;
    var d = $('#yearly tr.detail[data-for="' + tr.dataset.y + '"]');
    d.hidden = !d.hidden;
  });
  window.addEventListener('hashchange', function () { writeParams(fromHash()); render(); });

  writeParams(fromHash());
  render();
})();
