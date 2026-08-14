import os
import sys
import sqlite3
import bcrypt
from flask import g

if getattr(sys, 'frozen', False):
    _data_dir = os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'StaffClockIn'
    )
    os.makedirs(_data_dir, exist_ok=True)
    DB_PATH = os.path.join(_data_dir, 'attendance.db')
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attendance.db')


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'agent',
            is_active INTEGER NOT NULL DEFAULT 1,
            force_password_change INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            clock_in TEXT NOT NULL,
            clock_out TEXT,
            total_minutes INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS status_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_status_session ON status_events(session_id);
    ''')
    existing = db.execute('SELECT id FROM users WHERE username = ?', ('admin',)).fetchone()
    if not existing:
        hashed = bcrypt.hashpw(b'admin123', bcrypt.gensalt()).decode()
        db.execute(
            'INSERT INTO users (username, password_hash, role, is_active, force_password_change)'
            ' VALUES (?, ?, ?, 1, 1)',
            ('admin', hashed, 'manager')
        )
    db.commit()
    db.close()
