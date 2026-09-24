/* Static dashboard over docs/data/*.json written by the Python tracker. */
(function () {
  'use strict';
  var $ = function (s) { return document.querySelector(s); };
  var AS = window.Assumptions;
  var data = {};

  function money(n, short) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var s = n < 0 ? '−' : ''; n = Math.abs(n);
    if (short) { if (n >= 1e6) return s + '$' + (n / 1e6).toFixed(2) + 'M'; if (n >= 1e3) return s + '$' + Math.round(n / 1e3) + 'K'; }
    return s + '$' + Math.round(n).toLocaleString('en-US');
  }
  function pct(x, d) { return x === null || x === undefined || isNaN(x) ? '—' : (x * 100).toFixed(d === undefined ? 1 : d) + '%'; }
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // ---------- tiny SVG line chart ----------
  function chart(title, rows, opts) {
    opts = opts || {};
    rows = (rows || []).filter(function (r) { return r.value !== null && r.value !== undefined; });
    if (rows.length < 2) return '<div class="chart"><div class="ct">' + esc(title) + '</div><div class="hint">no data yet</div></div>';
    var w = 300, h = 90, pad = 4;
    var vals = rows.map(function (r) { return r.value; });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (max === min) { max += 1; min -= 1; }
    var pts = rows.map(function (r, i) {
      var x = pad + (w - 2 * pad) * i / (rows.length - 1);
      var y = h - pad - (h - 2 * pad) * (r.value - min) / (max - min);
      return x.toFixed(1) + ',' + y.toFixed(1);
    });
    var last = rows[rows.length - 1], first = rows[0];
    var fmt = opts.fmt || function (v) { return v.toFixed(2); };
    var change = last.value - first.value;
    return '<div class="chart"><div class="ct">' + esc(title) + '</div>' +
      '<div class="cv">' + fmt(last.value) + ' <small>' + esc(last.date) + '</small></div>' +
      '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none"><polyline fill="none" stroke="currentColor" stroke-width="1.5" points="' + pts.join(' ') + '"/></svg>' +
      '<div class="hint">' + esc(first.date) + ' → ' + esc(last.date) + ': ' + (change >= 0 ? '+' : '') + fmt(change) + '</div></div>';
  }

  function renderMarket() {
    var rates = data.rates || {}, labels = data.meta.rates_labels || {};
    $('#rate-charts').innerHTML = Object.keys(rates).map(function (k) {
      return chart(labels[k] || k, rates[k].slice(-156), { fmt: function (v) { return v.toFixed(2) + '%'; } });
    }).join('');
    var m = data.market || {}, ml = data.meta.market_labels || {}, out = [];
    Object.keys(m.fred || {}).forEach(function (k) {
      out.push(chart(ml[k] || k, m.fred[k].slice(-60), { fmt: function (v) { return v >= 1000 ? Math.round(v).toLocaleString('en-US') : v.toFixed(0); } }));
    });
    var zm = m.zillow_metro || {};
    if (zm.inventory) out.push(chart('Zillow LA metro inventory', zm.inventory.slice(-60), { fmt: function (v) { return Math.round(v).toLocaleString('en-US'); } }));
    if (zm.days_to_pending) out.push(chart('Zillow days to pending', zm.days_to_pending.slice(-60), { fmt: function (v) { return v.toFixed(0) + ' d'; } }));
    if (zm.sale_to_list) out.push(chart('Zillow sale-to-list ratio', zm.sale_to_list.slice(-60), { fmt: function (v) { return v.toFixed(3); } }));
    var rf = m.redfin || {};
    if (rf.redfin_months_of_supply) out.push(chart('Redfin months of supply (LA metro)', rf.redfin_months_of_supply, { fmt: function (v) { return v.toFixed(1); } }));
    if (rf.redfin_sale_to_list) out.push(chart('Redfin sale-to-list (LA metro)', rf.redfin_sale_to_list, { fmt: function (v) { return v.toFixed(3); } }));
    $('#market-charts').innerHTML = out.join('');
    var comps = (m.comps || []).map(function (c) { return (data.meta.neighborhoods[c.neighborhood] || { name: c.neighborhood }).name + ' ' + c.n; });
    $('#market-note').textContent = comps.length ? 'Sold comps on file: ' + comps.join(', ') : 'No sold comps yet — run `python -m tracker comps` or import a Redfin sold-search CSV.';
  }

  function renderAlerts() {
    var a = data.meta.alerts || [];
    $('#alerts').innerHTML = a.map(function (x) {
      return '<div class="card alert ' + esc(x.level) + '"><b>' + esc(x.title) + '</b><div class="hint">' + esc(x.detail) + '</div></div>';
    }).join('');
  }

  // ---------- listings ----------
  function calcLink(l, page) {
    var nb = data.meta.neighborhoods[l.neighborhood] || {};
    var parts = ['price=' + Math.round(l.list_price || 0)];
    if (l.est_rent) parts.push('rent=' + Math.round(l.est_rent));
    if (l.neighborhood && AS.byKey(l.neighborhood)) parts.push('neighborhood=' + l.neighborhood);
    var tax = (l.overrides && l.overrides.prop_tax_rate) || nb.prop_tax_rate;
    if (tax) parts.push('propTaxRate=' + tax);
    var ins = (l.overrides && l.overrides.insurance) || nb.insurance;
    if (ins) parts.push('insurance=' + Math.round(ins));
    if (l.hoa) parts.push('hoa=' + Math.round(l.hoa));
    return (page || 'index.html') + '#' + parts.join('&');
  }

  function bar(label, v) {
    if (v === null || v === undefined) return '<div class="bar"><span>' + label + '</span><i class="na">no data</i></div>';
    return '<div class="bar"><span>' + label + '</span><b style="width:' + Math.round(v) + '%"></b><i>' + Math.round(v) + '</i></div>';
  }

  function listingCard(l) {
    var nb = data.meta.neighborhoods[l.neighborhood] || {};
    var raw = l.raw || {}, c = l.components || {};
    var cut = l.original_list_price && l.list_price ? (l.original_list_price - l.list_price) / l.original_list_price : 0;
    var unchecked = data.meta.red_flags.filter(function (f) { return l.flags[f] === null || l.flags[f] === undefined; });
    var flagged = data.meta.red_flags.filter(function (f) { return l.flags[f] === true; });
    var hist = (l.price_history || []).map(function (h) { return esc(h.date) + ' ' + money(h.price, true) + (h.event !== 'list' ? ' (' + esc(h.event) + ')' : ''); }).join(' → ');
    return '<details class="listing"><summary>' +
      '<span class="score ' + (l.score >= 70 ? 'hi' : l.score >= 50 ? 'mid' : 'lo') + '">' + (l.score !== null && l.score !== undefined ? Math.round(l.score) : '–') + '</span>' +
      '<span class="addr">' + esc(l.address) + '<small>' + esc(nb.name || l.city || '') + (l.status !== 'active' ? ' · ' + esc(l.status) : '') + '</small></span>' +
      '<span class="price">' + money(l.list_price, true) + '<small>' + (l.sqft ? money(l.list_price / l.sqft) + '/sf' : '') + (cut > 0.001 ? ' · −' + pct(cut, 0) : '') + '</small></span>' +
      '</summary><div class="body">' +
      '<div class="verdict-line">' + esc(l.verdict || 'not scored yet') + '</div>' +
      '<div class="facts">' + [
        l.beds ? l.beds + ' bd' : '', l.baths ? l.baths + ' ba' : '', l.sqft ? Math.round(l.sqft).toLocaleString() + ' sf' : '',
        l.lot_sqft ? Math.round(l.lot_sqft).toLocaleString() + ' sf lot' : '', l.year_built ? 'built ' + l.year_built : '',
        l.days_on_market !== null && l.days_on_market !== undefined ? l.days_on_market + ' DOM' : '',
        l.hoa ? 'HOA ' + money(l.hoa) + '/mo' : '',
        l.last_sale_price ? 'last sold ' + money(l.last_sale_price, true) + (l.last_sale_date ? ' ' + esc(l.last_sale_date.slice(0, 4)) : '') : '',
        l.est_rent ? 'est. rent ' + money(l.est_rent) + '/mo' : '',
        l.breakeven_price ? 'breakeven ' + money(l.breakeven_price, true) : ''
      ].filter(Boolean).map(esc).join(' · ') + '</div>' +
      '<div class="bars">' + bar('$/sqft vs comps', c.ppsf) + bar('Price cuts', c.cuts) + bar('Days on market', c.dom) + bar('Below last sale', c.below_last_sale) + bar('Rent yield', c.yield) + bar('Breakeven', c.breakeven) + '</div>' +
      (hist ? '<div class="hint">Price history: ' + hist + '</div>' : '') +
      '<div class="flags">' + (flagged.length ? '<span class="tag bad">' + flagged.map(esc).join(', ') + '</span> ' : '') +
        (unchecked.length ? '<span class="hint">Unchecked: ' + unchecked.map(function (f) { return esc(f.replace(/_/g, ' ')); }).join(', ') + '</span>' : '<span class="hint">Checklist complete</span>') + '</div>' +
      (l.notes ? '<div class="hint">Notes: ' + esc(l.notes) + '</div>' : '') +
      '<div class="actions"><a class="cta" href="' + calcLink(l) + '">Cash calculator</a> <a class="cta" href="' + calcLink(l, 'finance.html') + '">Financing</a>' +
        (l.source_url ? ' <a class="ghost-link" href="' + esc(l.source_url) + '" target="_blank" rel="noopener">Listing ↗</a>' : '') +
        '<span class="hint"> sources: ' + esc((l.sources || []).join(', ')) + ' · id ' + esc(l.id) + '</span></div>' +
      '</div></details>';
  }

  function renderListings() {
    var nb = $('#f-nb').value, minScore = parseFloat($('#f-score').value) || 0, status = $('#f-status').value, sort = $('#f-sort').value;
    var rows = (data.listings || []).filter(function (l) {
      return (!nb || l.neighborhood === nb) && (l.score || 0) >= minScore && (!status || l.status === status);
    });
    var key = { score: function (l) { return -(l.score || 0); }, price: function (l) { return l.list_price || 0; },
      ppsf: function (l) { return l.sqft ? l.list_price / l.sqft : 1e9; }, dom: function (l) { return -(l.days_on_market || 0); },
      cut: function (l) { return l.original_list_price ? -((l.original_list_price - l.list_price) / l.original_list_price) : 0; } }[sort];
    rows.sort(function (a, b) { return key(a) - key(b); });
    $('#count').textContent = rows.length + ' of ' + (data.listings || []).length + ' listings';
    $('#listings').innerHTML = rows.map(listingCard).join('') || '<p class="hint">Nothing yet. Run the tracker or drop a Redfin CSV into data/inbox and run <code>python -m tracker import-csv && python -m tracker score && python -m tracker export</code>.</p>';
  }

  function renderMeta() {
    var m = data.meta;
    var req = m.requests_this_month || {};
    $('#generated').textContent = 'updated ' + (m.generated_at || '').replace('T', ' ').slice(0, 16);
    $('#meta').innerHTML = '<div class="metrics">' +
      '<div><span class="k">Price band</span><span class="v">' + money(m.price_band.min, true) + ' – ' + money(m.price_band.max, true) + '</span></div>' +
      '<div><span class="k">Listings / active / comps</span><span class="v">' + m.counts.listings + ' / ' + m.counts.active + ' / ' + m.counts.comps + '</span></div>' +
      '<div><span class="k">Requests this month</span><span class="v">' + (Object.keys(req).map(function (k) { return k + ' ' + req[k]; }).join(', ') || 'none') + '</span></div>' +
      '<div><span class="k">Last run</span><span class="v">' + (m.last_runs && m.last_runs[0] ? esc(m.last_runs[0].ts.slice(0, 16)) : '—') + '</span></div>' +
      '</div>';
  }

  function init() {
    var sel = $('#f-nb');
    Object.keys(data.meta.neighborhoods).forEach(function (k) {
      var o = document.createElement('option'); o.value = k; o.textContent = data.meta.neighborhoods[k].name; sel.appendChild(o);
    });
    ['#f-nb', '#f-score', '#f-status', '#f-sort'].forEach(function (s) { $(s).addEventListener('input', renderListings); });
    renderAlerts(); renderMarket(); renderListings(); renderMeta();
  }

  Promise.all(['meta', 'listings', 'rates', 'market'].map(function (n) {
    return fetch('data/' + n + '.json', { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error(n + ' ' + r.status); return r.json(); });
  })).then(function (res) {
    data = { meta: res[0], listings: res[1], rates: res[2], market: res[3] };
    init();
  }).catch(function (e) {
    $('#generated').textContent = 'no data yet (' + e.message + ')';
    $('#listings').innerHTML = '<p class="hint">Export data first: <code>python -m tracker export</code></p>';
  });
})();
