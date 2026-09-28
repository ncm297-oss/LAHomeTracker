/* Rentals page over docs/data/rentals.json written by `python -m tracker run-rentals`. */
(function () {
  'use strict';
  var $ = function (s) { return document.querySelector(s); };
  var data = null;

  var SOURCES = { 'realtor.com': 'Realtor.com', zillow: 'Zillow', 'apartments.com': 'Apartments.com', redfin: 'Redfin',
    hotpads: 'HotPads', trulia: 'Trulia', zumper: 'Zumper', manual: 'Added by hand', sign: 'Yard sign', craigslist: 'Craigslist',
    facebook: 'Facebook', email: 'Email alert' };
  // Realtor.com "mls" feed codes worth naming; the rest are MLS boards.
  var FEEDS = { ZILL: 'Zillow', ZUMU: 'Zumper', ZMPC: 'Zumper', APTL: 'Apartment List', AVAL: 'Avail' };
  var TRIAGE_ORDER = { shortlist: 0, contacted: 1, toured: 2, applied: 3 };

  function money(n) { return n === null || n === undefined || isNaN(n) ? '—' : '$' + Math.round(n).toLocaleString('en-US'); }
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function num(n) { return n === null || n === undefined ? null : Math.round(n).toLocaleString('en-US'); }

  function zillowUrl(r) {
    var q = [r.address.replace(/#/g, ''), r.city || '', 'CA', r.zip_code || ''].join(' ').replace(/,/g, ' ').trim().replace(/\s+/g, '-');
    return 'https://www.zillow.com/homes/' + encodeURIComponent(q).replace(/%2D/g, '-') + '_rb/';
  }
  function searchUrl(r) { return 'https://www.google.com/search?q=' + encodeURIComponent('"' + r.address + '" ' + (r.city || '') + ' for rent'); }
  function buyVsRentUrl(r) {
    var p = ['rent=' + Math.round(r.rent || 0)];
    if (r.neighborhood) p.push('neighborhood=' + r.neighborhood);
    return 'index.html#' + p.join('&');
  }
  // days_listed counts from the listing date when the source gives one, else from first sighting.
  function listedWithin(r, days) { return r.days_listed !== null && r.days_listed !== undefined && r.days_listed <= days; }

  function sourceChips(r) {
    var seen = {}, out = [];
    (r.links || []).forEach(function (l) {
      var name = SOURCES[l.source] || l.source;
      if (l.source === 'realtor.com' && FEEDS[l.feed]) name += ' (from ' + FEEDS[l.feed] + ')';
      var key = name + '|' + l.url;
      if (seen[key]) return; seen[key] = 1;
      out.push(l.url ? '<a class="chip" href="' + esc(l.url) + '" target="_blank" rel="noopener">' + esc(name) + ' ↗</a>' : '<span class="chip">' + esc(name) + '</span>');
    });
    var onZillow = (r.links || []).some(function (l) { return l.source === 'zillow' || l.feed === 'ZILL'; });
    if (!onZillow) out.push('<a class="chip ghost-chip" href="' + zillowUrl(r) + '" target="_blank" rel="noopener">Check Zillow ↗</a>');
    out.push('<a class="chip ghost-chip" href="' + searchUrl(r) + '" target="_blank" rel="noopener">Search web ↗</a>');
    return out.join('');
  }

  function card(r) {
    var c = data.criteria, nb = data.neighborhoods[r.neighborhood] || {};
    var isNew = listedWithin(r, 3) && r.status === 'active';
    var cut = r.original_rent && r.rent && r.rent < r.original_rent ? r.original_rent - r.rent : 0;
    var miss = r.misses || [];
    var facts = [
      r.kind ? '<span' + (miss.indexOf('type') >= 0 ? ' class="neg"' : '') + '>' + esc(r.kind) + '</span>' : '',
      r.beds !== null && r.beds !== undefined ? '<span' + (miss.indexOf('beds') >= 0 ? ' class="neg"' : '') + '>' + r.beds + ' bd</span>' : '<span class="neg">beds ?</span>',
      r.baths ? r.baths + ' ba' : '',
      r.sqft ? '<span' + (miss.indexOf('sqft') >= 0 ? ' class="neg"' : '') + '>' + num(r.sqft) + ' sf</span>' : 'sf ?',
      r.year_built ? 'built ' + r.year_built : '',
      r.days_listed !== null && r.days_listed !== undefined ? (r.days_listed > 120 ? '<span class="neg" title="Old listings are often already leased">listed ' + r.days_listed + ' days</span>' :
        (r.listed_date ? 'listed ' : 'first seen ') + (r.days_listed ? r.days_listed + (r.days_listed === 1 ? ' day' : ' days') + ' ago' : 'today')) : '',
      r.available_date ? 'available ' + esc(r.available_date) : ''
    ].filter(Boolean).join(' · ');
    var hist = (r.history || []).length > 1 ? (r.history || []).map(function (h) { return esc(h.date.slice(5)) + ' ' + money(h.rent); }).join(' → ') : '';
    var tags = (isNew ? '<span class="tag">new</span> ' : '') +
      (r.triage ? '<span class="tag' + (r.triage === 'pass' ? ' bad' : '') + '">' + esc(r.triage) + '</span> ' : '') +
      (r.status === 'gone' ? '<span class="tag bad">gone ' + esc((r.gone_date || '').slice(5)) + '</span> ' : '') +
      (miss.indexOf('rent') >= 0 ? '<span class="tag bad">' + (r.rent > c.rent[1] ? 'over' : 'under') + ' budget</span> ' : '');
    return '<details class="listing rental"><summary>' +
      (r.photo ? '<img class="thumb" src="' + esc(r.photo) + '" alt="" loading="lazy" referrerpolicy="no-referrer">' : '<span class="thumb none"></span>') +
      '<span class="addr">' + esc(r.address) + '<small>' + tags + esc(nb.name || r.city || '') + '</small><small class="facts">' + facts + '</small></span>' +
      '<span class="price">' + money(r.rent) + '<small>/mo' + (r.sqft ? ' · $' + (r.rent / r.sqft).toFixed(2) + '/sf' : '') + (cut ? ' · −' + money(cut) : '') + '</small></span>' +
      '</summary><div class="body">' +
      '<div class="chips">' + sourceChips(r) + '</div>' +
      (r.notes ? '<div class="hint"><b>Notes:</b> ' + esc(r.notes) + '</div>' : '') +
      (hist ? '<div class="hint">Rent history: ' + hist + '</div>' : '') +
      (r.listed_by ? '<div class="hint">Listed by ' + esc(r.listed_by) + '</div>' : '') +
      (r.description ? '<p class="desc">' + esc(r.description) + '</p>' : '') +
      '<div class="actions"><a class="ghost-link" href="' + buyVsRentUrl(r) + '">Buy vs rent at this rent →</a>' +
      '<span class="hint">first seen ' + esc(r.first_seen) + ' · id ' + esc(r.id) + '</span></div>' +
      '</div></details>';
  }

  var KINDS = { home: ['house', 'townhome', null], condo: ['house', 'townhome', null, 'condo', 'duplex'] };

  function render() {
    var nb = $('#f-nb').value, fit = $('#f-fit').value, type = $('#f-type').value, status = $('#f-status').value,
      triage = $('#f-triage').value, sort = $('#f-sort').value;
    var kinds = KINDS[type];
    var rows = data.rentals.filter(function (r) {
      var kind = r.kind || null;
      if (kinds && kinds.indexOf(kind) < 0) return false;
      // The Type filter owns the type question: a kind the viewer asked to see is not a miss.
      var misses = (r.misses || []).filter(function (m) { return m !== 'type'; });
      if (fit === 'match' && misses.length) return false;
      if (fit === 'near' && misses.length > 1) return false;
      if (nb && r.neighborhood !== nb) return false;
      if (status && r.status !== status) return false;
      if (triage === 'nopass' && r.triage === 'pass') return false;
      if (triage === 'shortlist' && !(r.triage in TRIAGE_ORDER)) return false;
      return true;
    });
    var key = {
      new: function (r) { return (r.days_listed === null || r.days_listed === undefined ? 1e4 : r.days_listed) + (r.rent || 0) / 1e6; },
      rent: function (r) { return r.rent || 0; }, rentd: function (r) { return -(r.rent || 0); },
      sqft: function (r) { return -(r.sqft || 0); }, ppsf: function (r) { return r.sqft ? r.rent / r.sqft : 1e9; }
    }[sort];
    rows.sort(function (a, b) {
      var ta = a.triage in TRIAGE_ORDER ? 0 : 1, tb = b.triage in TRIAGE_ORDER ? 0 : 1;
      return ta - tb || key(a) - key(b);
    });
    $('#count').textContent = rows.length + ' of ' + data.rentals.length + ' homes tracked' + (rows.some(function (r) { return r.triage in TRIAGE_ORDER; }) ? ' · your shortlist is pinned to the top' : '');
    $('#rentals').innerHTML = rows.map(card).join('') || '<p class="hint">Nothing matches these filters.</p>';
  }

  function renderSummary() {
    var c = data.criteria, rs = data.rentals;
    var active = rs.filter(function (r) { return r.status === 'active'; });
    var matches = active.filter(function (r) { return r.fit === 'match'; });
    var fresh = matches.filter(function (r) { return listedWithin(r, 7); });
    var srcCount = {};
    active.forEach(function (r) { (r.links || []).forEach(function (l) { srcCount[l.source] = (srcCount[l.source] || 0) + 1; }); });
    $('#summary').innerHTML =
      '<div><span class="k">Matches on the market</span><span class="v">' + matches.length + '</span></div>' +
      '<div><span class="k">New matches this week</span><span class="v">' + fresh.length + '</span></div>' +
      '<div><span class="k">Homes tracked (in range)</span><span class="v">' + active.length + '</span></div>' +
      '<div><span class="k">Sources</span><span class="v">' + (Object.keys(srcCount).map(function (k) { return esc(SOURCES[k] || k) + ' ' + srcCount[k]; }).join(', ') || '—') + '</span></div>';
    $('#criteria').textContent = 'Match = ' + money(c.rent[0]) + '–' + money(c.rent[1]) + '/mo, ' + c.min_beds + '+ beds, ' + num(c.min_sqft) + '+ sqft (unknown sqft still counts), ' +
      c.match_types.join(' or ') + '. A near miss fails exactly one of those.';
    var run = data.last_runs && data.last_runs[0];
    $('#generated').textContent = 'updated ' + (data.generated_at || '').replace('T', ' ').slice(0, 16) + (run && JSON.parse(run.summary).pulled_ok === false ? ' · last pull incomplete' : '');
  }

  function init() {
    var sel = $('#f-nb');
    Object.keys(data.neighborhoods).forEach(function (k) {
      var o = document.createElement('option'); o.value = k; o.textContent = data.neighborhoods[k].name; sel.appendChild(o);
    });
    ['#f-nb', '#f-fit', '#f-type', '#f-status', '#f-triage', '#f-sort'].forEach(function (s) { $(s).addEventListener('input', render); });
    renderSummary(); render();
  }

  fetch('data/rentals.json', { cache: 'no-store' }).then(function (r) {
    if (!r.ok) throw new Error('rentals.json ' + r.status);
    return r.json();
  }).then(function (d) { data = d; init(); }).catch(function (e) {
    $('#generated').textContent = 'no data yet (' + e.message + ')';
    $('#rentals').innerHTML = '<p class="hint">Run <code>python -m tracker run-rentals</code> first.</p>';
  });
})();
