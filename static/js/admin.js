/* ksync Admin JS */
document.addEventListener('DOMContentLoaded', async () => {
  const r = await api('/api/me');
  if (!r.is_admin) { window.location.href = '/'; return; }
  tab('dashboard', document.querySelector('.slink'));
});

async function tab(name, btn) {
  document.querySelectorAll('.slink').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.querySelectorAll('.apanel').forEach(p => p.classList.remove('active'));
  const el = document.getElementById('panel-' + name);
  if (el) el.classList.add('active');
  if (name === 'dashboard')  loadDash();
  if (name === 'movies')     loadMovies();
  if (name === 'showtimes')  loadShowtimes();
  if (name === 'bookings')   loadBookings();
  if (name === 'analytics')  loadAnalytics();
}

async function loadDash() {
  const s = await api('/api/admin/stats');
  document.getElementById('stats-row').innerHTML = `
    <div class="stat-card"><div class="s-icon">👥</div><div class="s-lbl">Users</div><div class="s-val">${s.total_users}</div></div>
    <div class="stat-card"><div class="s-icon">🎬</div><div class="s-lbl">Movies</div><div class="s-val">${s.total_movies}</div></div>
    <div class="stat-card"><div class="s-icon">🎟</div><div class="s-lbl">Confirmed</div><div class="s-val">${s.total_bookings}</div></div>
    <div class="stat-card"><div class="s-icon">₦</div><div class="s-lbl">Revenue</div><div class="s-val">${Number(s.total_revenue).toLocaleString()}</div></div>`;
  document.getElementById('popular-list').innerHTML = s.popular_movies.map((m,i) => `
    <div class="pop-item">
      <span class="pop-rank ${i<3?'top':'}'}">#${i+1}</span>
      <span class="pop-title">${esc(m.title)}</span>
      <span class="pop-count">${m.bookings} bookings</span>
    </div>`).join('') || '<p style="color:var(--text2)">No data yet</p>';
}

async function loadMovies() {
  const movies = await api('/api/admin/movies');
  const wrap = document.getElementById('movies-tbl');
  if (!movies.length) { wrap.innerHTML='<p>No movies</p>'; return; }
  wrap.innerHTML = `<table class="admin-table">
    <thead><tr><th>Title</th><th>Genre</th><th>Rating</th><th>Status</th><th>Actions</th></tr></thead>
    <tbody>${movies.map(m => `<tr>
      <td><strong>${esc(m.title)}</strong></td>
      <td>${esc(m.genre)}</td><td>★ ${m.rating}</td>
      <td><span class="badge ${m.is_active?'badge-green':'badge-red'}">${m.is_active?'Active':'Hidden'}</span></td>
      <td>
        <button class="tbl-btn" onclick="toggleMovie(${m.id},${m.is_active})">${m.is_active?'Hide':'Show'}</button>
        <button class="tbl-btn del" onclick="deleteMovie(${m.id})">Delete</button>
      </td>
    </tr>`).join('')}</tbody></table>`;
}

async function toggleMovie(id, active) {
  const all = await api('/api/admin/movies');
  const m   = all.find(x => x.id === id);
  if (!m) return;
  await api(`/api/admin/movies/${id}`, { method:'PUT', body:{...m, is_active: active?0:1} });
  loadMovies();
}
async function deleteMovie(id) {
  if (!confirm('Deactivate this movie?')) return;
  await api(`/api/admin/movies/${id}`, {method:'DELETE'});
  loadMovies(); toast('Movie deactivated','info');
}
async function addMovie() {
  const errEl = document.getElementById('mf-err'); errEl.classList.add('hidden');
  const data = {
    title: val('m-title'), genre: val('m-genre'), description: val('m-desc'),
    duration_min: +val('m-dur'), rating: +val('m-rat'),
    poster_url: val('m-poster'), director: val('m-dir'),
    cast_list: val('m-cast'), release_year: +val('m-yr')
  };
  if (!data.title) { showErr(errEl,'Title required'); return; }
  const r = await api('/api/admin/movies', {method:'POST', body:data});
  if (r.error) { showErr(errEl, r.error); return; }
  closeModal('add-movie-modal'); loadMovies(); toast('Movie added!','success');
}

