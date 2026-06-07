/* ═══════════════════════════════════════════════════════════════════════════
   ksync — Frontend App JS
   Real Paystack inline popup · Seat-aware recommendations · Live seat map
   Genre Matcher (rule-based) · Session-persistent auth
   ═══════════════════════════════════════════════════════════════════════════ */

// ─── STATE ─────────────────────────────────────────────────────────────────
const S = {
  user: null,
  movies: [],
  showtimeId: null,
  selectedSeat: null,
  lockExpiry: null,
  booking: null,
  seatPoll: null,
  lockTick: null,
  matcherMovies: [],
};

// ─── BOOT ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  applyTheme(localStorage.getItem('theme') || 'light');
  await checkAuth();   // Restores session if cookie still valid
  loadMovies();
  loadGenres();
  loadCinemas();
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAllModals(); });
});

// ─── AUTH ──────────────────────────────────────────────────────────────────
async function checkAuth() {
  const r = await api('/api/me');
  if (r.logged_in) setUser(r);
}

function setUser(u) {
  S.user = u;
  ge('nav-auth').style.display = 'none';
  ge('nav-user').style.display = 'flex';
  ge('user-chip').textContent  = u.username;
  ge('admin-link').style.display = u.is_admin ? '' : 'none';
  show('rec-nav'); show('tix-nav'); show('hero-rec-btn');
}

function clearUser() {
  S.user = null;
  ge('nav-auth').style.display = 'flex';
  ge('nav-user').style.display  = 'none';
  hide('rec-nav'); hide('tix-nav'); hide('hero-rec-btn');
}

async function login() {
  const username = val('login-user'), password = val('login-pass');
  const errEl = ge('login-err');
  hide(errEl);
  if (!username || !password) { showErr(errEl, 'Please enter both username and password'); return; }
  const r = await api('/api/login', { method: 'POST', body: { username, password } });
  if (r.error) { showErr(errEl, r.error); toast(r.error, 'error'); return; }
  setUser(r);
  closeModal('login-modal');
  ge('login-user').value = '';
  ge('login-pass').value = '';
  toast('Welcome back, ' + r.username + ' ✦', 'success');
  if (r.is_admin) window.location.href = '/admin';
}

async function register() {
  const username = val('reg-user'), email = val('reg-email'), password = val('reg-pass');
  const errEl = ge('reg-err');
  hide(errEl);
  if (!username || !email || !password) { showErr(errEl, 'All fields required'); return; }
  if (password.length < 6) { showErr(errEl, 'Password must be at least 6 characters'); return; }
  const r = await api('/api/register', { method: 'POST', body: { username, email, password } });
  if (r.error) { showErr(errEl, r.error); return; }  // server returns "Username or email already exists"
  setUser(r);
  closeModal('register-modal');
  ge('reg-user').value = ''; ge('reg-email').value = ''; ge('reg-pass').value = '';
  toast('Account created! Welcome ✦', 'success');
}

async function logout() {
  await api('/api/logout', { method: 'POST' });
  clearUser();
  showSection('browse');
  toast('Signed out successfully', 'info');
}

// ─── NAV / SECTIONS ────────────────────────────────────────────────────────
function showSection(name) {
  document.querySelector('.main').scrollIntoView({ behavior: 'smooth' });
  document.querySelectorAll('.section').forEach(s => s.classList.add('hidden'));
  const sec = ge('section-' + name);
  if (!sec) return;
  sec.classList.remove('hidden');
  if (name === 'recommendations') {
    if (!S.user) { openModal('login-modal'); return; }
    loadRecs();
  } else if (name === 'my-bookings') {
    if (!S.user) { openModal('login-modal'); return; }
    loadTickets();
  } else if (name === 'genre-matcher') {
    initGenreMatcher();
  }
}

// ─── MOVIES ────────────────────────────────────────────────────────────────
async function loadMovies() {
  const movies = await api('/api/movies');
  S.movies = Array.isArray(movies) ? movies : [];
  renderGrid(S.movies, 'movies-grid', false);
}

async function loadGenres() {
  const genres = await api('/api/genres');
  if (!Array.isArray(genres)) return;
  const sel = ge('genre-select');
  genres.forEach(g => {
    const o = document.createElement('option');
    o.value = g; o.textContent = g; sel.appendChild(o);
  });
}

function filterMovies() {
  const q = val('search-input').toLowerCase();
  const g = val('genre-select');
  const filtered = S.movies.filter(m =>
    (!g || m.genre === g) &&
    (!q || m.title.toLowerCase().includes(q) || (m.description||'').toLowerCase().includes(q)));
  renderGrid(filtered, 'movies-grid', false);
}

function renderGrid(movies, containerId, isRec) {
  const grid = ge(containerId);
  if (!grid) return;
  if (!movies || !movies.length) {
    grid.innerHTML = `<div class="empty-state"><div class="ei">🎬</div><h3>No films found</h3><p>Try adjusting your filters</p></div>`;
    return;
  }
  grid.innerHTML = movies.map((m, i) => {
    const mid   = m.movie_id || m.id;
    const score = m.score != null ? Math.round(m.score * 100) : null;
    const poster = m.poster_url
      ? `<img class="movie-poster" src="${esc(m.poster_url)}" alt="${esc(m.title)}" loading="lazy"
             onerror="this.outerHTML='<div class=\\'poster-ph\\'>🎬</div>'">`
      : `<div class="poster-ph">🎬</div>`;

    const badge = isRec && score != null ? `<div class="rec-score-badge">${score}%</div>` : '';

    const pills = isRec ? `
      <div class="seat-info-pill">
        ${m.available_seats!=null ? `<span class="mini-pill mp-sage">⬛ ${m.available_seats} seats</span>` : ''}
        ${m.best_quality    ? `<span class="mini-pill mp-gold">⭐ ${m.best_quality}/10</span>` : ''}
        ${m.pref_match!=null? `<span class="mini-pill mp-rose">🎯 ${m.pref_match}% match</span>` : ''}
      </div>
      ${m.showtime ? `<div style="font-size:0.7rem;color:var(--text3);margin-top:0.35rem">${fmtDateShort(m.showtime)} · ${esc(m.cinema_name||m.hall_name||'')}</div>` : ''}` : '';

    return `<div class="movie-card" style="animation-delay:${i*0.04}s" onclick="openMovieDetail(${mid})">
      ${poster}${badge}
      <div class="movie-body">
        <div class="movie-title">${esc(m.title)}</div>
        <div class="movie-meta">
          <span class="genre-tag">${esc(m.genre)}</span>
          <span class="rating-tag">${m.rating}</span>
        </div>
        ${pills}
      </div>
    </div>`;
  }).join('');
}

