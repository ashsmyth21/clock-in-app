"""One-time user import script. Run once on PythonAnywhere then delete."""
import os, sqlite3, bcrypt

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'attendance.db')

USERS = [
    ('adam rose',               '2nd Line'),
    ('owen forde',              '2nd Line'),
    ('Jules Tranter',           'Onboarding'),
    ('Jupleen Bains',           'Onboarding'),
    ('Kai Flack',               'Onboarding'),
    ('Ben Smith',               'Onboarding'),
    ('Mohammed Al-Firas',       'Onboarding'),
    ('Elisabeth Morrell',       'Onboarding'),
    ('Leah Almeida',            'Risk'),
    ('Kyle Birch',              'Risk'),
    ('Devon Pellow',            'Risk'),
    ('Scarlett Harrington',     'Risk'),
    ('Huma Khan',               'Risk'),
    ('adith natarajan',         'Risk'),
    ('Talula Walker',           'Credit Control'),
    ('Nathan Khusal',           'Credit Control'),
    ('Jasmin Hughes',           'Credit Control'),
    ('ben atkins',              'Support'),
    ('daisy everton-whittard',  'Support'),
    ('galina teshovska',        'Support'),
    ('hannah warbey',           'Support'),
    ('holly prior',             'Support'),
    ('jack mehlin',             'Support'),
    ('john morada',             'Support'),
    ('kludia stepien',          'Support'),
    ('pedro rosselli',          'Support'),
    ('usman malik',             'Support'),
    ('vagner de souza',         'Support'),
    ('varnon crasto',           'Support'),
]

pw_hash = bcrypt.hashpw(b'tr4n5p0rt', bcrypt.gensalt()).decode()

db = sqlite3.connect(DB_PATH)
added, skipped = 0, 0
for username, team in USERS:
    existing = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    if existing:
        print(f'  SKIP (already exists): {username}')
        skipped += 1
    else:
        db.execute(
            'INSERT INTO users (username, password_hash, role, team, is_active, force_password_change)'
            ' VALUES (?, ?, ?, ?, 1, 1)',
            (username, pw_hash, 'agent', team)
        )
        print(f'  ADDED: {username} → {team}')
        added += 1
db.commit()
db.close()
print(f'\nDone. {added} added, {skipped} skipped.')
