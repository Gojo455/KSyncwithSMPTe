# AbujaCine — Seat-Aware Cinema Reservation & Recommendation System

**Final Year Project | Computer Science | Abuja, Nigeria**
Built with Flask · SQLite · Paystack · Hybrid Recommender Engine

---

## What This System Does

AbujaCine is a full-stack web application that lets users in Abuja browse
films, select seats with quality scores derived from cinema engineering
standards, pay via Paystack, and receive personalised movie recommendations
that improve with every booking.

It serves four real Abuja cinema venues:
- Silverbird Cinemas — Central Business District
- Genesis Cinemas — Ceddi Plaza, Central Area
- Ozone Cinemas — Jabi Lake Mall
- FilmHouse Cinemas — Lugbe Dunamis Mall

---

## Quick Start (Run Locally)

### 1. Requirements
- Python 3.8 or higher
- pip

### 2. Install dependencies
```bash
pip install flask gunicorn requests
```

### 3. Delete any old database (important after updates)
```bash
# Windows
del instance\cinema.db

# Mac / Linux
rm instance/cinema.db
```

### 4. Run the application
```bash
python app.py
```

### 5. Open in browser
```
http://localhost:5000
```

---

## Default Login Accounts

| Role  | Username | Password  |
|-------|----------|-----------|
| Admin | admin    | admin123  |
| Demo  | demo     | demo123   |

Admin panel: `http://localhost:5000/admin`

---

## Project Structure

```
KSyncwithSMPTe-main/
│
├── app.py                          ← Main Flask application (all backend logic)
├── requirements.txt                ← Python dependencies
├── README.md                       ← This file
│
├── templates/
│   ├── index.html                  ← Main user-facing page
│   └── admin.html                  ← Admin dashboard page
│
├── static/
│   ├── css/
│   │   ├── main.css                ← User interface styles
│   │   └── admin.css               ← Admin panel styles
│   └── js/
│       ├── app.js                  ← User interface logic (Vanilla JS)
│       └── admin.js                ← Admin panel logic
│
├── instance/
│   └── cinema.db                   ← SQLite database (auto-created on first run)
│
├── Unit Test script for KSync.py           ← End-to-end API test
├── Unit test for Jaccrd similarity.py      ← Jaccard similarity unit test
└── Unit test for hash_password.py          ← Password hashing unit test
```

---

## Architecture Overview

### Three-Tier Architecture

```
CLIENT LAYER
  Web Browser (Vanilla JS / HTML / CSS)
  Admin Panel (admin.js)
  Paystack Inline SDK (external)
        ↕ REST API / JSON
APPLICATION LAYER
  Flask (Python 3)
  ├── Auth Module         — PBKDF2-SHA256 password hashing, session management
  ├── Booking Engine      — Seat locking (300s TTL), Paystack integration
  ├── Seat Scorer         — Q_obj (SMPTE EG-18-1994) + Q_pref (user preference)
  └── Recommender         — 7-component hybrid scoring engine
        ↕ SQL / PRAGMA foreign_keys=ON / WAL journal mode
DATA LAYER
  SQLite (cinema.db)
  ├── users               — PBKDF2 hashed passwords, is_verified flag
  ├── movies              — 12 real films from TMDB
  ├── cinemas             — 4 real Abuja venues
  ├── halls               — 8 configured screening halls
  ├── showtimes           — 5 days × 5 daily slots
  ├── seats               — Q_obj pre-computed at showtime creation
  ├── bookings            — Paystack payment references
  ├── user_preferences    — Genre weights + seat preference profile
  └── email_verifications — Token-based email verification
```

---

## Key Technical Features

### Dual Seat Scoring (Original Contribution)

Two entirely separate scores are computed for every seat:

**Q_obj — Objective Quality Score**
- Computed once at showtime creation, stored in the database
- Derived from SMPTE EG-18-1994 viewing angle specifications
- Row score (60% weight): optimal zone is 40–65% back from the screen
- Column score (40% weight): ITU-R BT.2022 lateral angle ≤ 30°
- Identical for every user looking at the same seat
- Scale: 0.5 – 10.0

**Q_pref — Subjective Preference Match**
- Computed live on every seat map request
- Compares seat position tags against the user's booking history
- Position match (40%) + Zone match (30%) + Quality proximity (30%)
- Different for every user looking at the same seat
- Scale: 0.0 – 1.0

### Hybrid Recommendation Engine (7 Components)

| Component   | Signal Type           | Returning User | New User |
|-------------|----------------------|----------------|----------|
| genre_s     | Content-based        | 0.20           | 0.00     |
| collab      | Collaborative (Jaccard)| 0.15         | 0.00     |
| pref_s      | Seat preference match| 0.20           | 0.00     |
| quality_s   | Objective seat quality| 0.15          | 0.35     |
| avail_s     | Seat availability    | 0.10           | 0.20     |
| time_s      | Showtime proximity   | 0.10           | 0.15     |
| rating_s    | TMDB rating          | 0.10           | 0.30     |
| **TOTAL**   |                      | **1.00**       | **1.00** |

New users automatically receive the redistributed weight profile so
recommendations are still meaningful before any booking history exists.