async function loadShowtimes() {
  const rows = await api('/api/admin/showtimes');
  const wrap = document.getElementById('showtimes-tbl');
  if (!rows.length) { wrap.innerHTML = '<p>No showtimes</p>'; return; }
  wrap.innerHTML = `<table class="admin-table">
    <thead><tr><th>Movie</th><th>Cinema</th><th>Hall</th><th>Date & Time</th><th>Price</th><th>Actions</th></tr></thead>
    <tbody>${rows.map(s => `<tr id="st-row-${s.id}">
      <td><strong>${esc(s.title)}</strong></td>
      <td>${esc(s.cinema_name)}</td>
      <td>${esc(s.hall_name)}</td>
      <td>${new Date(s.showtime).toLocaleString()}</td>
      <td>₦${Number(s.price).toLocaleString()}</td>
      <td>
        <button class="tbl-btn" onclick="openEditShowtime(${s.id},'${esc(s.title)}','${s.showtime}',${s.price})">Edit</button>
        <button class="tbl-btn del" onclick="deleteShowtime(${s.id})">Delete</button>
      </td>
    </tr>`).join('')}</tbody></table>`;
}

function openEditShowtime(id, title, showtime, price) {
  document.getElementById('edit-st-id').value   = id;
  document.getElementById('edit-st-label').textContent = title;
  // Convert "2026-06-05 18:30:00" → "2026-06-05T18:30" for datetime-local input
  document.getElementById('edit-st-time').value  = showtime.replace(' ', 'T').substring(0, 16);
  document.getElementById('edit-st-price').value = price;
  document.getElementById('edit-st-err').classList.add('hidden');
  openModal('edit-showtime-modal');
}

async function saveShowtime() {
  const id       = val('edit-st-id');
  const showtime = val('edit-st-time').replace('T', ' ') + ':00';
  const price    = val('edit-st-price');
  const errEl    = document.getElementById('edit-st-err');
  errEl.classList.add('hidden');
  if (!showtime || !price) { showErr(errEl, 'All fields are required'); return; }
  const r = await api(`/api/admin/showtimes/${id}`, {
    method: 'PUT',
    body: { showtime, price: parseFloat(price) }
  });
  if (r.error) { showErr(errEl, r.error); return; }
  closeModal('edit-showtime-modal');
  loadShowtimes();
  toast('Showtime updated', 'success');
}

async function deleteShowtime(id) {
  if (!confirm('Delete this showtime? All its seats and bookings will also be removed.')) return;
  const r = await api(`/api/admin/showtimes/${id}`, { method: 'DELETE' });
  if (r.error) { toast(r.error, 'error'); return; }
  loadShowtimes();
  toast('Showtime deleted', 'info');
}
async function prepShowtime() {
  const [movies, halls] = await Promise.all([api('/api/movies'), api('/api/halls')]);
  document.getElementById('st-movie').innerHTML = movies.map(m=>`<option value="${m.id}">${esc(m.title)}</option>`).join('');
  document.getElementById('st-hall').innerHTML  = halls.map(h=>`<option value="${h.id}">${esc(h.cinema_name)} — ${esc(h.name)} (${h.total_rows*h.total_cols} seats)</option>`).join('');
  openModal('add-showtime-modal');
}
async function addShowtime() {
  const data = {
    movie_id: +val('st-movie'), hall_id: +val('st-hall'),
    showtime: val('st-time').replace('T',' ')+':00', price: +val('st-price')
  };
  const r = await api('/api/admin/showtimes', {method:'POST', body:data});
  if (r.error) { toast(r.error,'error'); return; }
  closeModal('add-showtime-modal'); loadShowtimes(); toast('Showtime + seats created!','success');
}

async function loadBookings() {
  const rows = await api('/api/admin/bookings');
  const wrap = document.getElementById('bookings-tbl');
  if (!rows.length) { wrap.innerHTML='<p>No bookings</p>'; return; }
  wrap.innerHTML = `<table class="admin-table">
    <thead><tr><th>User</th><th>Movie</th><th>Cinema</th><th>Showtime</th><th>Seat</th><th>Amount</th><th>Status</th></tr></thead>
    <tbody>${rows.map(b=>`<tr>
      <td>${esc(b.username)}</td><td>${esc(b.title)}</td>
      <td>${esc(b.cinema_name)}</td>
      <td>${new Date(b.showtime).toLocaleString()}</td>
      <td>${b.row_label}${b.seat_number}</td>
      <td>₦${Number(b.amount).toLocaleString()}</td>
      <td><span class="badge ${b.status==='confirmed'?'badge-green':'badge-rose'}">${b.status}</span></td>
    </tr>`).join('')}</tbody></table>`;
}

