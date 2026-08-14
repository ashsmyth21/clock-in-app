# Staff Clock-In

A standalone desktop app for staff attendance tracking. Built with Python (Flask) + SQLite + Electron. All data is stored locally — no internet connection required.

---

## Features

| Role | Capabilities |
|------|-------------|
| **Agent** | Clock in/out, set status, view own shift history (7 days) |
| **Manager** | Live dashboard, user management, date-range reports, CSV export |

**Agent statuses:** Available · Lunch · Comfort Break · Training · Meeting · Back Soon

---

## Prerequisites

| Tool | Minimum version | Download |
|------|----------------|---------|
| Python | 3.10 | https://www.python.org/downloads/ |
| Node.js | 18 | https://nodejs.org/ |
| npm | 9 (bundled with Node.js) | — |

---

## Setup

### 1 — Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2 — Install Node dependencies

```bash
npm install
```

### 3 — Start the app

```bash
npm start
```

Electron will launch, start the Flask backend automatically, and open the app window.

---

## Default login

| Username | Password | Role |
|----------|----------|------|
| `admin` | `admin123` | Manager |

> **Important:** You will be prompted to set a new password the first time you log in.

---

## First-run behaviour

On the very first launch, `attendance.db` is created automatically in the project folder. The default `admin` account is seeded if it does not already exist.

---

## Building a distributable

### Windows `.exe`

```bash
npm run build-win
```

Output: `dist/Staff Clock-In Setup.exe`

> **Note:** The installer packages the Electron shell only. Python and the Flask dependencies must still be installed separately on each machine, or you can bundle a Python runtime using tools such as [PyInstaller](https://pyinstaller.org/) to produce a standalone `app.py` executable and point `main.js` at it.

### macOS `.dmg`

```bash
npm run build-mac
```

### Linux AppImage

```bash
npm run build-linux
```

---

## Project structure

```
clock-in-app/
├── app.py            Flask application (routes, auth, API)
├── database.py       SQLite initialisation and connection helper
├── requirements.txt  Python dependencies
├── main.js           Electron entry — spawns Flask, opens window
├── package.json      Node dependencies and build config
├── static/
│   └── style.css     All styles (no CDN, fully offline)
└── templates/
    ├── base.html
    ├── login.html
    ├── change_password.html
    ├── agent_dashboard.html
    ├── manager_dashboard.html
    ├── user_management.html
    └── reports.html
```

---

## Database schema

```sql
users         (id, username, password_hash, role, is_active, force_password_change, created_at)
sessions      (id, user_id, clock_in, clock_out, total_minutes)
status_events (id, user_id, session_id, status, timestamp)
```

---

## Running Flask standalone (without Electron)

Useful for debugging:

```bash
python app.py
```

Then open `http://127.0.0.1:5000` in any browser.

---

## Security notes

- Passwords are hashed with bcrypt (cost factor 12).
- The Flask secret key is set via the `SECRET_KEY` environment variable. For production use, set this to a long random string.
- The Flask server binds to `127.0.0.1` only — it is not reachable from other machines on the network.