Penalties applied after weighted sum:
- Best available seat Q_obj < 3.0 → score × 0.65
- Availability ratio < 5% → score × 0.55

### Security Implementation
- Passwords: PBKDF2-SHA256, 100,000 iterations, random 16-byte salt per user
- Anti-enumeration: login errors return identical message for unknown user vs wrong password
- Session: SAMESITE=Lax, HTTPONLY=True
- Paystack secret key loaded from environment variable, never exposed client-side
- Email verification: token-based, stored in email_verifications table

### Seat Locking
- 300-second (5-minute) hold placed on selected seat before payment
- Uses UTC timestamps throughout to avoid timezone errors
- Lock expiry cleared on every seat map request (eventually consistent)
- User cannot hold more than one seat per showtime simultaneously

---

## API Endpoints Reference

### Authentication
| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| POST   | /api/register       | Register new account, returns verification token |
| POST   | /api/verify-email   | Confirm email with token           |
| POST   | /api/login          | Login (username or email accepted) |
| POST   | /api/logout         | Clear session                      |
| GET    | /api/me             | Current session info               |

### Movies & Showtimes
| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| GET    | /api/movies         | List all active movies             |
| GET    | /api/movies/[id]    | Movie detail + upcoming showtimes  |
| GET    | /api/genres         | All distinct genre tokens          |
| POST   | /api/genre-matcher  | Rule-based genre + seat filter     |

### Seats & Bookings
| Method | Endpoint            | Description                        |
|--------|---------------------|------------------------------------|
| GET    | /api/seats/[sid]    | Seat map with Q_obj + Q_pref scores |
| POST   | /api/seats/lock     | Lock seat for 300 seconds          |
| POST   | /api/seats/unlock   | Release held seat                  |
| POST   | /api/bookings/initiate | Create pending booking, get Paystack keys |
| POST   | /api/bookings/verify   | Confirm payment, trigger preference learning |
| GET    | /api/my-bookings    | User's booking history             |

### Admin (admin session required)
| Method | Endpoint                | Description               |
|--------|-------------------------|---------------------------|
| GET    | /api/admin/stats        | Dashboard totals          |
| GET/POST | /api/admin/movies     | List or add movies        |
| PUT/DELETE | /api/admin/movies/[id] | Edit or deactivate movie |
| GET/POST | /api/admin/showtimes  | List or add showtimes     |
| GET    | /api/admin/bookings     | All confirmed bookings    |
| GET    | /api/admin/analytics    | Genre / movie / cinema breakdown |

---

## Paystack Integration

Payment is handled via Paystack Inline JS (client-side popup).

**Test card for demos:**
- Card number: `4084 0840 8408 4081`
- Expiry: any future date
- CVV: any 3 digits

The system uses Paystack test keys. To use real keys, set these
environment variables before running:

```bash
# Windows
set PAYSTACK_SECRET_KEY=sk_live_your_key_here
set PAYSTACK_PUBLIC_KEY=pk_live_your_key_here

# Mac / Linux
export PAYSTACK_SECRET_KEY=sk_live_your_key_here
export PAYSTACK_PUBLIC_KEY=pk_live_your_key_here
```

---

## Running Tests

```bash
# End-to-end API test (requires the app to be running on port 5000)
python "Unit Test script for KSync.py"

# Jaccard similarity unit test (standalone, no server needed)
python "Unit test for Jaccrd similarity.py"

# Password hashing unit test (standalone, no server needed)
python "Unit test for hash_password.py"
```

---

## Design Decisions & Trade-offs

| Decision | Chosen Approach | Reason | Production Alternative |
|---|---|---|---|
| Database | SQLite | Zero-config, portable, no server process | PostgreSQL with connection pooling |
| JS Framework | Vanilla JS | Auditable without build tools — correct for academic submission | React or HTMX |
| Genre storage | Compound string `Action · Comedy` | Simpler seeding and queries at this scale | Normalised genres table + junction table |
| Email verification | Token in API response | No SMTP dependency for demo | SendGrid / AWS SES — no structural changes needed |
| Collaborative filter | Jaccard on genre tokens | Interpretable and correct for sparse data | Matrix factorisation (SVD) at scale |
| Concurrency | SQLite WAL mode | Allows concurrent reads alongside writes | PostgreSQL for high concurrent write load |

---

## Standards Referenced

- **SMPTE EG-18-1994** — Cinema viewing angle specifications (seat row scoring)
- **ITU-R BT.2022** — Maximum lateral viewing angle ≤ 30° (seat column scoring)
- **NIST SP 800-132** — Password-based key derivation (PBKDF2 implementation)
- **Paystack API v2** — Payment processing and inline popup integration

---

## Academic Context

This project was submitted as a final year capstone for a Bachelor of Science
in Computer Science. The original contributions are:

1. The dual Q_obj / Q_pref seat scoring architecture that separates
   objective engineering standards from subjective user preference
2. The 7-component hybrid recommendation engine with automatic
   cold-start weight redistribution for new users
3. The integration of SMPTE EG-18-1994 and ITU-R BT.2022 standards
   into a working seat quality scoring function

---

*AbujaCine — Built for Abuja. Designed to scale.*