function openModal(id) { document.getElementById(id).classList.remove('hidden') }
function closeModal(id) { document.getElementById(id).classList.add('hidden') }
function closeOverlay(e,id) { if(e.target===e.currentTarget) closeModal(id) }
function val(id) { return (document.getElementById(id)||{}).value?.trim()||'' }
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') }
function showErr(el,msg) { el.textContent=msg; el.classList.remove('hidden') }
function toast(msg,type='info') {
  const el = document.createElement('div');
  el.className=`toast ${type}`;
  el.textContent=msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(()=>el.remove(),3500);
}
async function api(url,opts={}) {
  const {method='GET',body}=opts;
  try {
    const r=await fetch(url,{method,headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
    return await r.json();
  } catch(e){return{error:'Network error'}}
}

// ════════════════════════════════════════════════════════
//  ANALYTICS
// ════════════════════════════════════════════════════════

const CHART_PALETTES = {
  genre:  ['#e05c5c','#e07c3a','#e0b83a','#5cb85c','#3a9de0','#7b5ce0','#e05ca8','#3ae0c8','#a0a0a0'],
  movies: ['#5c8ee0','#5cb8e0','#5ce0b8','#5ce07c','#b8e05c','#e0c85c','#e0855c','#e05c5c'],
  zones:  { front:'#e05c5c', middle:'#5c8ee0', back:'#5cb85c' },
};

let _chartInstances = {};   // canvas id → { data, type }

async function loadAnalytics() {
  const data = await api('/api/admin/analytics');
  if (data.error) { toast('Could not load analytics: ' + data.error, 'error'); return; }

  drawBarChart('chart-genre',  data.genres, CHART_PALETTES.genre,  { xLabel:'Genre',  yLabel:'Bookings' });
  drawBarChart('chart-movies', data.movies, CHART_PALETTES.movies, { xLabel:'Movie',  yLabel:'Bookings', truncate:14 });
  drawBarChart('chart-zones',  data.zones,  Object.values(CHART_PALETTES.zones), { xLabel:'Zone', yLabel:'Bookings', horizontal: true });
}

function drawBarChart(canvasId, dataset, palette, opts = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  // Store for theme-redraw
  _chartInstances[canvasId] = { dataset, palette, opts };

  // Size canvas to its CSS container
  const wrap = canvas.parentElement;
  canvas.width  = wrap.clientWidth  || 400;
  canvas.height = wrap.clientHeight || 240;

  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  if (!dataset || dataset.length === 0) {
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--muted') || '#aaa';
    ctx.font = '13px DM Sans, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No booking data yet', W / 2, H / 2);
    return;
  }

  const isDark    = document.documentElement.getAttribute('data-theme') === 'dark';
  const textColor = isDark ? '#e0e0e0' : '#333333';
  const gridColor = isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.06)';
  const { xLabel, yLabel, truncate, horizontal } = opts;

  const PAD = { top: 18, right: 18, bottom: horizontal ? 56 : 52, left: horizontal ? 110 : 42 };
  const chartW = W - PAD.left - PAD.right;
  const chartH = H - PAD.top  - PAD.bottom;

  const maxVal = Math.max(...dataset.map(d => d.value), 1);
  const niceMax = niceNumber(maxVal);
  const tickCount = 5;

  ctx.font = '11px DM Sans, sans-serif';
  ctx.textAlign = 'center';

  if (!horizontal) {
    // ── Vertical bar chart ──────────────────────────────
    const barW   = Math.min(48, (chartW / dataset.length) * 0.6);
    const gap    = chartW / dataset.length;

    // Grid lines + Y-axis labels
    for (let i = 0; i <= tickCount; i++) {
      const val = (niceMax / tickCount) * i;
      const y   = PAD.top + chartH - (val / niceMax) * chartH;
      ctx.strokeStyle = gridColor;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(PAD.left + chartW, y); ctx.stroke();
      ctx.fillStyle = textColor;
      ctx.textAlign = 'right';
      ctx.fillText(Math.round(val), PAD.left - 6, y + 4);
    }

    // Bars + X labels
    dataset.forEach((d, i) => {
      const x      = PAD.left + gap * i + gap / 2 - barW / 2;
      const barH   = (d.value / niceMax) * chartH;
      const y      = PAD.top + chartH - barH;
      const color  = Array.isArray(palette) ? palette[i % palette.length] : palette;

      // Bar with rounded top
      roundRect(ctx, x, y, barW, barH, 5);
      ctx.fillStyle = color;
      ctx.fill();

      // Value label on top
      ctx.fillStyle = textColor;
      ctx.textAlign = 'center';
      ctx.font = 'bold 11px DM Sans, sans-serif';
      ctx.fillText(d.value, x + barW / 2, y - 5);

      // X-axis label (truncated)
      ctx.font = '10px DM Sans, sans-serif';
      const label = truncate ? d.label.substring(0, truncate) + (d.label.length > truncate ? '…' : '') : d.label;
      ctx.fillText(label, x + barW / 2, PAD.top + chartH + 16);
    });

    // Axis lines
    ctx.strokeStyle = isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(PAD.left, PAD.top);
    ctx.lineTo(PAD.left, PAD.top + chartH);
    ctx.lineTo(PAD.left + chartW, PAD.top + chartH);
    ctx.stroke();

    // Y-axis label
    if (yLabel) {
      ctx.save();
      ctx.translate(12, PAD.top + chartH / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.textAlign = 'center';
      ctx.font = '10px DM Sans, sans-serif';
      ctx.fillStyle = textColor;
      ctx.fillText(yLabel, 0, 0);
      ctx.restore();
    }

  } else {
    // ── Horizontal bar chart (for zones) ────────────────
    const barH = Math.min(36, (chartH / dataset.length) * 0.6);
    const gap  = chartH / dataset.length;

    // Grid lines + X-axis labels
    for (let i = 0; i <= tickCount; i++) {
      const val = (niceMax / tickCount) * i;
      const x   = PAD.left + (val / niceMax) * chartW;
      ctx.strokeStyle = gridColor;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, PAD.top); ctx.lineTo(x, PAD.top + chartH); ctx.stroke();
      ctx.fillStyle = textColor;
      ctx.textAlign = 'center';
      ctx.font = '10px DM Sans, sans-serif';
      ctx.fillText(Math.round(val), x, PAD.top + chartH + 16);
    }

    dataset.forEach((d, i) => {
      const y      = PAD.top + gap * i + gap / 2 - barH / 2;
      const bw     = (d.value / niceMax) * chartW;
      const color  = Array.isArray(palette) ? palette[i % palette.length] : (CHART_PALETTES.zones[d.label.toLowerCase()] || '#5c8ee0');

      roundRect(ctx, PAD.left, y, bw, barH, 5);
      ctx.fillStyle = color;
      ctx.fill();

      // Value inside/beside bar
      ctx.fillStyle = textColor;
      ctx.textAlign = 'left';
      ctx.font = 'bold 11px DM Sans, sans-serif';
      ctx.fillText(d.value, PAD.left + bw + 6, y + barH / 2 + 4);

      // Y-axis label
      ctx.textAlign = 'right';
      ctx.font = '11px DM Sans, sans-serif';
      ctx.fillText(d.label, PAD.left - 8, y + barH / 2 + 4);
    });

    // Axis lines
    ctx.strokeStyle = isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.12)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(PAD.left, PAD.top);
    ctx.lineTo(PAD.left, PAD.top + chartH);
    ctx.lineTo(PAD.left + chartW, PAD.top + chartH);
    ctx.stroke();
  }
}