// ─── RECOMMENDATIONS ───────────────────────────────────────────────────────
async function loadRecs() {
  ge('recs-grid').innerHTML = '<div class="shimmer" style="height:300px"></div>';
  const [recs, prefs] = await Promise.all([api('/api/recommendations'), api('/api/my-preferences')]);

  const pc = ge('pref-card');
  if (prefs && prefs.seat_position_pref && !prefs.error) {
    const gw = JSON.parse(prefs.genre_weights || '{}');
    const topGenre = Object.entries(gw).sort((a,b)=>b[1]-a[1])[0];
    pc.innerHTML = `
      Prefers <strong>${prefs.seat_position_pref} ${prefs.seat_zone_pref}</strong> seats ·
      Avg quality taste: <strong>${prefs.avg_quality_pref}/10</strong><br>
      ${topGenre ? `Favourite genre: <strong>${topGenre[0]}</strong>` : 'No bookings yet — watch more!'}
      · ${prefs.booking_count} booking${prefs.booking_count !== 1 ? 's' : ''}`;
  } else {
    pc.innerHTML = 'Book your first ticket to train your personal recommendations ✦';
  }

  if (!recs || recs.error || !recs.length) {
    ge('recs-grid').innerHTML = `<div class="empty-state"><div class="ei">✦</div><h3>No recommendations yet</h3><p>Book a film to unlock personalised picks based on your taste & seat preferences</p></div>`;
    return;
  }
  renderGrid(recs, 'recs-grid', true);
}

// ─── MOVIE DETAIL ──────────────────────────────────────────────────────────
async function openMovieDetail(mid) {
  openModal('movie-modal');
  ge('movie-detail').innerHTML = '<div class="shimmer" style="height:320px"></div>';
  const { movie: m, showtimes } = await api(`/api/movies/${mid}`);

  const byCinema = {};
  (showtimes || []).forEach(s => {
    const cn = s.cinema_name || s.hall_name;
    if (!byCinema[cn]) byCinema[cn] = [];
    byCinema[cn].push(s);
  });

  const stHtml = Object.entries(byCinema).map(([cinema, sts]) => `
    <div style="margin-bottom:1rem">
      <div style="font-size:0.78rem;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.5rem">${esc(cinema)}</div>
      <div style="display:flex;flex-wrap:wrap;gap:0.55rem">
        ${sts.map(s => {
          const avail = s.available_seats || 0;
          const full  = avail === 0;
          return `<button class="st-btn ${full ? 'st-full' : ''}"
                    ${full ? 'disabled' : `onclick="openSeatMap(${s.id})"`}>
            <span class="stime">${fmtTime(s.showtime)}</span>
            <span class="shall">${esc(s.hall_name)}</span>
            <span class="savail">${full ? '✕ Full' : '✓ ' + avail + ' seats'}</span>
            <span class="sprice">₦${Number(s.price).toLocaleString()}</span>
          </button>`;
        }).join('')}
      </div>
    </div>`).join('') || '<p style="color:var(--text2)">No upcoming showtimes</p>';

  ge('movie-detail').innerHTML = `
    <div class="detail-grid">
      <div>
        <img class="detail-poster" src="${esc(m.poster_url||'')}" alt="${esc(m.title)}"
             onerror="this.style.display='none'"/>
      </div>
      <div>
        <h2 class="dtitle">${esc(m.title)}</h2>
        <div class="dmeta">
          <span class="dtag rose">★ ${m.rating}</span>
          <span class="dtag">${esc(m.genre)}</span>
          <span class="dtag">${m.duration_min} min</span>
          ${m.release_year ? `<span class="dtag">${m.release_year}</span>` : ''}
          ${m.director ? `<span class="dtag">Dir. ${esc(m.director)}</span>` : ''}
        </div>
        <p class="ddesc">${esc(m.description||'')}</p>
        ${m.cast_list ? `<p style="font-size:0.78rem;color:var(--text3);margin-bottom:1.25rem">Cast: ${esc(m.cast_list)}</p>` : ''}
        <div class="sts-label">Choose a Showtime</div>
        ${stHtml}
      </div>
    </div>`;
}

// ─── SEAT MAP ──────────────────────────────────────────────────────────────
async function openSeatMap(showtimeId) {
  closeModal('movie-modal');
  openModal('seat-modal');
  ge('seat-content').innerHTML = '<div class="shimmer" style="height:400px"></div>';

  S.showtimeId   = showtimeId;
  S.selectedSeat = null;
  S.lockExpiry   = null;
  clearInterval(S.seatPoll);

  await drawSeatMap(showtimeId, false);
  S.seatPoll = setInterval(() => drawSeatMap(showtimeId, true), 8000);
}

