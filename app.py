import csv
import io
import logging
import os
import sys
from datetime import datetime, date, timedelta
from functools import wraps

import bcrypt
from flask import (Flask, render_template, redirect, url_for, request,
                   flash, jsonify, make_response)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)

from database import init_db, get_db, close_db

if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    _log_dir = os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'StaffClockIn'
    )
    os.makedirs(_log_dir, exist_ok=True)
    logging.basicConfig(
        filename=os.path.join(_log_dir, 'error.log'),
        level=logging.ERROR,
        format='%(asctime)s %(levelname)s %(message)s',
    )
else:
    _base = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__,
            template_folder=os.path.join(_base, 'templates'),
            static_folder=os.path.join(_base, 'static'))
app.secret_key = os.environ.get('SECRET_KEY', 'staff-clock-in-key-v2-xk9mq2!')
app.teardown_appcontext(close_db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to continue.'
login_manager.login_message_category = 'info'

STATUSES = ['Available', 'Lunch', 'Comfort Break', 'Training', 'Meeting']
TEAMS = ['Support', 'Onboarding', 'Risk', 'Credit Control', '2nd Line']

POLICY = {
    'lunch_limit_mins': 60,
    'break_limit_mins': 30,
}

LEAVE_TYPES = [
    'Sick Leave', 'Emergency Leave', 'Holiday', 'Out of Office',
    'Parental Leave', 'Sabbatical / Extended Leave', 'Jury Duty', 'Bereavement Leave',
]

LEAVE_COLOURS = {
    'Sick Leave':                   '#e74c3c',
    'Emergency Leave':              '#c0392b',
    'Holiday':                      '#27ae60',
    'Out of Office':                '#8e44ad',
    'Parental Leave':               '#16a085',
    'Sabbatical / Extended Leave':  '#2980b9',
    'Jury Duty':                    '#d35400',
    'Bereavement Leave':            '#7f8c8d',
}

STATUS_COLOURS = {
    'Available':     '#27ae60',
    'Lunch':         '#f39c12',
    'Comfort Break': '#e67e22',
    'Training':      '#2980b9',
    'Meeting':       '#8e44ad',
    'Clocked Out':   '#95a5a6',
}


# ─── User model ───────────────────────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, row):
        self.id = row['id']
        self.username = row['username']
        self.role = row['role']
        self._is_active = bool(row['is_active'])
        self.force_password_change = bool(row['force_password_change'])
        self.team = row['team'] if row['team'] else None

    # UserMixin.is_active is a @property — must override it, not assign to it
    @property
    def is_active(self):
        return self._is_active

    def get_id(self):
        return str(self.id)


@login_manager.user_loader
def load_user(user_id):
    row = get_db().execute(
        'SELECT * FROM users WHERE id = ? AND deleted_at IS NULL', (user_id,)
    ).fetchone()
    return User(row) if row else None


# ─── Decorators ───────────────────────────────────────────────────────────────

def manager_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ('manager', 'admin'):
            flash('Manager access required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


# ─── Template filters ─────────────────────────────────────────────────────────

@app.template_filter('fmt_mins')
def fmt_mins(mins):
    if mins is None:
        return '–'
    if mins == 0:
        return '< 1m'
    h, m = divmod(int(mins), 60)
    return f'{h}h {m:02d}m' if h else f'{m}m'


@app.template_filter('fmt_dt')
def fmt_dt(s):
    if not s:
        return '–'
    return datetime.fromisoformat(s).strftime('%d/%m/%Y %H:%M')


@app.template_filter('fmt_time')
def fmt_time(s):
    if not s:
        return '–'
    return datetime.fromisoformat(s).strftime('%H:%M')


@app.template_filter('fmt_date')
def fmt_date(s):
    if not s:
        return '–'
    return datetime.fromisoformat(s).strftime('%d/%m/%Y')



@app.context_processor
def inject_globals():
    return {
        'status_colours': STATUS_COLOURS, 'statuses': STATUSES,
        'teams': TEAMS, 'POLICY': POLICY,
        'leave_types': LEAVE_TYPES, 'leave_colours': LEAVE_COLOURS,
    }


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _open_session(db, user_id):
    return db.execute(
        'SELECT * FROM sessions WHERE user_id = ? AND clock_out IS NULL',
        (user_id,)
    ).fetchone()


def _current_status(db, session_id):
    row = db.execute(
        'SELECT status, timestamp FROM status_events'
        ' WHERE session_id = ? ORDER BY timestamp DESC LIMIT 1',
        (session_id,)
    ).fetchone()
    return row


def _do_clock_out(db, session_id, user_id):
    now = datetime.now()
    row = db.execute('SELECT clock_in FROM sessions WHERE id = ?', (session_id,)).fetchone()
    clock_in_dt = datetime.fromisoformat(row['clock_in'])
    total_minutes = max(0, int((now - clock_in_dt).total_seconds() / 60))
    db.execute(
        'UPDATE sessions SET clock_out = ?, total_minutes = ? WHERE id = ?',
        (now.isoformat(), total_minutes, session_id)
    )
    db.execute(
        'INSERT INTO status_events (user_id, session_id, status, timestamp)'
        ' VALUES (?, ?, ?, ?)',
        (user_id, session_id, 'Clocked Out', now.isoformat())
    )


def _status_breakdown(events, clock_out):
    breakdown = {}
    for i, ev in enumerate(events):
        if ev['status'] == 'Clocked Out':
            continue
        start = datetime.fromisoformat(ev['timestamp'])
        if i + 1 < len(events):
            end = datetime.fromisoformat(events[i + 1]['timestamp'])
        elif clock_out:
            end = datetime.fromisoformat(clock_out)
        else:
            end = datetime.now()
        minutes = max(0, int((end - start).total_seconds() / 60))
        breakdown[ev['status']] = breakdown.get(ev['status'], 0) + minutes
    return breakdown


# ─── Core routes ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    if current_user.force_password_change:
        return redirect(url_for('change_password'))
    return redirect(url_for('manager_dashboard' if current_user.role in ('manager', 'admin') else 'agent_dashboard'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        row = db.execute(
            'SELECT * FROM users WHERE username = ? AND is_active = 1 AND deleted_at IS NULL',
            (username,)
        ).fetchone()
        if row and bcrypt.checkpw(password.encode(), row['password_hash'].encode()):
            user = User(row)
            login_user(user)
            if user.force_password_change:
                return redirect(url_for('change_password'))
            return redirect(url_for('index'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    db = get_db()
    sess = _open_session(db, current_user.id)
    if sess:
        _do_clock_out(db, sess['id'], current_user.id)
        db.commit()
    logout_user()
    return redirect(url_for('login'))


@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        new_pw = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if len(new_pw) < 8:
            flash('Password must be at least 8 characters.', 'danger')
        elif new_pw != confirm:
            flash('Passwords do not match.', 'danger')
        else:
            hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
            db = get_db()
            db.execute(
                'UPDATE users SET password_hash = ?, force_password_change = 0 WHERE id = ?',
                (hashed, current_user.id)
            )
            db.commit()
            flash('Password updated successfully.', 'success')
            return redirect(url_for('index'))
    return render_template('change_password.html')


# ─── Agent routes ─────────────────────────────────────────────────────────────

@app.route('/agent')
@login_required
def agent_dashboard():
    if current_user.force_password_change:
        return redirect(url_for('change_password'))
    db = get_db()
    sess = _open_session(db, current_user.id)
    current_status = None
    status_since = None
    minutes_in_status = 0

    if sess:
        ev = _current_status(db, sess['id'])
        if ev:
            current_status = ev['status']
            status_since = ev['timestamp']
            minutes_in_status = max(0, int(
                (datetime.now() - datetime.fromisoformat(ev['timestamp'])).total_seconds() / 60
            ))
        else:
            current_status = 'Available'

    since = (date.today() - timedelta(days=7)).isoformat()
    history = db.execute(
        '''SELECT s.id, s.clock_in, s.clock_out, s.total_minutes
           FROM sessions s
           WHERE s.user_id = ? AND date(s.clock_in) >= ?
           ORDER BY s.clock_in DESC''',
        (current_user.id, since)
    ).fetchall()

    # Group sessions by date so the agent sees one row per day
    day_groups = {}
    for h in history:
        evts = db.execute(
            'SELECT status, timestamp FROM status_events WHERE session_id = ? ORDER BY timestamp',
            (h['id'],)
        ).fetchall()
        bd = _status_breakdown(evts, h['clock_out'])
        day = h['clock_in'][:10] if h['clock_in'] else ''
        if day not in day_groups:
            day_groups[day] = {
                'clock_ins': [], 'clock_outs': [],
                'total_minutes': 0, 'breakdown': {}, 'has_open': False,
            }
        g = day_groups[day]
        if h['clock_in']:  g['clock_ins'].append(h['clock_in'])
        if h['clock_out']: g['clock_outs'].append(h['clock_out'])
        else:              g['has_open'] = True
        g['total_minutes'] += h['total_minutes'] or 0
        for s, m in bd.items():
            g['breakdown'][s] = g['breakdown'].get(s, 0) + m

    history_with_breakdown = []
    for day, g in sorted(day_groups.items(), reverse=True):
        history_with_breakdown.append({
            'clock_in':      min(g['clock_ins']) if g['clock_ins'] else None,
            'clock_out':     (max(g['clock_outs']) if g['clock_outs'] and not g['has_open'] else None),
            'total_minutes': g['total_minutes'] if not g['has_open'] else None,
            'breakdown':     g['breakdown'],
        })

    return render_template('agent_dashboard.html',
                           open_session=sess,
                           current_status=current_status,
                           status_since=status_since,
                           minutes_in_status=minutes_in_status,
                           history=history_with_breakdown)


@app.route('/agent/clock-in', methods=['POST'])
@login_required
def clock_in():
    db = get_db()
    if _open_session(db, current_user.id):
        flash('Already clocked in.', 'warning')
        return redirect(url_for('agent_dashboard'))
    now = datetime.now().isoformat()
    cur = db.execute('INSERT INTO sessions (user_id, clock_in) VALUES (?, ?)',
                     (current_user.id, now))
    db.execute(
        'INSERT INTO status_events (user_id, session_id, status, timestamp) VALUES (?, ?, ?, ?)',
        (current_user.id, cur.lastrowid, 'Available', now)
    )
    db.commit()
    return redirect(url_for('agent_dashboard'))


@app.route('/agent/clock-out', methods=['POST'])
@login_required
def clock_out():
    db = get_db()
    sess = _open_session(db, current_user.id)
    if not sess:
        flash('Not currently clocked in.', 'warning')
        return redirect(url_for('agent_dashboard'))
    _do_clock_out(db, sess['id'], current_user.id)
    db.commit()
    return redirect(url_for('agent_dashboard'))


@app.route('/agent/auto-clock-out', methods=['POST'])
@login_required
def auto_clock_out():
    db = get_db()
    sess = _open_session(db, current_user.id)
    if sess:
        _do_clock_out(db, sess['id'], current_user.id)
        db.commit()
    return '', 204


@app.route('/agent/status', methods=['POST'])
@login_required
def set_status():
    status = request.form.get('status')
    if status not in STATUSES:
        flash('Invalid status.', 'danger')
        return redirect(url_for('agent_dashboard'))
    db = get_db()
    sess = _open_session(db, current_user.id)
    if not sess:
        flash('You must clock in before setting a status.', 'warning')
        return redirect(url_for('agent_dashboard'))
    db.execute(
        'INSERT INTO status_events (user_id, session_id, status, timestamp) VALUES (?, ?, ?, ?)',
        (current_user.id, sess['id'], status, datetime.now().isoformat())
    )
    db.commit()
    return redirect(url_for('agent_dashboard'))


# ─── Manager routes ───────────────────────────────────────────────────────────

@app.route('/manager')
@login_required
@manager_required
def manager_dashboard():
    if current_user.force_password_change:
        return redirect(url_for('change_password'))
    db = get_db()
    if current_user.role == 'admin':
        agents = db.execute(
            "SELECT id, username, role, team FROM users WHERE is_active = 1 AND deleted_at IS NULL AND role != 'admin' ORDER BY username"
        ).fetchall()
    else:
        agents = db.execute(
            "SELECT id, username, role, team FROM users WHERE is_active = 1 AND deleted_at IS NULL AND role = 'agent' ORDER BY username"
        ).fetchall()

    live = []
    for agent in agents:
        sess = _open_session(db, agent['id'])
        if sess:
            ev = _current_status(db, sess['id'])
            status = ev['status'] if ev else 'Available'
            status_ts = ev['timestamp'] if ev else sess['clock_in']
            now = datetime.now()
            mins_status = max(0, int((now - datetime.fromisoformat(status_ts)).total_seconds() / 60))
            mins_shift = max(0, int((now - datetime.fromisoformat(sess['clock_in'])).total_seconds() / 60))
            live.append({
                'username': agent['username'],
                'role': agent['role'],
                'team': agent['team'],
                'status': status,
                'mins_in_status': mins_status,
                'mins_on_shift': mins_shift,
                'clock_in': sess['clock_in'],
                'clocked_in': True,
            })
        else:
            live.append({
                'username': agent['username'],
                'role': agent['role'],
                'team': agent['team'],
                'status': 'Clocked Out',
                'mins_in_status': 0,
                'mins_on_shift': 0,
                'clock_in': None,
                'clocked_in': False,
            })

    clocked_in_count = sum(1 for a in live if a['clocked_in'])
    return render_template('manager_dashboard.html', live=live,
                           clocked_in_count=clocked_in_count,
                           total_count=len(live))


@app.route('/api/live-status')
@login_required
@manager_required
def api_live_status():
    db = get_db()
    if current_user.role == 'admin':
        agents = db.execute(
            "SELECT id, username, role, team FROM users WHERE is_active = 1 AND deleted_at IS NULL AND role != 'admin' ORDER BY username"
        ).fetchall()
    else:
        agents = db.execute(
            "SELECT id, username, role, team FROM users WHERE is_active = 1 AND deleted_at IS NULL AND role = 'agent' ORDER BY username"
        ).fetchall()
    result = []
    for agent in agents:
        sess = _open_session(db, agent['id'])
        if sess:
            ev = _current_status(db, sess['id'])
            status = ev['status'] if ev else 'Available'
            status_ts = ev['timestamp'] if ev else sess['clock_in']
            now = datetime.now()
            mins_status = max(0, int((now - datetime.fromisoformat(status_ts)).total_seconds() / 60))
            mins_shift = max(0, int((now - datetime.fromisoformat(sess['clock_in'])).total_seconds() / 60))
            result.append({
                'username': agent['username'], 'role': agent['role'], 'team': agent['team'],
                'status': status, 'mins_in_status': mins_status,
                'mins_on_shift': mins_shift, 'clocked_in': True,
                'clock_in': sess['clock_in'],
            })
        else:
            result.append({
                'username': agent['username'], 'role': agent['role'], 'team': agent['team'],
                'status': 'Clocked Out', 'mins_in_status': 0,
                'mins_on_shift': 0, 'clocked_in': False, 'clock_in': None,
            })
    return jsonify(result)


# ─── User management ──────────────────────────────────────────────────────────

@app.route('/manager/users')
@login_required
@manager_required
def user_management():
    db = get_db()
    users = db.execute(
        "SELECT id, username, role, team, is_active, force_password_change FROM users WHERE deleted_at IS NULL ORDER BY role, username"
    ).fetchall()
    deleted_users = []
    if current_user.role == 'admin':
        deleted_users = db.execute(
            "SELECT id, username, role, team, deleted_at FROM users WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        ).fetchall()
    return render_template('user_management.html', users=users, deleted_users=deleted_users)


@app.route('/manager/users/add', methods=['POST'])
@login_required
@manager_required
def add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'agent')
    team = request.form.get('team', '').strip()

    if current_user.role == 'manager':
        if role not in ('manager', 'agent'):
            role = 'agent'
        team = current_user.team
    elif current_user.role == 'admin':
        if role not in ('admin', 'manager', 'agent'):
            role = 'agent'
        if role == 'admin':
            team = None
        elif team not in TEAMS:
            flash('Please select a valid team.', 'danger')
            return redirect(url_for('user_management'))

    if not username:
        flash('Username is required.', 'danger')
        return redirect(url_for('user_management'))
    if not password:
        flash('Password is required.', 'danger')
        return redirect(url_for('user_management'))
    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'danger')
        return redirect(url_for('user_management'))
    db = get_db()
    if db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone():
        flash(f'Username "{username}" already exists.', 'danger')
        return redirect(url_for('user_management'))
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db.execute(
        'INSERT INTO users (username, password_hash, role, team, is_active, force_password_change)'
        ' VALUES (?, ?, ?, ?, 1, 1)',
        (username, hashed, role, team)
    )
    db.commit()
    flash(f'User "{username}" created. They will be prompted to set a password on first login.', 'success')
    return redirect(url_for('user_management'))


@app.route('/manager/users/<int:uid>/reset-password', methods=['POST'])
@login_required
@manager_required
def reset_password(uid):
    if current_user.role == 'manager':
        target = get_db().execute("SELECT role FROM users WHERE id = ?", (uid,)).fetchone()
        if not target or target['role'] != 'agent':
            flash('Managers can only reset passwords for agents.', 'danger')
            return redirect(url_for('user_management'))
    new_pw = request.form.get('new_password', '')
    if len(new_pw) < 8:
        flash('Password must be at least 8 characters.', 'danger')
        return redirect(url_for('user_management'))
    hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
    get_db().execute(
        'UPDATE users SET password_hash = ?, force_password_change = 1 WHERE id = ?',
        (hashed, uid)
    )
    get_db().commit()
    flash('Password reset. The user will be required to set a new password on next login.', 'success')
    return redirect(url_for('user_management'))


@app.route('/manager/users/<int:uid>/delete', methods=['POST'])
@login_required
@manager_required
def delete_user(uid):
    if uid == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('user_management'))
    db = get_db()
    target = db.execute(
        "SELECT role, username FROM users WHERE id = ? AND deleted_at IS NULL", (uid,)
    ).fetchone()
    if not target:
        flash('User not found.', 'danger')
        return redirect(url_for('user_management'))
    if current_user.role == 'manager' and target['role'] != 'agent':
        flash('Managers can only delete agents.', 'danger')
        return redirect(url_for('user_management'))
    db.execute('UPDATE users SET deleted_at = ? WHERE id = ?',
               (datetime.now().isoformat(), uid))
    db.commit()
    flash(f'{target["username"]} has been deleted. Their historical data is retained in reports.', 'success')
    return redirect(url_for('user_management'))


@app.route('/manager/users/<int:uid>/restore', methods=['POST'])
@login_required
@admin_required
def restore_user(uid):
    db = get_db()
    target = db.execute(
        "SELECT username FROM users WHERE id = ? AND deleted_at IS NOT NULL", (uid,)
    ).fetchone()
    if not target:
        flash('User not found.', 'danger')
        return redirect(url_for('user_management'))
    db.execute('UPDATE users SET deleted_at = NULL, is_active = 1 WHERE id = ?', (uid,))
    db.commit()
    flash(f'{target["username"]} has been restored.', 'success')
    return redirect(url_for('user_management'))


@app.route('/manager/users/<int:uid>/reassign-team', methods=['POST'])
@login_required
@manager_required
def reassign_team(uid):
    new_team = request.form.get('team')
    if new_team not in TEAMS:
        flash('Invalid team.', 'danger')
        return redirect(url_for('user_management'))
    db = get_db()
    row = db.execute('SELECT role, username FROM users WHERE id = ?', (uid,)).fetchone()
    if not row:
        flash('User not found.', 'danger')
        return redirect(url_for('user_management'))
    if current_user.role == 'manager' and row['role'] != 'agent':
        flash('Managers can only reassign agents.', 'danger')
        return redirect(url_for('user_management'))
    db.execute('UPDATE users SET team = ? WHERE id = ?', (new_team, uid))
    db.commit()
    flash(f'Team updated for {row["username"]}.', 'success')
    return redirect(url_for('user_management'))


# ─── Leave management ─────────────────────────────────────────────────────────

@app.route('/manager/leave')
@login_required
@manager_required
def leave_management():
    db = get_db()
    if current_user.role == 'admin':
        leave_users = db.execute(
            "SELECT id, username, role, team FROM users WHERE deleted_at IS NULL ORDER BY team, username"
        ).fetchall()
        records = db.execute(
            "SELECT lr.id, lr.leave_type, lr.leave_date_from, lr.leave_date_to, lr.notes,"
            " u.id as user_id, u.username, u.team,"
            " ab.username as applied_by_name"
            " FROM leave_records lr"
            " JOIN users u ON u.id = lr.user_id"
            " JOIN users ab ON ab.id = lr.applied_by"
            " ORDER BY lr.leave_date_from DESC"
        ).fetchall()
    else:
        leave_users = db.execute(
            "SELECT id, username, role, team FROM users"
            " WHERE deleted_at IS NULL AND (team = ? OR id = ?) ORDER BY username",
            (current_user.team, current_user.id)
        ).fetchall()
        records = db.execute(
            "SELECT lr.id, lr.leave_type, lr.leave_date_from, lr.leave_date_to, lr.notes,"
            " u.id as user_id, u.username, u.team,"
            " ab.username as applied_by_name"
            " FROM leave_records lr"
            " JOIN users u ON u.id = lr.user_id"
            " JOIN users ab ON ab.id = lr.applied_by"
            " WHERE u.team = ? OR u.id = ?"
            " ORDER BY lr.leave_date_from DESC",
            (current_user.team, current_user.id)
        ).fetchall()
    return render_template('leave_management.html',
                           leave_users=leave_users, records=records)


@app.route('/manager/leave/apply', methods=['POST'])
@login_required
@manager_required
def apply_leave():
    uid        = request.form.get('user_id', type=int)
    leave_type = request.form.get('leave_type', '').strip()
    date_from  = request.form.get('date_from', '').strip()
    date_to    = request.form.get('date_to', '').strip()
    notes      = request.form.get('notes', '').strip()

    if not uid or leave_type not in LEAVE_TYPES or not date_from or not date_to:
        flash('Please fill in all required fields.', 'danger')
        return redirect(url_for('leave_management'))
    if date_to < date_from:
        flash('End date must be on or after start date.', 'danger')
        return redirect(url_for('leave_management'))

    db = get_db()
    target = db.execute(
        "SELECT id, team, role FROM users WHERE id = ? AND deleted_at IS NULL", (uid,)
    ).fetchone()
    if not target:
        flash('User not found.', 'danger')
        return redirect(url_for('leave_management'))
    if current_user.role == 'manager':
        if target['team'] != current_user.team and target['id'] != current_user.id:
            flash('You can only apply leave for your own team or yourself.', 'danger')
            return redirect(url_for('leave_management'))

    db.execute(
        "INSERT INTO leave_records (user_id, leave_type, leave_date_from, leave_date_to, applied_by, applied_at, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uid, leave_type, date_from, date_to, current_user.id, datetime.now().isoformat(), notes or None)
    )
    db.commit()
    flash('Leave applied.', 'success')
    return redirect(url_for('leave_management'))


@app.route('/manager/leave/<int:lid>/delete', methods=['POST'])
@login_required
@manager_required
def delete_leave(lid):
    db = get_db()
    record = db.execute(
        "SELECT lr.id, u.team, u.id as user_id FROM leave_records lr"
        " JOIN users u ON u.id = lr.user_id WHERE lr.id = ?", (lid,)
    ).fetchone()
    if not record:
        flash('Leave record not found.', 'danger')
        return redirect(url_for('leave_management'))
    if current_user.role == 'manager':
        if record['team'] != current_user.team and record['user_id'] != current_user.id:
            flash('Access denied.', 'danger')
            return redirect(url_for('leave_management'))
    db.execute('DELETE FROM leave_records WHERE id = ?', (lid,))
    db.commit()
    flash('Leave record deleted.', 'success')
    return redirect(url_for('leave_management'))


# ─── Reports ──────────────────────────────────────────────────────────────────

@app.route('/manager/reports')
@login_required
@manager_required
def reports():
    db = get_db()
    date_from = request.args.get('date_from', (date.today() - timedelta(days=7)).isoformat())
    date_to   = request.args.get('date_to',   date.today().isoformat())
    user_filter = request.args.get('user_id', '')
    team_filter = request.args.get('team', '')

    if current_user.role == 'admin':
        users = db.execute(
            "SELECT id, username, team FROM users WHERE role = 'agent' ORDER BY team, username"
        ).fetchall()
    else:
        users = db.execute(
            "SELECT id, username, team FROM users WHERE role = 'agent' AND team = ? ORDER BY username",
            (current_user.team,)
        ).fetchall()

    query = (
        "SELECT s.id, u.username, u.team, s.clock_in, s.clock_out, s.total_minutes"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE date(s.clock_in) BETWEEN ? AND ? AND u.role = 'agent'"
    )
    params = [date_from, date_to]
    if team_filter:
        query += ' AND u.team = ?'
        params.append(team_filter)
    elif current_user.role == 'manager':
        query += ' AND u.team = ?'
        params.append(current_user.team)
    if user_filter:
        query += ' AND s.user_id = ?'
        params.append(user_filter)
    query += ' ORDER BY s.clock_in DESC'
    rows = db.execute(query, params).fetchall()

    # Group sessions by (username, date) so one row = one agent-day
    groups = {}
    for row in rows:
        evts = db.execute(
            'SELECT status, timestamp FROM status_events WHERE session_id = ? ORDER BY timestamp',
            (row['id'],)
        ).fetchall()
        bd = _status_breakdown(evts, row['clock_out'])
        key = (row['username'], row['clock_in'][:10] if row['clock_in'] else '')
        if key not in groups:
            groups[key] = {
                'username': row['username'],
                'team': row['team'],
                'clock_ins': [],
                'clock_outs': [],
                'total_minutes': 0,
                'breakdown': {},
                'has_open': False,
            }
        g = groups[key]
        if row['clock_in']:
            g['clock_ins'].append(row['clock_in'])
        if row['clock_out']:
            g['clock_outs'].append(row['clock_out'])
        else:
            g['has_open'] = True
        g['total_minutes'] += row['total_minutes'] or 0
        for s, m in bd.items():
            g['breakdown'][s] = g['breakdown'].get(s, 0) + m

    report_rows = []
    weekly = {}
    chart_statuses = {}
    chart_daily = {}

    for (username, date_key), g in groups.items():
        clock_in  = min(g['clock_ins'])  if g['clock_ins']  else None
        clock_out = max(g['clock_outs']) if g['clock_outs'] and not g['has_open'] else None
        mins = g['total_minutes']
        # Policy breach flags
        flags = []
        lunch_mins = g['breakdown'].get('Lunch', 0)
        break_mins = g['breakdown'].get('Comfort Break', 0)
        if lunch_mins > POLICY['lunch_limit_mins']:
            over = lunch_mins - POLICY['lunch_limit_mins']
            flags.append(f"Lunch {lunch_mins}m — {over}m over 1h limit")
        if break_mins > POLICY['break_limit_mins']:
            over = break_mins - POLICY['break_limit_mins']
            flags.append(f"Comfort break {break_mins}m — {over}m over 30m limit")
        report_rows.append({
            'username':      username,
            'team':          g['team'],
            'clock_in':      clock_in,
            'clock_out':     clock_out,
            'total_minutes': mins,
            'in_progress':   g['has_open'],
            'breakdown':     g['breakdown'],
            'flags':         flags,
        })
        weekly[username] = weekly.get(username, 0) + mins
        for s, m in g['breakdown'].items():
            if m > 0:
                chart_statuses[s] = chart_statuses.get(s, 0) + m
        if date_key:
            chart_daily[date_key] = chart_daily.get(date_key, 0) + mins

    # Also pull in leave records that overlap the date range
    leave_query = (
        "SELECT lr.id, u.username, u.team, lr.leave_type,"
        " lr.leave_date_from, lr.leave_date_to, lr.notes"
        " FROM leave_records lr JOIN users u ON u.id = lr.user_id"
        " WHERE lr.leave_date_from <= ? AND lr.leave_date_to >= ?"
        " AND u.deleted_at IS NULL"
    )
    leave_params = [date_to, date_from]
    if team_filter:
        leave_query += ' AND u.team = ?'
        leave_params.append(team_filter)
    elif current_user.role == 'manager':
        leave_query += ' AND (u.team = ? OR u.id = ?)'
        leave_params.extend([current_user.team, current_user.id])
    if user_filter:
        leave_query += ' AND lr.user_id = ?'
        leave_params.append(user_filter)
    for lr in db.execute(leave_query, leave_params).fetchall():
        report_rows.append({
            'username': lr['username'], 'team': lr['team'],
            'clock_in': lr['leave_date_from'], 'clock_out': None,
            'total_minutes': None, 'in_progress': False,
            'breakdown': {}, 'flags': [],
            'is_leave': True,
            'leave_type': lr['leave_type'],
            'leave_date_from': lr['leave_date_from'],
            'leave_date_to': lr['leave_date_to'],
            'notes': lr['notes'] or '',
        })

    report_rows.sort(key=lambda r: r['clock_in'] or '', reverse=True)
    chart_daily = dict(sorted(chart_daily.items()))
    total_hours = sum(weekly.values()) / 60
    avg_hours   = total_hours / len(weekly) if weekly else 0

    user_teams = {r['username']: r['team'] for r in report_rows}

    return render_template('reports.html',
                           users=users, report_rows=report_rows, weekly=weekly,
                           user_teams=user_teams,
                           date_from=date_from, date_to=date_to,
                           user_filter=user_filter, team_filter=team_filter,
                           statuses_list=STATUSES,
                           chart_statuses=chart_statuses, chart_daily=chart_daily,
                           total_shifts=len(report_rows),
                           total_hours=total_hours, avg_hours=avg_hours)


@app.route('/manager/reports/export')
@login_required
@manager_required
def export_csv():
    db = get_db()
    date_from   = request.args.get('date_from', (date.today() - timedelta(days=7)).isoformat())
    date_to     = request.args.get('date_to',   date.today().isoformat())
    user_filter = request.args.get('user_id', '')
    team_filter = request.args.get('team', '')

    query = (
        "SELECT s.id, u.username, u.team, s.clock_in, s.clock_out, s.total_minutes"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE date(s.clock_in) BETWEEN ? AND ? AND u.role = 'agent'"
    )
    params = [date_from, date_to]
    if team_filter:
        query += ' AND u.team = ?'
        params.append(team_filter)
    elif current_user.role == 'manager':
        query += ' AND u.team = ?'
        params.append(current_user.team)
    if user_filter:
        query += ' AND s.user_id = ?'
        params.append(user_filter)
    query += ' ORDER BY s.clock_in'
    rows = db.execute(query, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Username', 'Clock In', 'Clock Out', 'Total Minutes', 'Total Hours',
        *STATUSES
    ])
    for row in rows:
        evts = db.execute(
            'SELECT status, timestamp FROM status_events WHERE session_id = ? ORDER BY timestamp',
            (row['id'],)
        ).fetchall()
        bd = _status_breakdown(evts, row['clock_out'])
        writer.writerow([
            row['username'],
            row['clock_in'],
            row['clock_out'] or '',
            row['total_minutes'] or 0,
            round((row['total_minutes'] or 0) / 60, 2),
            *[bd.get(s, 0) for s in STATUSES],
        ])

    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv'
    resp.headers['Content-Disposition'] = (
        f'attachment; filename=attendance_{date_from}_to_{date_to}.csv'
    )
    return resp


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