function roundRect(ctx, x, y, w, h, r) {
  if (h < 1) return;
  r = Math.min(r, h / 2, w / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h);
  ctx.lineTo(x, y + h);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function niceNumber(max) {
  if (max <= 0) return 10;
  const exp   = Math.floor(Math.log10(max));
  const frac  = max / Math.pow(10, exp);
  const nice  = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
  return nice * Math.pow(10, exp);
}

function downloadChart(canvasId, filename) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const link = document.createElement('a');
  link.download = filename + '.png';
  link.href = canvas.toDataURL('image/png');
  link.click();
}

// Redraw all visible charts when window resizes
window.addEventListener('resize', () => {
  Object.entries(_chartInstances).forEach(([id, cfg]) => {
    drawBarChart(id, cfg.dataset, cfg.palette, cfg.opts);
  });
});

// ════════════════════════════════════════════════════════
//  THEME TOGGLE
// ════════════════════════════════════════════════════════

function toggleAdminTheme() {
  const html  = document.documentElement;
  const next  = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('ksync-theme', next);
  // Redraw charts so colours update
  Object.entries(_chartInstances).forEach(([id, cfg]) => {
    drawBarChart(id, cfg.dataset, cfg.palette, cfg.opts);
  });
}

// Apply saved theme on load
(function() {
  const saved = localStorage.getItem('ksync-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
})();