async function drawSeatMap(showtimeId, soft) {
  const data = await api(`/api/seats/${showtimeId}`);
  if (!data || data.error) return;
  const { seats, showtime } = data;
  console.log('SEATS SAMPLE:', seats.slice(0,2), 'USER:', S.user);

  const rows = {};
  seats.forEach(s => { (rows[s.row_num] = rows[s.row_num]||[]).push(s); });
  const maxCol = Math.max(...seats.map(s => s.col_num));
  const mid    = Math.floor(maxCol / 2);

  const rowsHtml = Object.values(rows).map(row => {
    const cells = row.map((s, idx) => {
      const isSel = S.selectedSeat && S.selectedSeat.id === s.id;
      let cls = 'seat ';
      if      (isSel)               cls += 'selected';
      else if (s.my_lock)           cls += 'my-lock';
      else if (s.status==='booked') cls += 'booked';
      else if (s.status==='locked') cls += 'locked';
      else {
        // Colour by Q_obj — the objective, research-based score
        const qObj = parseFloat(s.q_obj ?? s.quality_score);
        cls += qObj >= 7.5 ? 'av-high' : qObj >= 5.0 ? 'av-mid' : 'av-low';
      }
      const clickable = s.status === 'available' || s.my_lock;
      const tags      = s.position_tags ? JSON.parse(s.position_tags) : [];
      const qObj      = s.q_obj ?? s.quality_score;
      const qPrefPct  = s.q_pref_pct ?? null;
      const gap       = idx === mid ? '<div class="seat-gap"></div>' : '';
      return `${gap}<div class="${cls}"
        ${clickable ? `onclick="selectSeat(${JSON.stringify(s).replace(/"/g,'&quot;')})"` : ''}
        onmouseenter="showTip(event,'${s.row_label}${s.seat_number}',${qObj},'${tags.join(', ')}','${s.status}',${qPrefPct})"
        onmouseleave="hideTip()"></div>`;
    }).join('');
    return `<div class="seat-row"><div class="row-lbl">${row[0].row_label}</div>${cells}<div class="row-lbl">${row[0].row_label}</div></div>`;
  }).join('');

  const totalSeats = seats.length;
  const availSeats = seats.filter(s => s.status === 'available').length;

  if (!soft) {
    ge('seat-content').innerHTML = `
      <div class="seat-map-wrap">
        <div class="seat-map-head">
          <h2>${esc(showtime.title)}</h2>
          <p>${esc(showtime.cinema_name||'')} · ${esc(showtime.hall_name)} · ${fmtDateTime(showtime.showtime)} · ₦${Number(showtime.price).toLocaleString()} per seat</p>
        </div>
        <div class="screen-wrap">
          <div class="screen-bar"></div>
          <div class="screen-txt">Screen</div>
        </div>
        <div class="seat-grid-wrap"><div id="seat-inner">${rowsHtml}</div></div>
        <div class="seat-legend">
          <div class="leg-item"><div class="leg-dot ld-high"></div>Premium ≥7.5</div>
          <div class="leg-item"><div class="leg-dot ld-mid"></div>Standard 5–7.5</div>
          <div class="leg-item"><div class="leg-dot ld-low"></div>Budget &lt;5</div>
          <div class="leg-item"><div class="leg-dot ld-sel"></div>Selected</div>
          <div class="leg-item"><div class="leg-dot ld-lkd"></div>Reserved (5 min)</div>
          <div class="leg-item"><div class="leg-dot ld-bkd"></div>Booked</div>
        </div>
        <div class="avail-info" id="avail-info">${availSeats} of ${totalSeats} seats available</div>
        <div class="seat-summary hidden" id="seat-summary">
          <div class="ss-row"><span class="ss-lbl">Seat</span><span class="ss-val" id="ss-seat">—</span></div>
          <div class="ss-row"><span class="ss-lbl">Position</span><span class="ss-val" id="ss-pos">—</span></div>
          <div class="ss-divider">Objective Score <span class="ss-badge ss-badge-gold">Research-based · same for all users</span></div>
          <div class="ss-row"><span class="ss-lbl">Q<sub>obj</sub></span><span class="ss-val" id="ss-qobj">—</span></div>
          <div class="quality-bar"><div class="q-marker" id="q-marker" style="left:50%"></div></div>
          <div class="ss-divider">Preference Score <span class="ss-badge ss-badge-rose">Personal · based on your history</span></div>
          <div class="ss-row"><span class="ss-lbl">Q<sub>pref</sub></span><span class="ss-val" id="ss-qpref">—</span></div>
          <div class="ss-row" style="margin-top:0.6rem"><span class="ss-lbl">Price</span><span class="ss-val" style="color:var(--rose)" id="ss-price">—</span></div>
          <div class="lock-timer" id="lock-timer"></div>
          <div class="book-wrap">
            <button class="btn-rose full" onclick="goToPayment()">Proceed to Payment →</button>
          </div>
        </div>
      </div>`;
      if (S.user) setTimeout(() => autoSuggestSeats(seats), 0);  // top-3 suggestion banner
  } else {
    const inner = ge('seat-inner');
    if (inner) inner.innerHTML = rowsHtml;
    const ai = ge('avail-info');
    if (ai) ai.textContent = `${availSeats} of ${totalSeats} seats available`;
  }

  if (S.selectedSeat) updateSummary(S.selectedSeat, showtime.price);
}

// Seat Tooltip
const TIP = ge('seat-tooltip');
document.addEventListener('mousemove', e => {
  if (TIP && TIP.classList.contains('show')) {
    TIP.style.left = (e.clientX + 14) + 'px';
    TIP.style.top  = (e.clientY - 36) + 'px';
  }
});
function showTip(e, label, qObj, tags, status, qPrefPct) {
  const statusMap = { available: '✓ Available', booked: '✕ Booked', locked: '⏳ Held' };
  const prefLine = (qPrefPct !== null && qPrefPct !== undefined)
    ? `<span style="color:var(--rose)">Your preference match: ${qPrefPct}%</span>`
    : '';
  TIP.innerHTML = `
    <strong>${label}</strong> · ${statusMap[status] || status}<br>
    <span style="color:var(--gold)">Objective quality (research): ${qObj}/10</span><br>
    ${prefLine}
    <span style="color:var(--text3);font-size:0.75em">${tags}</span>`;
  TIP.classList.add('show');
  TIP.style.left = (e.clientX + 14) + 'px';
  TIP.style.top  = (e.clientY - 36) + 'px';
}
function hideTip() { if (TIP) TIP.classList.remove('show'); }

function autoSuggestSeats(seats) {
  const available = seats.filter(s => s.status === 'available');
  if (!available.length) return;

  const gridWrap = document.querySelector('.seat-grid-wrap');
  console.log('gridWrap:', gridWrap); // temporary debug
  if (!gridWrap) return;

  // Score each seat: Q_obj 60% + Q_pref 40%
  const scored = available.map(s => {
    const qObj  = parseFloat(s.q_obj ?? s.quality_score ?? 0);
    const qPref = s.q_pref ?? 0;
    return { ...s, _combined: (qObj / 10) * 0.60 + qPref * 0.40 };
  });
  scored.sort((a, b) => b._combined - a._combined);
  const top3 = scored.slice(0, 3);

  // Store on window — avoids JSON serialisation inside onclick attributes
  window._suggestedSeats = top3;

  const existing = ge('auto-suggest-bar');
  if (existing) existing.remove();

  const bar = document.createElement('div');
  bar.id = 'auto-suggest-bar';
  bar.style.cssText = [
    'background: linear-gradient(135deg, #fff5f8, #fdf0ff)',
    'border: 2px solid var(--rose, #c96b7a)',
    'border-radius: 16px',
    'padding: 1rem 1.2rem',
    'margin-bottom: 1rem',
    'box-shadow: 0 4px 18px rgba(201,107,122,0.15)',
    'font-size: 0.82rem',
    'animation: suggestFadeIn 0.4s ease',
  ].join(';');

  const pillsHtml = top3.map((s, i) => {
    const tags = s.position_tags ? JSON.parse(s.position_tags) : [];
    const q    = parseFloat(s.q_obj ?? s.quality_score).toFixed(1);
    const pct  = s.q_pref_pct != null ? ` · ${s.q_pref_pct}% match` : '';
    const medals = ['🥇', '🥈', '🥉'];
    return `<button class="suggest-pill" data-idx="${i}"
      style="background: linear-gradient(135deg, #c96b7a, #a0547a);
             color:#fff; border:none; border-radius:20px;
             padding: 0.45rem 1rem; font-size:0.8rem; font-weight:600;
             cursor:pointer; margin-right:0.5rem; margin-top:0.45rem;
             box-shadow: 0 2px 8px rgba(201,107,122,0.3);
             transition: transform 0.15s, box-shadow 0.15s;
             display:inline-flex; align-items:center; gap:0.3rem"
      onmouseover="this.style.transform='translateY(-2px)';this.style.boxShadow='0 4px 14px rgba(201,107,122,0.45)'"
      onmouseout="this.style.transform='';this.style.boxShadow='0 2px 8px rgba(201,107,122,0.3)'">
      ${medals[i]} ${s.row_label}${s.seat_number} · ${tags.join(' ')} · ${q}/10${pct}
    </button>`;
  }).join('');



  

  bar.innerHTML = `
    <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.4rem">
      <span style="font-size:1.1rem">✦</span>
      <span style="font-weight:700; font-size:0.95rem; color:var(--rose,#c96b7a)">
        Top 3 Suggested Seats For You
      </span>
      <span style="margin-left:auto; background:var(--rose,#c96b7a); color:#fff;
                   font-size:0.62rem; font-weight:700; padding:2px 8px;
                   border-radius:20px; letter-spacing:0.5px">AI PICK</span>
    </div>
    <div style="color:#7a5c52; font-size:0.76rem; margin-bottom:0.6rem; padding-left:1.6rem">
      Ranked by cinema research standards + your personal seating history
    </div>
    ${pillsHtml}`;

  // Event delegation — no JSON in onclick attributes
  bar.querySelectorAll('.suggest-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const seat = window._suggestedSeats[parseInt(btn.dataset.idx)];
      if (seat) selectSeat(seat);
    });
  });

  gridWrap.insertAdjacentElement('beforebegin', bar);
}

async function selectSeat(seatObj) {
  if (!S.user) { closeSeatModal(); openModal('login-modal'); return; }

  if (S.selectedSeat && S.selectedSeat.id !== seatObj.id) {
    await api('/api/seats/unlock', { method: 'POST', body: { seat_id: S.selectedSeat.id } });
  }

  const r = await api('/api/seats/lock', {
    method: 'POST',
    body: { seat_id: seatObj.id, showtime_id: S.showtimeId }
  });
  if (r.error) { toast(r.error, 'error'); return; }

  S.selectedSeat = seatObj;
  S.lockExpiry = new Date(r.locked_until + 'Z');

  await drawSeatMap(S.showtimeId, true);
  startLockTimer();

  const data = await api(`/api/seats/${S.showtimeId}`);
  if (data.showtime) updateSummary(seatObj, data.showtime.price);
}

function updateSummary(seat, price) {
  const tags    = seat.position_tags ? JSON.parse(seat.position_tags) : [];
  const qObj    = parseFloat(seat.q_obj ?? seat.quality_score);
  const qPref   = seat.q_pref_pct;
  const summaryEl = ge('seat-summary');
  if (!summaryEl) return;
  summaryEl.classList.remove('hidden');

  ge('ss-seat').textContent  = `${seat.row_label}${seat.seat_number}`;
  ge('ss-pos').textContent   = tags.join(', ');

  // Q_obj — objective, research-based, same for every user
  const qObjLabel = qObj >= 7.5 ? '⭐ Premium' : qObj >= 5 ? '✦ Standard' : '● Budget';
  ge('ss-qobj').textContent  = `${qObj}/10 — ${qObjLabel}`;
  ge('q-marker').style.left  = `${(qObj / 10) * 100}%`;

  // Q_pref — personal, based on this user's booking history
  const prefEl = ge('ss-qpref');
  if (prefEl) {
    if (qPref !== null && qPref !== undefined) {
      prefEl.textContent = `${qPref}% match to your usual preferences`;
      prefEl.style.color = qPref >= 70 ? 'var(--sage)' : qPref >= 40 ? 'var(--gold)' : 'var(--text2)';
    } else {
      prefEl.textContent = 'Log in to see your personal preference match';
      prefEl.style.color = 'var(--text3)';
    }
  }

  ge('ss-price').textContent = `₦${Number(price).toLocaleString()}`;
}

function startLockTimer() {
  clearInterval(S.lockTick);
  const el = ge('lock-timer');
  if (!el) return;
  S.lockTick = setInterval(() => {
    if (!S.lockExpiry) { clearInterval(S.lockTick); return; }
    const rem = Math.max(0, Math.ceil((S.lockExpiry - Date.now()) / 1000));
    if (el) el.textContent = rem > 0
      ? `⏱ Seat held for ${Math.floor(rem/60)}:${String(rem%60).padStart(2,'0')}`
      : '⚠ Hold expired — please reselect';
    if (rem === 0) {
      clearInterval(S.lockTick);
      S.selectedSeat = null;
      drawSeatMap(S.showtimeId, false);
      toast('Seat hold expired', 'info');
    }
  }, 1000);
}

// ─── PAYMENT ───────────────────────────────────────────────────────────────
async function goToPayment() {
  if (!S.selectedSeat || !S.showtimeId) {
    toast('No seat selected. Please pick a seat first.', 'error');
    return;
  }

  const r = await api('/api/bookings/initiate', {
    method: 'POST',
    body: { showtime_id: S.showtimeId, seat_id: S.selectedSeat.id }
  });

  if (r.error) { toast(r.error, 'error'); return; }

  S.booking = r;
  clearInterval(S.seatPoll);

  const seat = S.selectedSeat;
  closeSeatModal();

  let tags = [];
  try {
    tags = typeof seat.position_tags === 'string'
           ? JSON.parse(seat.position_tags)
           : (seat.position_tags || []);
  } catch(e) {}

  ge('payment-content').innerHTML = `
    <h2 class="pay-title">Confirm Your Booking</h2>
    <p class="pay-sub">Review your selection before paying via Paystack</p>
    <div class="pay-row">
        <span class="pay-lbl">Seat</span>
        <span>${seat.row_label}${seat.seat_number} · ${tags.join(', ')}</span>
    </div>
    <div class="pay-row"><span class="pay-lbl">Seat Quality</span><span>${seat.quality_score}/10</span></div>
    <div class="pay-row"><span class="pay-lbl">Reference</span><span style="font-size:0.78rem">${r.payment_ref}</span></div>
    <div class="pay-row"><span class="pay-lbl">Amount</span><span class="pay-total">₦${Number(r.amount).toLocaleString()}</span></div>
    <div class="pay-actions">
      <button class="btn-rose full" onclick="launchPaystack()">Pay ₦${Number(r.amount).toLocaleString()} with Paystack</button>
      <button class="btn-outline full" onclick="closeModal('payment-modal')">Cancel</button>
    </div>
    <div class="paystack-note">🔒 Secured by Paystack · Cards, Bank Transfer & USSD accepted</div>`;

  openModal('payment-modal');
}

function launchPaystack() {
  const b = S.booking;
  if (!b) return;

  const handler = PaystackPop.setup({
    key:      window.PAYSTACK_PUBLIC_KEY,
    email:    b.email,
    amount:   b.amount_kobo,
    ref:      b.payment_ref,
    currency: 'NGN',
    metadata: {
      booking_id: b.booking_id,
      custom_fields: [
        { display_name: 'Booking ID', variable_name: 'booking_id', value: b.booking_id },
      ]
    },
    onClose: () => {
      toast('Payment window closed. Your seat hold is still active.', 'info');
      openModal('payment-modal');
    },
    callback: (response) => {
      closeModal('payment-modal');
      confirmPayment(b.booking_id, response.reference);
    }
  });

  closeModal('payment-modal');
  handler.openIframe();
}

async function confirmPayment(bookingId, paystackRef) {
  toast('Verifying payment…', 'info');
  const r = await api('/api/bookings/verify', {
    method: 'POST',
    body: { booking_id: bookingId, paystack_ref: paystackRef }
  });
  if (r.error) { toast('Verification failed: ' + r.error, 'error'); return; }
  showConfirmation(r.booking);
}

function showConfirmation(b) {
  ge('confirm-content').innerHTML = `
    <div class="confirm-wrap">
      <div class="confirm-icon">🎟</div>
      <h2 class="confirm-title">You're all set!</h2>
      <p class="confirm-sub">Your seat is confirmed. Enjoy the film ✦</p>
      <div class="confirm-details">
        <div class="cd-row"><span class="cd-lbl">Film</span><span class="cd-val">${esc(b.title)}</span></div>
        <div class="cd-row"><span class="cd-lbl">Cinema</span><span class="cd-val">${esc(b.cinema_name||b.hall_name)}</span></div>
        <div class="cd-row"><span class="cd-lbl">Showtime</span><span class="cd-val">${fmtDateTime(b.showtime)}</span></div>
        <div class="cd-row"><span class="cd-lbl">Seat</span><span class="cd-val">${b.row_label}${b.seat_number}</span></div>
        <div class="cd-row"><span class="cd-lbl">Quality</span><span class="cd-val">${b.quality_score}/10</span></div>
        <div class="cd-row"><span class="cd-lbl">Paid</span><span class="cd-val" style="color:var(--rose)">₦${Number(b.price||b.amount).toLocaleString()}</span></div>
        <div class="cd-row"><span class="cd-lbl">Ref</span><span class="cd-val" style="font-size:0.75rem">${b.paystack_ref||b.payment_ref}</span></div>
      </div>
      <button class="btn-rose full" style="margin-bottom:10px" onclick="closeModal('confirm-modal');showSection('my-bookings')">View My Tickets</button>
      <button class="btn-ghost full" onclick="closeModal('confirm-modal');showSection('browse')">Back to Movies</button>
    </div>`;
  openModal('confirm-modal');
  toast('Booking confirmed! Enjoy the show 🎬', 'success');
}

// ─── MY TICKETS (Showtime History) ─────────────────────────────────────────
async function loadTickets() {
  const list = ge('tickets-list');
  list.innerHTML = '<div class="shimmer" style="height:120px;margin-bottom:1rem;border-radius:14px"></div><div class="shimmer" style="height:120px;border-radius:14px"></div>';

  const bookings = await api('/api/my-bookings');

  if (!bookings || bookings.error) {
    list.innerHTML = `<div class="empty-state"><div class="ei">⚠</div><h3>Could not load tickets</h3><p>Please try refreshing</p></div>`;
    return;
  }

  if (!bookings.length) {
    list.innerHTML = `<div class="empty-state"><div class="ei">🎟</div><h3>No booking history yet</h3><p>Book a film and complete payment to see your tickets here</p></div>`;
    return;
  }

  const confirmed = bookings.filter(b => b.status === 'confirmed');
  const pending   = bookings.filter(b => b.status === 'pending');

  function ticketCard(b) {
    const tags = b.position_tags ? JSON.parse(b.position_tags) : [];
    const isConfirmed = b.status === 'confirmed';
    // Normalise showtime — SQLite stores without Z, JS Date needs it
    const showtimeStr = b.showtime.includes('T') ? b.showtime : b.showtime.replace(' ', 'T');
    const showtime  = new Date(showtimeStr);
    const isPast    = showtime < new Date();
    const q = parseFloat(b.quality_score || 0);
    const qLabel = q >= 7.5 ? '⭐ Premium' : q >= 5.0 ? '✦ Standard' : '● Budget';
    const ref = b.paystack_ref || b.payment_ref || '—';
    const posterEl = b.poster_url
      ? `<img class="ticket-poster" src="${esc(b.poster_url)}" alt="${esc(b.title)}" onerror="this.outerHTML='<div class=\\'ticket-poster-ph\\'>🎬</div>'">`
      : `<div class="ticket-poster-ph">🎬</div>`;

    return `<div class="ticket-card ${isConfirmed ? 'confirmed' : 'pending'} ${isPast ? 'past' : ''}">
      ${posterEl}
      <div class="ticket-body">
        <div class="ticket-film">${esc(b.title)}</div>
        <div class="ticket-genre-tag">${esc(b.genre || '')}</div>
        <div class="ticket-detail-row"><span class="ticket-detail-icon">🏛</span>${esc(b.cinema_name || b.hall_name)}</div>
        <div class="ticket-detail-row"><span class="ticket-detail-icon">📅</span>${fmtDateTime(showtimeStr)}${isPast ? ' <span style="color:var(--text3);font-size:0.72rem">(Past)</span>' : ''}</div>
        <div class="ticket-detail-row"><span class="ticket-detail-icon">💺</span>Seat ${b.row_label}${b.seat_number} · ${tags.join(', ')} · ${qLabel} (${q}/10)</div>
        <div class="ticket-detail-row"><span class="ticket-detail-icon">🏟</span>${esc(b.hall_name)}</div>
        <div class="ticket-detail-row"><span class="ticket-detail-icon">🔖</span><span class="ticket-ref">${esc(ref)}</span></div>
      </div>
      <div class="ticket-status-col">
        <div class="ticket-badge ${isConfirmed ? 'confirmed' : 'pending'}">${isConfirmed ? '✓ Confirmed' : '⏳ Pending'}</div>
        <div class="ticket-amount">₦${Number(b.price || b.amount || 0).toLocaleString()}</div>
        <div class="ticket-quality">${qLabel}</div>
        ${isPast && isConfirmed ? '<div class="ticket-past-tag">📼 Watched</div>' : ''}
      </div>
    </div>`;
  }

  let html = '';

  if (confirmed.length) {
    const upcoming = confirmed.filter(b => {
      const s = b.showtime.includes('T') ? b.showtime : b.showtime.replace(' ', 'T');
      return new Date(s) >= new Date();
    });
    const past = confirmed.filter(b => {
      const s = b.showtime.includes('T') ? b.showtime : b.showtime.replace(' ', 'T');
      return new Date(s) < new Date();
    });

    if (upcoming.length) {
      html += `<div class="tickets-group">
        <div class="ticket-section-header">🎟 Upcoming <span class="ticket-section-count">${upcoming.length} ticket${upcoming.length > 1 ? 's' : ''}</span></div>
        ${upcoming.map(ticketCard).join('')}
      </div>`;
    }
    if (past.length) {
      html += `<div class="tickets-group">
        <div class="ticket-section-header">📼 Watch History <span class="ticket-section-count">${past.length} film${past.length > 1 ? 's' : ''} watched</span></div>
        ${past.map(ticketCard).join('')}
      </div>`;
    }
  }

  if (pending.length) {
    html += `<div class="tickets-group">
      <div class="ticket-section-header">⏳ Pending Payment <span class="ticket-section-count">${pending.length}</span></div>
      <p style="font-size:0.82rem;color:var(--text2);margin-bottom:0.75rem">These bookings are awaiting payment. Complete payment or they will expire.</p>
      ${pending.map(ticketCard).join('')}
    </div>`;
  }

  if (!html) {
    html = `<div class="empty-state"><div class="ei">🎟</div><h3>No tickets yet</h3><p>Book a film and complete payment to see your tickets here</p></div>`;
  }

  list.innerHTML = html;
}

// ─── GENRE MATCHER ─────────────────────────────────────────────────────────
async function initGenreMatcher() {
  // Load all genres for checkboxes
  const genres = await api('/api/genres');
  renderMatcherForm(Array.isArray(genres) ? genres : []);
}

function renderMatcherForm(genres) {
  const wrap = ge('matcher-form-wrap');
  if (!wrap) return;

  wrap.innerHTML = `
    <div class="matcher-step">
      <div class="matcher-step-label">1 · Pick your genres</div>
      <div class="matcher-genre-grid" id="matcher-genres">
        ${genres.map(g => `
          <label class="matcher-genre-pill">
            <input type="checkbox" value="${esc(g)}" onchange="updateMatcherBtn()">
            <span>${esc(g)}</span>
          </label>`).join('')}
      </div>
    </div>

    <div class="matcher-step">
      <div class="matcher-step-label">2 · Where do you like to sit?</div>
      <div class="matcher-option-row" id="matcher-position">
        <label class="matcher-option"><input type="radio" name="pos" value="" checked onchange="updateMatcherBtn()"><span>Any position</span></label>
        <label class="matcher-option"><input type="radio" name="pos" value="center" onchange="updateMatcherBtn()"><span>🎯 Centre</span></label>
        <label class="matcher-option"><input type="radio" name="pos" value="aisle" onchange="updateMatcherBtn()"><span>↔ Aisle</span></label>
        <label class="matcher-option"><input type="radio" name="pos" value="edge" onchange="updateMatcherBtn()"><span>🪟 Edge</span></label>
      </div>
    </div>

    <div class="matcher-step">
      <div class="matcher-step-label">3 · How far from the screen?</div>
      <div class="matcher-option-row" id="matcher-zone">
        <label class="matcher-option"><input type="radio" name="zone" value="" checked onchange="updateMatcherBtn()"><span>Any row</span></label>
        <label class="matcher-option"><input type="radio" name="zone" value="front" onchange="updateMatcherBtn()"><span>⬆ Front</span></label>
        <label class="matcher-option"><input type="radio" name="zone" value="middle" onchange="updateMatcherBtn()"><span>↕ Middle</span></label>
        <label class="matcher-option"><input type="radio" name="zone" value="back" onchange="updateMatcherBtn()"><span>⬇ Back</span></label>
      </div>
    </div>

    <div class="matcher-step">
      <div class="matcher-step-label">4 · Minimum rating</div>
      <div class="matcher-option-row">
        <label class="matcher-option"><input type="radio" name="rating" value="0" checked onchange="updateMatcherBtn()"><span>Any</span></label>
        <label class="matcher-option"><input type="radio" name="rating" value="6.5" onchange="updateMatcherBtn()"><span>★ 6.5+</span></label>
        <label class="matcher-option"><input type="radio" name="rating" value="7.5" onchange="updateMatcherBtn()"><span>★ 7.5+</span></label>
        <label class="matcher-option"><input type="radio" name="rating" value="8.0" onchange="updateMatcherBtn()"><span>★ 8.0+</span></label>
      </div>
    </div>

    <button class="btn-rose matcher-find-btn" id="matcher-find-btn" onclick="runGenreMatcher()">
      Find My Films →
    </button>`;
}

function updateMatcherBtn() {
  // Always enabled — just a visual feedback hook if needed later
}

async function runGenreMatcher() {
  const btn = ge('matcher-find-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Searching…'; }

  // Collect genres
  const checkedGenres = [...document.querySelectorAll('#matcher-genres input:checked')].map(el => el.value);
  const pos    = document.querySelector('input[name="pos"]:checked')?.value    || '';
  const zone   = document.querySelector('input[name="zone"]:checked')?.value   || '';
  const rating = parseFloat(document.querySelector('input[name="rating"]:checked')?.value || '0');

  const results = await api('/api/genre-matcher', {
    method: 'POST',
    body: { genres: checkedGenres, seat_position: pos, seat_zone: zone, min_rating: rating, max_results: 12 }
  });

  if (btn) { btn.disabled = false; btn.textContent = 'Find My Films →'; }

  renderMatcherResults(results, { genres: checkedGenres, pos, zone, rating });
}

function renderMatcherResults(results, filters) {
  const wrap = ge('matcher-results');
  if (!wrap) return;

  if (!results || results.error || !results.length) {
    wrap.innerHTML = `<div class="empty-state" style="margin-top:1.5rem">
      <div class="ei">🎬</div>
      <h3>No matches found</h3>
      <p>Try selecting fewer genres or lowering the rating threshold</p>
    </div>`;
    return;
  }

  const posLabel  = filters.pos  ? filters.pos  + ' seat' : 'any position';
  const zoneLabel = filters.zone ? filters.zone + ' row'  : 'any row';

  wrap.innerHTML = `
    <div class="matcher-results-header">
      <div class="matcher-results-title">✦ ${results.length} film${results.length !== 1 ? 's' : ''} matched</div>
      <div class="matcher-results-sub">${filters.genres.length ? filters.genres.join(', ') : 'All genres'} · ${posLabel} · ${zoneLabel} · Rating ${filters.rating > 0 ? filters.rating + '+' : 'any'}</div>
    </div>
    <div class="matcher-cards">
      ${results.map((m, i) => {
        const seat = m.best_seat;
        const seatTags = seat ? (seat.position_tags ? JSON.parse(seat.position_tags) : []) : [];
        const seatLabel = seat ? `${seat.row_label}${seat.seat_number} · ${seatTags.join(', ')} · ${seat.quality_score}/10` : 'Check showtimes';
        const q = seat ? parseFloat(seat.quality_score) : 0;
        const qBadge = q >= 7.5 ? 'mp-gold' : q >= 5 ? 'mp-sage' : '';

        return `<div class="matcher-card" onclick="openMovieDetail(${m.movie_id})">
          ${m.poster_url ? `<img class="matcher-card-poster" src="${esc(m.poster_url)}" alt="${esc(m.title)}" onerror="this.style.display='none'">` : '<div class="matcher-card-poster-ph">🎬</div>'}
          <div class="matcher-card-body">
            <div class="matcher-card-rank">#${i+1}</div>
            <div class="matcher-card-title">${esc(m.title)}</div>
            <div class="matcher-card-meta">
              <span class="mini-pill mp-rose">${esc(m.genre)}</span>
              <span class="mini-pill">★ ${m.rating}</span>
              <span class="mini-pill">${m.duration_min} min</span>
            </div>
            <div class="matcher-card-seat ${qBadge}">
              💺 Best seat: ${esc(seatLabel)}
            </div>
            <div class="matcher-card-show">
              📅 ${m.showtime ? fmtDateTime(m.showtime) : '—'} · ${esc(m.cinema_name || m.hall_name || '')}
            </div>
            <div class="matcher-card-avail">
              ${m.available_seats} of ${m.total_seats} seats available (${m.avail_pct}%)
              <button class="btn-rose sm" style="margin-left:0.75rem;padding:0.25rem 0.75rem;font-size:0.75rem"
                onclick="event.stopPropagation();openSeatMap(${m.showtime_id})">Book →</button>
            </div>
          </div>
        </div>`;
      }).join('')}
    </div>`;
}

// ─── CINEMAS ───────────────────────────────────────────────────────────────
async function loadCinemas() {
  const cinemas = await api('/api/cinemas');
  if (!Array.isArray(cinemas)) return;
  const icons = ['🎬','🎭','🎞','🍿'];
  ge('cinemas-grid').innerHTML = cinemas.map((c, i) => `
    <div class="cinema-card" style="animation-delay:${i*0.07}s">
      <div style="font-size:1.6rem;margin-bottom:0.5rem">${icons[i]||'🎬'}</div>
      <div class="cinema-name">${esc(c.name)}</div>
      <div class="cinema-loc">📍 ${esc(c.location)}</div>
      <div class="cinema-addr">${esc(c.address)}</div>
    </div>`).join('');
}

// ─── THEME ─────────────────────────────────────────────────────────────────
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  applyTheme(current === 'dark' ? 'light' : 'dark');
}
function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('theme', t);
  const btn = ge('themeBtn');
  if (btn) btn.textContent = t === 'dark' ? '○' : '◑';
}

// ─── MODAL UTILS ───────────────────────────────────────────────────────────
function openModal(id) {
  const m = ge(id); if (m) m.classList.remove('hidden');
}
function closeModal(id) {
  const m = ge(id); if (m) m.classList.add('hidden');
}
function closeAllModals() {
  document.querySelectorAll('.overlay, .modal-backdrop').forEach(m => m.classList.add('hidden'));
}
function closeOverlay(e, id) { if (e.target === e.currentTarget) closeModal(id); }
function switchModal(to, from) { closeModal(from); openModal(to); }
function closeSeatModal() {
  clearInterval(S.seatPoll);
  clearInterval(S.lockTick);
  if (S.selectedSeat) {
    api('/api/seats/unlock', { method:'POST', body:{ seat_id: S.selectedSeat.id } });
    S.selectedSeat = null;
  }
  closeModal('seat-modal');
}

// ─── TOAST ─────────────────────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const icons = { success:'✓', error:'✕', info:'✦' };
  el.innerHTML = `<span>${icons[type]||'✦'}</span><span>${msg}</span>`;
  ge('toast-container').appendChild(el);
  setTimeout(() => {
    el.style.animation = 'toastOut 0.3s ease forwards';
    setTimeout(() => el.remove(), 300);
  }, 3800);
}

// ─── FORMAT UTILS ──────────────────────────────────────────────────────────
function fmtTime(dt) {
  const d = new Date(dt.includes('T') ? dt : dt.replace(' ', 'T'));
  return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
}
function fmtDateTime(dt) {
  const d = new Date(dt.includes('T') ? dt : dt.replace(' ', 'T'));
  return d.toLocaleDateString([], {weekday:'short', month:'short', day:'numeric'}) + ' · ' + fmtTime(dt);
}
function fmtDateShort(dt) {
  const d = new Date(dt.includes('T') ? dt : dt.replace(' ', 'T'));
  return d.toLocaleDateString([], {month:'short', day:'numeric'});
}
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─── DOM SHORTCUTS ─────────────────────────────────────────────────────────
function ge(id)  { return document.getElementById(id); }
function val(id) { return (ge(id)||{}).value?.trim() || ''; }
function hide(id){ const el = typeof id==='string'?ge(id):id; if(el) el.style.display='none'; }
function show(id){ const el = typeof id==='string'?ge(id):id; if(el) el.style.display=''; }
function showErr(el, msg) { el.textContent=msg; el.classList.remove('hidden'); }

// ─── API ───────────────────────────────────────────────────────────────────
async function api(url, opts={}) {
  const { method='GET', body } = opts;
  try {
    const r = await fetch(url, {
      method,
      headers: {'Content-Type':'application/json'},
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined
    });
    return await r.json();
  } catch(e) { return {error:'Network error'}; }
}
