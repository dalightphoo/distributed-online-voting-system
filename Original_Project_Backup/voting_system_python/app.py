import os
import re
import string
import random
import sqlite3
import datetime
import configparser
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import bcrypt
from config import Config
from werkzeug.utils import secure_filename
import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import io
import base64

app = Flask(__name__)
app.config.from_object(Config)

def get_db():
    conn = sqlite3.connect(app.config['DB_PATH'])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            firstname TEXT NOT NULL,
            lastname TEXT NOT NULL,
            photo TEXT DEFAULT '',
            created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS voters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voters_id TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            firstname TEXT NOT NULL,
            lastname TEXT NOT NULL,
            photo TEXT DEFAULT '',
            has_voted INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS elections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            deadline TIMESTAMP NOT NULL,
            created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            election_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            max_vote INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (election_id)
                REFERENCES elections(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            firstname TEXT NOT NULL,
            lastname TEXT NOT NULL,
            photo TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            biography TEXT DEFAULT '',
            FOREIGN KEY (position_id)
                REFERENCES positions(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voters_id INTEGER NOT NULL,
            election_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            position_id INTEGER NOT NULL,
            FOREIGN KEY (voters_id)
                REFERENCES voters(id)
                ON DELETE CASCADE,
            FOREIGN KEY (election_id)
                REFERENCES elections(id)
                ON DELETE CASCADE,
            FOREIGN KEY (candidate_id)
                REFERENCES candidates(id)
                ON DELETE CASCADE,
            FOREIGN KEY (position_id)
                REFERENCES positions(id)
                ON DELETE CASCADE
        );
    ''')

    # ---------------------------------------------------------
    # DATABASE MIGRATION FOR EXISTING DATABASES
    # ---------------------------------------------------------

    voter_columns = {
        row['name']
        for row in cursor.execute("PRAGMA table_info(voters)").fetchall()
    }

    if 'has_voted' not in voter_columns:
        cursor.execute("""
            ALTER TABLE voters
            ADD COLUMN has_voted INTEGER NOT NULL DEFAULT 0
        """)

    candidate_columns = {
        row['name']
        for row in cursor.execute("PRAGMA table_info(candidates)").fetchall()
    }

    if 'biography' not in candidate_columns:
        cursor.execute("""
            ALTER TABLE candidates
            ADD COLUMN biography TEXT DEFAULT ''
        """)

    election_columns = {
        row['name']
        for row in cursor.execute("PRAGMA table_info(elections)").fetchall()
    }

    # Existing installations may not have elections yet.
    if not election_columns:
        cursor.execute("""
            CREATE TABLE elections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                deadline TIMESTAMP NOT NULL,
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    position_columns = {
        row['name']
        for row in cursor.execute("PRAGMA table_info(positions)").fetchall()
    }

    if 'election_id' not in position_columns:
        cursor.execute("""
            ALTER TABLE positions
            ADD COLUMN election_id INTEGER
        """)

    vote_columns = {
        row['name']
        for row in cursor.execute("PRAGMA table_info(votes)").fetchall()
    }

    if 'election_id' not in vote_columns:
        cursor.execute("""
            ALTER TABLE votes
            ADD COLUMN election_id INTEGER
        """)

    # ---------------------------------------------------------
    # CREATE DEFAULT ELECTION ONLY IF NONE EXISTS
    # ---------------------------------------------------------

    election_count = cursor.execute(
        "SELECT COUNT(*) FROM elections"
    ).fetchone()[0]

    if election_count == 0:
        cursor.execute("""
            INSERT INTO elections (title, deadline)
            VALUES (?, ?)
        """, (
            'Original Election',
            datetime.datetime.now() + datetime.timedelta(days=3650)
        ))

    default_election = cursor.execute("""
        SELECT id
        FROM elections
        ORDER BY id
        LIMIT 1
    """).fetchone()

    default_election_id = default_election['id']

    # Assign old positions to the original election.
    cursor.execute("""
        UPDATE positions
        SET election_id = ?
        WHERE election_id IS NULL
    """, (default_election_id,))

    # Assign old votes to the original election.
    cursor.execute("""
        UPDATE votes
        SET election_id = ?
        WHERE election_id IS NULL
    """, (default_election_id,))

    # ---------------------------------------------------------
    # DEFAULT ADMIN
    # ---------------------------------------------------------

    admin_count = cursor.execute(
        "SELECT COUNT(*) FROM admin"
    ).fetchone()[0]

    if admin_count == 0:
        hashed = bcrypt.hashpw(
            b'admin123',
            bcrypt.gensalt()
        ).decode('utf-8')

        cursor.execute("""
            INSERT INTO admin
                (username, password, firstname, lastname, photo)
            VALUES (?, ?, ?, ?, ?)
        """, (
            'admin',
            hashed,
            'Admin',
            'User',
            ''
        ))

    conn.commit()
    conn.close()

def slugify(text):
    preps = ['in', 'at', 'on', 'by', 'into', 'off', 'onto', 'from', 'to', 'with', 'a', 'an', 'the', 'using', 'for']
    pattern = r'\b(?:' + '|'.join(preps) + r')\b'
    text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    text = re.sub(r'[^\w\s-]', '', text)
    text = text.strip().lower()
    text = re.sub(r'[-\s]+', '-', text)
    return text

def validate_position(conn, description, max_vote, exclude_id=None, election_id=None):
    """Return a user-facing validation error, or None for a valid position."""
    description = (description or '').strip()
    if not description or not slugify(description):
        return 'Position description is required'
    try:
        max_vote = int(max_vote)
    except (TypeError, ValueError):
        return 'Maximum vote must be a whole number'
    if max_vote < 1:
        return 'Maximum vote must be at least 1'

    positions = conn.execute("SELECT id, description, election_id FROM positions").fetchall()
    for position in positions:
        if (position['id'] != exclude_id and
            (election_id is None or position['election_id'] == int(election_id)) and
            slugify(position['description']) == slugify(description)):
            return 'A position with this description already exists'
    return None

def validate_candidate(
    conn,
    firstname,
    lastname,
    position_id,
    election_id,
    exclude_id=None
):
    """Return a user-facing validation error, or None if valid."""

    firstname = (firstname or '').strip()
    lastname = (lastname or '').strip()

    if not firstname:
        return 'Candidate first name is required'

    if not lastname:
        return 'Candidate last name is required'

    if not position_id:
        return 'Please select a position'

    if not election_id:
        return 'Please select an election'

    # Make sure the position actually belongs to the selected election.
    position = conn.execute("""
        SELECT id
        FROM positions
        WHERE id = ?
          AND election_id = ?
    """, (position_id, election_id)).fetchone()

    if not position:
        return 'Select a position from the selected election'

    # Prevent duplicate candidate names in the same position.
    candidates = conn.execute("""
        SELECT id, firstname, lastname
        FROM candidates
        WHERE position_id = ?
    """, (position_id,)).fetchall()

    normalized_first = slugify(firstname)
    normalized_last = slugify(lastname)

    for candidate in candidates:

        if candidate['id'] == exclude_id:
            continue

        if (
            slugify(candidate['firstname']) == normalized_first
            and
            slugify(candidate['lastname']) == normalized_last
        ):
            return (
                'A candidate with this name already exists '
                'for this position'
            )

    return None

def selected_election_id(conn, value=None):
    election_id = value or request.args.get('election_id') or request.form.get('election_id') or session.get('selected_election_id')
    if election_id:
        election = conn.execute("SELECT id FROM elections WHERE id = ?", (election_id,)).fetchone()
        if election:
            session['selected_election_id'] = election['id']
            return election['id']
    election = conn.execute("SELECT id FROM elections ORDER BY deadline DESC LIMIT 1").fetchone()
    return election['id'] if election else None


def get_position_results(conn):
    """
    Build per-position totals with proper winner/tie handling.
    """

    positions = conn.execute("""
        SELECT *
        FROM positions
        ORDER BY priority ASC
    """).fetchall()

    results = []

    for pos in positions:

        candidates = conn.execute("""
            SELECT
                c.id,
                c.firstname,
                c.lastname,
                c.photo,
                COUNT(v.id) AS vote_count
            FROM candidates c
            LEFT JOIN votes v
                ON v.candidate_id = c.id
                AND v.position_id = ?
            WHERE c.position_id = ?
            GROUP BY
                c.id,
                c.firstname,
                c.lastname,
                c.photo
            ORDER BY
                vote_count DESC,
                c.lastname ASC,
                c.firstname ASC
        """, (
            pos['id'],
            pos['id']
        )).fetchall()

        total_votes = sum(
            int(candidate['vote_count'] or 0)
            for candidate in candidates
        )

        winner = None
        winners = []
        is_tie = False

        if candidates:

            highest_votes = int(
                candidates[0]['vote_count'] or 0
            )

            winners = [
                candidate
                for candidate in candidates
                if int(candidate['vote_count'] or 0)
                == highest_votes
            ]

            if len(winners) == 1:
                winner = winners[0]
            else:
                is_tie = True

        results.append({
            'position': pos,
            'candidates': candidates,
            'winner': winner,
            'winners': winners,
            'is_tie': is_tie,
            'total_votes': total_votes
        })

    return results

def election_is_open(election):
    if not election:
        return False
    deadline = election['deadline']
    if isinstance(deadline, str):
        deadline = datetime.datetime.fromisoformat(deadline)
    return deadline > datetime.datetime.now()

def get_election_results(conn, election_id):
    """
    Build election results with proper winner/tie handling.

    A position has:
    - one winner if exactly one candidate has the highest vote count
    - multiple winners if candidates are tied for the highest vote count
    - no winner if there are no candidates
    """

    positions = conn.execute("""
        SELECT *
        FROM positions
        WHERE election_id = ?
        ORDER BY priority ASC
    """, (election_id,)).fetchall()

    results = []

    for pos in positions:

        candidates = conn.execute("""
            SELECT
                c.id,
                c.firstname,
                c.lastname,
                c.photo,
                COUNT(v.id) AS vote_count
            FROM candidates c
            LEFT JOIN votes v
                ON v.candidate_id = c.id
                AND v.position_id = ?
                AND v.election_id = ?
            WHERE c.position_id = ?
            GROUP BY
                c.id,
                c.firstname,
                c.lastname,
                c.photo
            ORDER BY
                vote_count DESC,
                c.lastname ASC,
                c.firstname ASC
        """, (
            pos['id'],
            election_id,
            pos['id']
        )).fetchall()

        total_votes = sum(
            int(candidate['vote_count'] or 0)
            for candidate in candidates
        )

        winner = None
        winners = []
        is_tie = False

        if candidates:

            highest_votes = int(
                candidates[0]['vote_count'] or 0
            )

            winners = [
                candidate
                for candidate in candidates
                if int(candidate['vote_count'] or 0)
                == highest_votes
            ]

            if len(winners) == 1:
                winner = winners[0]
                is_tie = False
            else:
                winner = None
                is_tie = True

        results.append({
            'position': pos,
            'candidates': candidates,

            # Single winner, or None when tied.
            'winner': winner,

            # All candidates sharing the highest vote count.
            'winners': winners,

            # True when two or more candidates have the highest votes.
            'is_tie': is_tie,

            'total_votes': total_votes
        })

    return results


def voter_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'voter' not in session:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin' not in session:
            flash('Your admin session has expired. Please log in again.', 'error')
            return redirect(url_for('admin_index'))
        return f(*args, **kwargs)
    return decorated_function

@app.context_processor
def inject_admin_context():
    admin_user = None
    admin_elections = []
    selected_admin_election_id = None
    election_title = 'ELECTION'
    conn = get_db()
    try:
        admin_elections = conn.execute("SELECT * FROM elections ORDER BY deadline DESC").fetchall()
        if 'admin' in session:
            admin_user = conn.execute("SELECT * FROM admin WHERE id = ?", (session['admin'],)).fetchone()
            selected_admin_election_id = selected_election_id(conn)
            if admin_user and admin_user['created_on']:
                try:
                    admin_user = dict(admin_user)
                    admin_user['created_on'] = datetime.datetime.strptime(admin_user['created_on'], '%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError):
                    pass
    finally:
        conn.close()
    config = configparser.ConfigParser()
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin', 'config.ini')
    config.read(config_path)
    election_title = config.get('DEFAULT', 'election_title', fallback='ELECTION')
    return dict(admin_user=admin_user, election_title=election_title,
                admin_elections=admin_elections,
                selected_admin_election_id=selected_admin_election_id)

@app.route('/')
def index():
    if 'voter' in session:
        return redirect(url_for('home'))
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():

    voter_id = (request.form.get('voter') or '').strip()
    password = request.form.get('password') or ''

    if not voter_id or not password:
        flash(
            'Enter your Voter ID and password.',
            'error'
        )
        return redirect(url_for('index'))

    conn = get_db()

    try:

        voter = conn.execute("""
            SELECT *
            FROM voters
            WHERE voters_id = ?
        """, (voter_id,)).fetchone()

        if not voter:

            flash(
                'We could not find a voter account with that ID. Please check your Voter ID and try again.',
                'error'
            )

            return redirect(url_for('index'))

        if not bcrypt.checkpw(
            password.encode('utf-8'),
            voter['password'].encode('utf-8')
        ):

            flash(
                'Incorrect password.',
                'error'
            )

            return redirect(url_for('index'))

        session['voter'] = voter['id']

        return redirect(url_for('home'))

    finally:
        conn.close()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')

    firstname = (request.form.get('firstname') or '').strip()
    lastname = (request.form.get('lastname') or '').strip()
    password = request.form.get('password') or ''
    confirm_password = request.form.get('confirm_password') or ''

    if not firstname or not lastname:
        flash(
            'First name and last name are required.',
            'error'
        )
        return redirect(url_for('register'))

    if len(password) < 6:
        flash(
            'Password must be at least 6 characters.',
            'error'
        )
        return redirect(url_for('register'))

    if password != confirm_password:
        flash(
            'Passwords do not match.',
            'error'
        )
        return redirect(url_for('register'))

    # Generate a unique voter ID
    voter_id = ''.join(
        random.choices(
            string.ascii_letters + string.digits,
            k=15
        )
    )

    hashed_password = bcrypt.hashpw(
        password.encode('utf-8'),
        bcrypt.gensalt()
    ).decode('utf-8')

    conn = get_db()

    try:
        conn.execute(
            """
            INSERT INTO voters
            (voters_id, password, firstname, lastname, photo)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                voter_id,
                hashed_password,
                firstname,
                lastname,
                ''
            )
        )

        conn.commit()

        flash(
            f'Registration successful! Your Voter ID is: {voter_id}. '
            f'Please remember your Voter ID and password.',
            'success'
        )

        return redirect(url_for('index'))

    except Exception as error:
        conn.rollback()
        flash(str(error), 'error')
        return redirect(url_for('register'))

    finally:
        conn.close()

@app.route('/logout')
def logout():
    session.pop('voter', None)
    return redirect(url_for('index'))

@app.route('/home')
@voter_required
def home():
    conn = get_db()
    try:
        voter = conn.execute("SELECT * FROM voters WHERE id = ?", (session['voter'],)).fetchone()
        elections = conn.execute("SELECT * FROM elections ORDER BY deadline DESC").fetchall()
        election_sections = []
        for election in elections:
            section = dict(election)
            section['is_open'] = election_is_open(election)
            deadline = election['deadline']
            if isinstance(deadline, str):
                deadline = datetime.datetime.fromisoformat(deadline)
            section['deadline_ms'] = int(deadline.timestamp() * 1000)
            section['results'] = [] if section['is_open'] else get_election_results(conn, election['id'])
            section['positions'] = []
            for position in conn.execute(
                "SELECT * FROM positions WHERE election_id = ? ORDER BY priority ASC", (election['id'],)
            ).fetchall():
                position_data = dict(position)
                position_data['candidates'] = conn.execute(
                    "SELECT * FROM candidates WHERE position_id = ?", (position['id'],)
                ).fetchall()
                position_data['votes_used'] = conn.execute(
                    "SELECT COUNT(*) FROM votes WHERE voters_id = ? AND election_id = ? AND position_id = ?",
                    (session['voter'], election['id'], position['id'])
                ).fetchone()[0]
                position_data['position_locked'] = position_data['votes_used'] >= position['max_vote']
                position_data['previous_candidate_ids'] = [row['candidate_id'] for row in conn.execute(
                    "SELECT candidate_id FROM votes WHERE voters_id = ? AND election_id = ? AND position_id = ?",
                    (session['voter'], election['id'], position['id'])
                ).fetchall()]
                section['positions'].append(position_data)
            section['has_voted'] = bool(section['positions']) and all(
                position['position_locked'] for position in section['positions'])
            election_sections.append(section)
        return render_template('home.html', voter=voter, elections=election_sections, slugify=slugify)
    finally:
        conn.close()

@app.route('/profile')
@voter_required
def profile():
    conn = get_db()

    try:
        voter = conn.execute("""
            SELECT
                id,
                voters_id,
                firstname,
                lastname,
                photo
            FROM voters
            WHERE id = ?
        """, (session['voter'],)).fetchone()

        if not voter:
            session.pop('voter', None)
            flash('Voter account not found.', 'error')
            return redirect(url_for('index'))

        return render_template(
            'profile.html',
            voter=voter
        )

    finally:
        conn.close()

@app.route('/profile/update', methods=['POST'])
@voter_required
def profile_update():

    firstname = (request.form.get('firstname') or '').strip()
    lastname = (request.form.get('lastname') or '').strip()
    password = request.form.get('password') or ''
    confirm_password = request.form.get('confirm_password') or ''

    if not firstname or not lastname:
        flash(
            'First name and last name are required.',
            'error'
        )
        return redirect(url_for('profile'))

    conn = get_db()

    try:

        voter = conn.execute("""
            SELECT *
            FROM voters
            WHERE id = ?
        """, (session['voter'],)).fetchone()

        if not voter:
            session.pop('voter', None)
            flash('Voter account not found.', 'error')
            return redirect(url_for('index'))

        # =================================================
        # PASSWORD
        # =================================================

        # If the voter enters a new password,
        # validate it and hash it.
        if password:

            if len(password) < 6:
                flash(
                    'New password must be at least 6 characters.',
                    'error'
                )
                return redirect(url_for('profile'))

            if password != confirm_password:
                flash(
                    'The passwords do not match.',
                    'error'
                )
                return redirect(url_for('profile'))

            hashed_password = bcrypt.hashpw(
                password.encode('utf-8'),
                bcrypt.gensalt()
            ).decode('utf-8')

        else:

            # No new password entered.
            # Keep the existing password.
            hashed_password = voter['password']


        # =================================================
        # PROFILE PHOTO
        # =================================================

        photo = voter['photo']

        if 'photo' in request.files:

            file = request.files['photo']

            if file and file.filename:

                filename = secure_filename(file.filename)

                if filename:

                    # Optional: prevent unsupported file types
                    allowed_extensions = {
                        'jpg',
                        'jpeg',
                        'png',
                        'gif',
                        'webp'
                    }

                    extension = (
                        filename.rsplit('.', 1)[1].lower()
                        if '.' in filename
                        else ''
                    )

                    if extension not in allowed_extensions:

                        flash(
                            'Please upload a valid image file '
                            '(JPG, JPEG, PNG, GIF, or WEBP).',
                            'error'
                        )

                        return redirect(url_for('profile'))

                    file.save(
                        os.path.join(
                            app.config['UPLOAD_FOLDER'],
                            filename
                        )
                    )

                    photo = filename


        # =================================================
        # UPDATE VOTER
        # =================================================

        conn.execute("""
            UPDATE voters
            SET
                firstname = ?,
                lastname = ?,
                password = ?,
                photo = ?
            WHERE id = ?
        """, (
            firstname,
            lastname,
            hashed_password,
            photo,
            session['voter']
        ))

        conn.commit()

        flash(
            'Your profile has been updated successfully.',
            'success'
        )

    except Exception as error:

        conn.rollback()

        flash(
            str(error),
            'error'
        )

    finally:
        conn.close()

    return redirect(url_for('profile'))

@app.route('/preview', methods=['POST'])
@voter_required
def preview():
    conn = get_db()
    try:
        output = {'error': False, 'list': ''}
        election = conn.execute("SELECT * FROM elections WHERE id = ?", (request.form.get('election_id'),)).fetchone()
        if not election_is_open(election):
            return jsonify({'error': True, 'message': ['This election is closed.']})
        positions = conn.execute("SELECT * FROM positions WHERE election_id = ?", (election['id'],)).fetchall()
        for pos in positions:
            position_slug = slugify(pos['description'])
            if position_slug in request.form:
                if pos['max_vote'] > 1:
                    selected = request.form.getlist(position_slug)
                    if len(selected) > pos['max_vote']:
                        output['error'] = True
                        if 'message' not in output:
                            output['message'] = []
                        output['message'].append(f'<li>You can only choose {pos["max_vote"]} candidates for {pos["description"]}</li>')
                    else:
                        for candidate_id in selected:
                            candidate = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
                            output['list'] += f"""
                                <div class='row votelist'>
                                    <span class='col-sm-4'><span class='pull-right'><b>{pos['description']} :</b></span></span>
                                    <span class='col-sm-8'>{candidate['firstname']} {candidate['lastname']}</span>
                                </div>
                            """
                else:
                    candidate_id = request.form.get(position_slug)
                    candidate = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
                    output['list'] += f"""
                        <div class='row votelist'>
                            <span class='col-sm-4'><span class='pull-right'><b>{pos['description']} :</b></span></span>
                            <span class='col-sm-8'>{candidate['firstname']} {candidate['lastname']}</span>
                        </div>
                    """
        return jsonify(output)
    finally:
        conn.close()

@app.route('/submit_ballot', methods=['POST'])
@voter_required
def submit_ballot():
    conn = get_db()
    try:
        election_id = request.form.get('election_id')
        election = conn.execute("SELECT * FROM elections WHERE id = ?", (election_id,)).fetchone()
        if not election_is_open(election):
            flash('This election is closed and no longer accepts votes', 'error')
            return redirect(url_for('home'))
        if 'vote' not in request.form:
            flash('Select candidates to vote first', 'error')
            return redirect(url_for('home'))
        positions = conn.execute("SELECT * FROM positions WHERE election_id = ?", (election['id'],)).fetchall()
        vote_list = []
        for pos in positions:
            position_slug = slugify(pos['description'])
            selected = request.form.getlist(position_slug)
            if not selected:
                continue

            existing_votes = conn.execute(
                "SELECT COUNT(*) FROM votes WHERE voters_id = ? AND election_id = ? AND position_id = ?",
                (session['voter'], election_id, pos['id'])
            ).fetchone()[0]

            # max_vote applies to this voter's total choices for this position,
            # including votes submitted during an earlier session.
            if (len(selected) != len(set(selected)) or
                    existing_votes + len(selected) > pos['max_vote']):
                flash(f'You can only choose {pos["max_vote"]} candidates for {pos["description"]}', 'error')
                return redirect(url_for('home'))

            previous_candidates = {
                row['candidate_id'] for row in conn.execute(
                    "SELECT candidate_id FROM votes WHERE voters_id = ? AND election_id = ? AND position_id = ?",
                    (session['voter'], election_id, pos['id'])
                ).fetchall()
            }
            if any(int(candidate_id) in previous_candidates for candidate_id in selected):
                flash(f'You already voted for a candidate in {pos["description"]}. Choose another candidate.', 'error')
                return redirect(url_for('home'))

            # Do not trust submitted IDs: each candidate must belong to the
            # position being voted on.
            for candidate_id in selected:
                candidate = conn.execute(
                    "SELECT id FROM candidates WHERE id = ? AND position_id = ?",
                    (candidate_id, pos['id'])
                ).fetchone()
                if not candidate:
                    flash('Invalid candidate selection', 'error')
                    return redirect(url_for('home'))
                vote_list.append((session['voter'], candidate['id'], pos['id']))

        if not vote_list:
            flash('Please vote for at least one candidate', 'error')
            return redirect(url_for('home'))

        # Per-position max_vote rules are enforced above. Do not block a voter
        # from casting a ballot just because they have already voted in another
        # section of the election.
        conn.executemany("INSERT INTO votes (voters_id, election_id, candidate_id, position_id) VALUES (?, ?, ?, ?)",
                 [(voter_id, election_id, candidate_id, position_id)
                  for voter_id, candidate_id, position_id in vote_list])
        conn.commit()
        flash('Ballot Submitted', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('home'))

@app.route('/admin/')
def admin_index():
    if 'admin' in session:
        return redirect(url_for('admin_home'))
    return render_template('admin/index.html')

@app.route('/admin/login', methods=['POST'])
def admin_login():

    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''

    if not username or not password:
        flash('Enter your username and password.', 'error')
        return redirect(url_for('admin_index'))

    conn = get_db()

    try:
        admin = conn.execute(
            "SELECT * FROM admin WHERE username = ?",
            (username,)
        ).fetchone()

        if not admin:
            flash(
                'Cannot find account with the username.',
                'error'
            )
            return redirect(url_for('admin_index'))

        if not bcrypt.checkpw(
            password.encode('utf-8'),
            admin['password'].encode('utf-8')
        ):
            flash(
                'Incorrect password.',
                'error'
            )
            return redirect(url_for('admin_index'))

        # Create admin session.
        session['admin'] = admin['id']

        return redirect(url_for('admin_home'))

    finally:
        conn.close()

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_index'))

@app.route('/admin/home')
@admin_required
def admin_home():
    conn = get_db()

    try:
        election_id = selected_election_id(conn)

        election = conn.execute(
            "SELECT * FROM elections WHERE id = ?",
            (election_id,)
        ).fetchone()

        positions_count = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE election_id = ?",
            (election_id,)
        ).fetchone()[0]

        candidates_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM candidates
            WHERE position_id IN (
                SELECT id FROM positions WHERE election_id = ?
            )
            """,
            (election_id,)
        ).fetchone()[0]

        voters_count = conn.execute(
            "SELECT COUNT(*) FROM voters"
        ).fetchone()[0]

        votes_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM votes
            WHERE election_id = ?
            """,
            (election_id,)
        ).fetchone()[0]

        positions = conn.execute(
            """
            SELECT *
            FROM positions
            WHERE election_id = ?
            ORDER BY priority ASC
            """,
            (election_id,)
        ).fetchall()

        candidates_data = {}
        votes_data = {}

        for pos in positions:

            candidates = conn.execute(
                """
                SELECT *
                FROM candidates
                WHERE position_id = ?
                """,
                (pos['id'],)
            ).fetchall()

            candidates_data[pos['id']] = [
                c['lastname'] for c in candidates
            ]

            votes_data[pos['id']] = []

            for candidate in candidates:

                cnt = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM votes
                    WHERE candidate_id = ?
                    AND election_id = ?
                    """,
                    (candidate['id'], election_id)
                ).fetchone()[0]

                votes_data[pos['id']].append(cnt)

        # ==========================================
        # ELECTION RESULTS
        # ==========================================

        results = get_election_results(conn, election_id)

        # ==========================================
        # MATPLOTLIB PIE CHARTS
        # ==========================================

        pie_charts = {}

        for result in results:

            candidates = result.get('candidates', [])

            # Remove candidates with 0 votes
            chart_candidates = [
                candidate
                for candidate in candidates
                if int(candidate['vote_count']) > 0
            ]

            if not chart_candidates:
                pie_charts[result['position']['id']] = None
                continue

            labels = [
                f"{candidate['firstname']} {candidate['lastname']}"
                for candidate in chart_candidates
            ]

            values = [
                int(candidate['vote_count'])
                for candidate in chart_candidates
            ]

            # Small compact chart
            fig, ax = plt.subplots(figsize=(4.2, 3.0))

            ax.pie(
                values,
                labels=labels,
                autopct='%1.1f%%',
                startangle=90,
                textprops={'fontsize': 8}
            )

            ax.set_title(
                result['position']['description'],
                fontsize=10,
                fontweight='bold'
            )

            plt.tight_layout()

            # Convert chart to PNG
            image = io.BytesIO()

            plt.savefig(
                image,
                format='png',
                bbox_inches='tight',
                dpi=100
            )

            plt.close(fig)

            image.seek(0)

            chart_image = base64.b64encode(
                image.getvalue()
            ).decode('utf-8')

            pie_charts[result['position']['id']] = chart_image

        return render_template(
            'admin/home.html',

            positions_count=positions_count,
            candidates_count=candidates_count,
            voters_count=voters_count,
            votes_count=votes_count,

            positions=positions,
            candidates_data=candidates_data,
            votes_data=votes_data,

            results=results,
            pie_charts=pie_charts,

            selected_election=election,
            slugify=slugify
        )

    finally:
        conn.close()

@app.route('/admin/elections')
@admin_required
def admin_elections():
    conn = get_db()
    try:
        elections = conn.execute("SELECT * FROM elections ORDER BY deadline DESC").fetchall()
        election_rows = []
        for election in elections:
            row = dict(election)
            row['is_open'] = election_is_open(election)
            row['results'] = get_election_results(conn, election['id'])
            election_rows.append(row)
        return render_template('admin/elections.html', elections=election_rows)
    finally:
        conn.close()

@app.route('/admin/elections/add', methods=['POST'])
@admin_required
def admin_elections_add():
    title = (request.form.get('title') or '').strip()
    deadline_text = request.form.get('deadline')
    try:
        deadline = datetime.datetime.strptime(deadline_text, '%Y-%m-%dT%H:%M')
    except (TypeError, ValueError):
        flash('Enter a valid election deadline', 'error')
        return redirect(url_for('admin_elections'))
    if not title:
        flash('Election title is required', 'error')
    elif deadline <= datetime.datetime.now():
        flash('Election deadline must be in the future', 'error')
    else:
        conn = get_db()
        try:
            conn.execute("INSERT INTO elections (title, deadline) VALUES (?, ?)", (title, deadline))
            new_election_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            flash('Election created successfully', 'success')
            session['selected_election_id'] = new_election_id
        finally:
            conn.close()
    return redirect(url_for('admin_elections'))

@app.route('/admin/elections/select', methods=['GET', 'POST'])
@admin_required
def admin_elections_select():
    conn = get_db()
    try:
        election_id = request.values.get('election_id')
        if not conn.execute("SELECT id FROM elections WHERE id = ?", (election_id,)).fetchone():
            flash('Select a valid election', 'error')
        else:
            session['selected_election_id'] = int(election_id)
    finally:
        conn.close()
    next_url = request.values.get('next') or url_for('admin_home')
    if not next_url.startswith('/'):
        next_url = url_for('admin_home')
    return redirect(next_url)

@app.route('/admin/elections/delete', methods=['POST'])
@admin_required
def admin_elections_delete():
    election_id = request.form.get('id')
    conn = get_db()
    try:
        conn.execute("DELETE FROM votes WHERE election_id = ?", (election_id,))
        conn.execute("DELETE FROM elections WHERE id = ?", (election_id,))
        conn.commit()
        flash('Election deleted successfully', 'success')
    finally:
        conn.close()
    return redirect(url_for('admin_elections'))

@app.route('/admin/voters')
@admin_required
def admin_voters():

    conn = get_db()

    try:

        voters = conn.execute("""
            SELECT
                id,
                voters_id,
                firstname,
                lastname,
                photo
            FROM voters
            ORDER BY id DESC
        """).fetchall()

        return render_template(
            'admin/voters.html',
            voters=voters
        )

    finally:
        conn.close()

@app.route('/admin/voters/add', methods=['POST'])
@admin_required
def admin_voters_add():

    firstname = (request.form.get('firstname') or '').strip()
    lastname = (request.form.get('lastname') or '').strip()
    password = request.form.get('password') or ''

    if not firstname or not lastname:
        flash(
            'First name and last name are required.',
            'error'
        )
        return redirect(url_for('admin_voters'))

    if len(password) < 4:
        flash(
            'Password must be at least 4 characters.',
            'error'
        )
        return redirect(url_for('admin_voters'))

    conn = get_db()

    try:

        while True:

            voter_id = ''.join(
                random.choices(
                    string.ascii_letters + string.digits,
                    k=15
                )
            )

            exists = conn.execute("""
                SELECT id
                FROM voters
                WHERE voters_id = ?
            """, (voter_id,)).fetchone()

            if not exists:
                break

        hashed_password = bcrypt.hashpw(
            password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

        photo = ''

        if 'photo' in request.files:

            file = request.files['photo']

            if file and file.filename:

                photo = file.filename

                file.save(
                    os.path.join(
                        app.config['UPLOAD_FOLDER'],
                        photo
                    )
                )

        conn.execute("""
            INSERT INTO voters
                (
                    voters_id,
                    password,
                    firstname,
                    lastname,
                    photo,
                    has_voted
                )
            VALUES (?, ?, ?, ?, ?, 0)
        """, (
            voter_id,
            hashed_password,
            firstname,
            lastname,
            photo
        ))

        conn.commit()

        flash(
            f'Voter created successfully. '
            f'Voter ID: {voter_id} | Password: {password}',
            'success'
        )

    except Exception as error:

        conn.rollback()

        flash(
            str(error),
            'error'
        )

    finally:
        conn.close()

    return redirect(url_for('admin_voters'))

@app.route('/admin/voters/edit', methods=['POST'])
@admin_required
def admin_voters_edit():
    id = request.form.get('id')
    firstname = request.form.get('firstname')
    lastname = request.form.get('lastname')
    conn = get_db()
    try:
        voter = conn.execute("SELECT * FROM voters WHERE id = ?", (id,)).fetchone()
        if not voter:
            flash('Voter not found', 'error')
            return redirect(url_for('admin_voters'))
        conn.execute(
            "UPDATE voters SET firstname = ?, lastname = ? WHERE id = ?",
            (firstname, lastname, id))
        conn.commit()
        flash('Voter updated successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_voters'))

@app.route('/admin/voters/delete', methods=['POST'])
@admin_required
def admin_voters_delete():
    id = request.form.get('id')
    conn = get_db()
    try:
        conn.execute("DELETE FROM voters WHERE id = ?", (id,))
        conn.commit()
        flash('Voter deleted successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_voters'))

@app.route('/admin/voters/row', methods=['POST'])
@admin_required
def admin_voters_row():
    voter_id = request.form.get('id')

    conn = get_db()

    try:
        voter = conn.execute("""
            SELECT
                id,
                voters_id,
                firstname,
                lastname,
                photo
            FROM voters
            WHERE id = ?
        """, (voter_id,)).fetchone()

        if not voter:
            return jsonify({
                'error': True,
                'message': 'Voter not found'
            }), 404

        return jsonify({
            'id': voter['id'],
            'voters_id': voter['voters_id'],
            'firstname': voter['firstname'],
            'lastname': voter['lastname'],
            'photo': voter['photo']
        })

    finally:
        conn.close()

@app.route('/admin/voters/photo', methods=['POST'])
@admin_required
def admin_voters_photo():
    id = request.form.get('id')
    photo = ''
    if 'photo' in request.files:
        file = request.files['photo']
        if file.filename:
            photo = file.filename
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo))
    conn = get_db()
    try:
        conn.execute("UPDATE voters SET photo = ? WHERE id = ?", (photo, id))
        conn.commit()
        flash('Photo updated successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_voters'))

@app.route('/admin/positions')
@admin_required
def admin_positions():
    conn = get_db()
    try:
        election_id = selected_election_id(conn)
        elections = conn.execute("SELECT * FROM elections ORDER BY deadline DESC").fetchall()
        positions = conn.execute("SELECT * FROM positions WHERE election_id = ? ORDER BY priority ASC", (election_id,)).fetchall()
        return render_template('admin/positions.html', positions=positions, elections=elections, selected_election_id=election_id)
    finally:
        conn.close()

@app.route('/admin/positions/add', methods=['POST'])
@admin_required
def admin_positions_add():
    description = request.form.get('description')
    max_vote = request.form.get('max_vote')
    election_id = request.form.get('election_id')
    conn = get_db()
    try:
        error = validate_position(conn, description, max_vote, election_id=election_id)
        if error:
            flash(error, 'error')
            return redirect(url_for('admin_positions'))
        description = description.strip()
        max_vote = int(max_vote)
        if not conn.execute("SELECT id FROM elections WHERE id = ?", (election_id,)).fetchone():
            flash('Select a valid election', 'error')
            return redirect(url_for('admin_positions'))
        last = conn.execute("SELECT * FROM positions WHERE election_id = ? ORDER BY priority DESC LIMIT 1", (election_id,)).fetchone()
        priority = (last['priority'] + 1) if last else 1
        conn.execute(
            "INSERT INTO positions (election_id, description, max_vote, priority) VALUES (?, ?, ?, ?)",
            (election_id, description, max_vote, priority))
        conn.commit()
        flash('Position added successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_positions', election_id=election_id))

@app.route('/admin/positions/edit', methods=['POST'])
@admin_required
def admin_positions_edit():
    id = request.form.get('id')
    description = request.form.get('description')
    max_vote = request.form.get('max_vote')
    conn = get_db()
    try:
        position = conn.execute("SELECT election_id FROM positions WHERE id = ?", (id,)).fetchone()
        error = validate_position(conn, description, max_vote, int(id), position['election_id'] if position else None)
        if error:
            flash(error, 'error')
            return redirect(url_for('admin_positions'))
        conn.execute(
            "UPDATE positions SET description = ?, max_vote = ? WHERE id = ?",
            (description.strip(), int(max_vote), id))
        conn.commit()
        flash('Position updated successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_positions', election_id=position['election_id'] if position else None))

@app.route('/admin/positions/delete', methods=['POST'])
@admin_required
def admin_positions_delete():
    id = request.form.get('id')
    conn = get_db()
    try:
        position = conn.execute("SELECT election_id FROM positions WHERE id = ?", (id,)).fetchone()
        conn.execute("DELETE FROM positions WHERE id = ?", (id,))
        conn.commit()
        flash('Position deleted successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_positions', election_id=position['election_id'] if position else None))

@app.route('/admin/positions/row', methods=['POST'])
@admin_required
def admin_positions_row():
    id = request.form.get('id')
    conn = get_db()
    try:
        position = conn.execute("SELECT * FROM positions WHERE id = ?", (id,)).fetchone()
        return jsonify(dict(position))
    finally:
        conn.close()

@app.route('/admin/candidates')
@admin_required
def admin_candidates():
    conn = get_db()
    try:
        election_id = selected_election_id(conn)
        elections = conn.execute("SELECT * FROM elections ORDER BY deadline DESC").fetchall()
        candidates = conn.execute("""
            SELECT candidates.id AS canid,
                   candidates.firstname, candidates.lastname, candidates.photo, candidates.platform, candidates.position_id,
                   positions.description AS position_desc
            FROM candidates
            LEFT JOIN positions ON positions.id = candidates.position_id
            WHERE positions.election_id = ?
            ORDER BY positions.priority ASC
        """, (election_id,)).fetchall()
        positions = conn.execute("SELECT * FROM positions WHERE election_id = ? ORDER BY priority ASC", (election_id,)).fetchall()
        return render_template('admin/candidates.html', candidates=candidates, positions=positions, elections=elections, selected_election_id=election_id)
    finally:
        conn.close()

@app.route('/admin/candidates/add', methods=['POST'])
@admin_required
def admin_candidates_add():

    firstname = (request.form.get('firstname') or '').strip()
    lastname = (request.form.get('lastname') or '').strip()
    position = request.form.get('position')
    election_id = request.form.get('election_id')
    platform = (request.form.get('platform') or '').strip()
    biography = (request.form.get('biography') or '').strip()

    photo = ''

    if 'photo' in request.files:

        file = request.files['photo']

        if file and file.filename:
            photo = file.filename

            file.save(
                os.path.join(
                    app.config['UPLOAD_FOLDER'],
                    photo
                )
            )

    conn = get_db()

    try:

        error = validate_candidate(
            conn,
            firstname,
            lastname,
            position,
            election_id
        )

        if error:
            flash(error, 'error')
            return redirect(
                url_for(
                    'admin_candidates',
                    election_id=election_id
                )
            )

        conn.execute("""
            INSERT INTO candidates
                (
                    position_id,
                    firstname,
                    lastname,
                    photo,
                    platform,
                    biography
                )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            position,
            firstname,
            lastname,
            photo,
            platform,
            biography
        ))

        conn.commit()

        flash(
            'Candidate added successfully',
            'success'
        )

    except Exception as e:

        conn.rollback()

        flash(
            str(e),
            'error'
        )

    finally:
        conn.close()

    return redirect(
        url_for(
            'admin_candidates',
            election_id=election_id
        )
    )

@app.route('/admin/candidates/edit', methods=['POST'])
@admin_required
def admin_candidates_edit():

    id = request.form.get('id')
    firstname = (request.form.get('firstname') or '').strip()
    lastname = (request.form.get('lastname') or '').strip()
    position = request.form.get('position')
    platform = (request.form.get('platform') or '').strip()
    biography = (request.form.get('biography') or '').strip()

    conn = get_db()

    try:

        # Find the existing candidate and its election.
        candidate = conn.execute("""
            SELECT
                candidates.id,
                positions.election_id
            FROM candidates
            JOIN positions
                ON positions.id = candidates.position_id
            WHERE candidates.id = ?
        """, (id,)).fetchone()

        if not candidate:
            flash(
                'Candidate not found',
                'error'
            )
            return redirect(
                url_for('admin_candidates')
            )

        election_id = candidate['election_id']

        # Validate the edited candidate.
        error = validate_candidate(
            conn,
            firstname,
            lastname,
            position,
            election_id,
            exclude_id=int(id)
        )

        if error:
            flash(error, 'error')
            return redirect(
                url_for(
                    'admin_candidates',
                    election_id=election_id
                )
            )

        conn.execute("""
            UPDATE candidates
            SET
                firstname = ?,
                lastname = ?,
                position_id = ?,
                platform = ?,
                biography = ?
            WHERE id = ?
        """, (
            firstname,
            lastname,
            position,
            platform,
            biography,
            id
        ))

        conn.commit()

        flash(
            'Candidate updated successfully',
            'success'
        )

    except Exception as e:

        conn.rollback()

        flash(
            str(e),
            'error'
        )

    finally:
        conn.close()

    return redirect(
        url_for(
            'admin_candidates',
            election_id=election_id
            if candidate else None
        )
    )

@app.route('/admin/candidates/delete', methods=['POST'])
@admin_required
def admin_candidates_delete():
    id = request.form.get('id')
    conn = get_db()
    try:
        candidate = conn.execute("SELECT positions.election_id FROM candidates JOIN positions ON positions.id = candidates.position_id WHERE candidates.id = ?", (id,)).fetchone()
        conn.execute("DELETE FROM candidates WHERE id = ?", (id,))
        conn.commit()
        flash('Candidate deleted successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_candidates', election_id=candidate['election_id'] if candidate else None))

@app.route('/admin/candidates/row', methods=['POST'])
@admin_required
def admin_candidates_row():
    id = request.form.get('id')
    conn = get_db()
    try:
        candidate = conn.execute("""
            SELECT candidates.id AS canid,
                   candidates.firstname, candidates.lastname, candidates.photo, candidates.platform, candidates.biography, candidates.position_id,
                   positions.description AS position_desc
            FROM candidates
            LEFT JOIN positions ON positions.id = candidates.position_id
            WHERE candidates.id = ?
        """, (id,)).fetchone()
        return jsonify(dict(candidate))
    finally:
        conn.close()

@app.route('/admin/candidates/photo', methods=['POST'])
@admin_required
def admin_candidates_photo():
    id = request.form.get('id')
    photo = ''
    if 'photo' in request.files:
        file = request.files['photo']
        if file.filename:
            photo = file.filename
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo))
    conn = get_db()
    try:
        conn.execute("UPDATE candidates SET photo = ? WHERE id = ?", (photo, id))
        conn.commit()
        flash('Photo updated successfully', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_candidates'))

@app.route('/admin/votes')
@admin_required
def admin_votes():
    conn = get_db()
    try:
        votes = conn.execute("""
            SELECT votes.id,
                   candidates.firstname AS canfirst, candidates.lastname AS canlast,
                   voters.firstname AS votfirst, voters.lastname AS votlast,
                     positions.description AS position_desc, positions.priority,
                     elections.title AS election_title
            FROM votes
            LEFT JOIN positions ON positions.id = votes.position_id
            LEFT JOIN candidates ON candidates.id = votes.candidate_id
            LEFT JOIN voters ON voters.id = votes.voters_id
                 LEFT JOIN elections ON elections.id = votes.election_id
                 ORDER BY elections.deadline DESC, positions.priority ASC
        """).fetchall()
        return render_template('admin/votes.html', votes=votes)
    finally:
        conn.close()

@app.route('/admin/votes/reset', methods=['POST'])
@admin_required
def admin_votes_reset():
    conn = get_db()
    try:
        conn.execute("DELETE FROM votes")
        # Reopen voting for every voter when an administrator resets an election.
        conn.execute("UPDATE voters SET has_voted = 0")
        conn.commit()
        flash('Votes reset successfully. All voters can vote again.', 'success')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_votes'))

@app.route('/admin/ballot')
@admin_required
def admin_ballot():
    conn = get_db()
    try:
        elections = conn.execute("SELECT * FROM elections ORDER BY deadline DESC").fetchall()
        election_id = selected_election_id(conn)
        election = conn.execute("SELECT * FROM elections WHERE id = ?", (election_id,)).fetchone()
        return render_template('admin/ballot.html', elections=elections, selected_election_id=election_id, selected_election=election)
    finally:
        conn.close()

@app.route('/admin/ballot/fetch', methods=['POST'])
@admin_required
def admin_ballot_fetch():

    conn = get_db()

    try:

        election_id = request.form.get('election_id')

        election = conn.execute("""
            SELECT *
            FROM elections
            WHERE id = ?
        """, (election_id,)).fetchone()

        if not election:
            return jsonify("""
                <div class="alert alert-danger">
                    Invalid election selected.
                </div>
            """)

        positions = conn.execute("""
            SELECT *
            FROM positions
            WHERE election_id = ?
            ORDER BY priority ASC, id ASC
        """, (election_id,)).fetchall()

        if not positions:
            return jsonify("""
                <div class="alert alert-warning">
                    <i class="fa fa-info-circle"></i>
                    This election has no positions yet.
                    Add positions from the Positions page first.
                </div>
            """)

        total_positions = len(positions)

        output = ''

        for num, pos in enumerate(positions, 1):

            name = slugify(pos['description'])

            candidates = conn.execute("""
                SELECT *
                FROM candidates
                WHERE position_id = ?
                ORDER BY lastname ASC, firstname ASC
            """, (pos['id'],)).fetchall()

            candidate_html = ''

            for candidate in candidates:

                image = (
                    f"../static/images/{candidate['photo']}"
                    if candidate['photo']
                    else "../static/images/profile.jpg"
                )

                candidate_html += f"""
                    <li>
                        <class="flat-red {name}"
                            name="{name}{'[]' if pos['max_vote'] > 1 else ''}"
                            value="{candidate['id']}"
                        >

                        <img
                            src="{image}"
                            height="100"
                            width="100"
                            class="clist"
                        >

                        <span class="cname clist">
                            {candidate['firstname']}
                            {candidate['lastname']}
                        </span>
                    </li>
                """

            if pos['max_vote'] > 1:
                instruction = (
                    f"You may select up to "
                    f"{pos['max_vote']} candidates"
                )
            else:
                instruction = "Select only one candidate"

            updisabled = (
                'disabled'
                if num == 1
                else ''
            )

            downdisabled = (
                'disabled'
                if num == total_positions
                else ''
            )

            output += f"""
                <div class="row ballot-position"
                     data-id="{pos['id']}">

                    <div class="col-xs-12">

                        <div
                            class="box box-solid"
                            id="position-{pos['id']}"
                        >

                            <div class="box-header with-border">

                                <h3 class="box-title">
                                    <b>
                                        {pos['description']}
                                    </b>
                                </h3>

                                <div class="pull-right box-tools">

                                    <button
                                        type="button"
                                        class="btn btn-default btn-sm moveup"
                                        data-id="{pos['id']}"
                                        {updisabled}
                                    >
                                        <i class="fa fa-arrow-up"></i>
                                    </button>

                                    <button
                                        type="button"
                                        class="btn btn-default btn-sm movedown"
                                        data-id="{pos['id']}"
                                        {downdisabled}
                                    >
                                        <i class="fa fa-arrow-down"></i>
                                    </button>

                                </div>

                            </div>

                            <div class="box-body">

                                <p>
                                    {instruction}

                                    <span class="pull-right">

                                        <button
                                            type="button"
                                            class="btn btn-success btn-sm reset"
                                            data-desc="{name}"
                                        >
                                            <i class="fa fa-refresh"></i>
                                            Reset
                                        </button>

                                    </span>
                                </p>

                                <div class="candidate_list">

                                    <ul>
                                        {candidate_html}
                                    </ul>

                                </div>

                            </div>

                        </div>

                    </div>

                </div>
            """

        return jsonify(output)

    finally:
        conn.close()

@app.route('/admin/ballot/up', methods=['POST'])
@admin_required
def admin_ballot_up():

    position_id = request.form.get('id')

    conn = get_db()

    try:

        position = conn.execute("""
            SELECT *
            FROM positions
            WHERE id = ?
        """, (position_id,)).fetchone()

        if not position:
            return jsonify({
                'error': True,
                'message': 'Position not found'
            })

        if position['priority'] <= 1:

            return jsonify({
                'error': True,
                'message': 'This position is already at the top'
            })

        current_priority = position['priority']

        previous = conn.execute("""
            SELECT id
            FROM positions
            WHERE election_id = ?
              AND priority = ?
        """, (
            position['election_id'],
            current_priority - 1
        )).fetchone()

        if not previous:

            return jsonify({
                'error': True,
                'message': 'Cannot move this position'
            })

        conn.execute("""
            UPDATE positions
            SET priority = ?
            WHERE id = ?
        """, (
            current_priority,
            previous['id']
        ))

        conn.execute("""
            UPDATE positions
            SET priority = ?
            WHERE id = ?
        """, (
            current_priority - 1,
            position_id
        ))

        conn.commit()

        return jsonify({
            'error': False
        })

    except Exception as error:

        conn.rollback()

        return jsonify({
            'error': True,
            'message': str(error)
        })

    finally:
        conn.close()

@app.route('/admin/ballot/down', methods=['POST'])
@admin_required
def admin_ballot_down():

    position_id = request.form.get('id')

    conn = get_db()

    try:

        position = conn.execute("""
            SELECT *
            FROM positions
            WHERE id = ?
        """, (position_id,)).fetchone()

        if not position:
            return jsonify({
                'error': True,
                'message': 'Position not found'
            })

        total_positions = conn.execute("""
            SELECT COUNT(*)
            FROM positions
            WHERE election_id = ?
        """, (position['election_id'],)).fetchone()[0]

        if position['priority'] >= total_positions:

            return jsonify({
                'error': True,
                'message': 'This position is already at the bottom'
            })

        current_priority = position['priority']

        next_position = conn.execute("""
            SELECT id
            FROM positions
            WHERE election_id = ?
              AND priority = ?
        """, (
            position['election_id'],
            current_priority + 1
        )).fetchone()

        if not next_position:

            return jsonify({
                'error': True,
                'message': 'Cannot move this position'
            })

        conn.execute("""
            UPDATE positions
            SET priority = ?
            WHERE id = ?
        """, (
            current_priority,
            next_position['id']
        ))

        conn.execute("""
            UPDATE positions
            SET priority = ?
            WHERE id = ?
        """, (
            current_priority + 1,
            position_id
        ))

        conn.commit()

        return jsonify({
            'error': False
        })

    except Exception as error:

        conn.rollback()

        return jsonify({
            'error': True,
            'message': str(error)
        })

    finally:
        conn.close()

@app.route('/admin/config/save', methods=['POST'])
@admin_required
def admin_config_save():
    return_url = request.args.get('return', 'home')
    title = request.form.get('title')
    config = configparser.ConfigParser()
    config['DEFAULT'] = {'election_title': title}
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin', 'config.ini')
    with open(config_path, 'w') as configfile:
        config.write(configfile)
    flash('Election title updated successfully', 'success')
    return redirect(url_for(f'admin_{return_url}'))

@app.route('/admin/profile/update', methods=['POST'])
@admin_required
def admin_profile_update():
    return_url = request.args.get('return', 'home')
    curr_password = request.form.get('curr_password')
    username = request.form.get('username')
    password = request.form.get('password')
    firstname = request.form.get('firstname')
    lastname = request.form.get('lastname')
    photo = ''
    if 'photo' in request.files:
        file = request.files['photo']
        if file.filename:
            photo = file.filename
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo))
    conn = get_db()
    try:
        admin = conn.execute("SELECT * FROM admin WHERE id = ?", (session['admin'],)).fetchone()
        if bcrypt.checkpw(curr_password.encode('utf-8'), admin['password'].encode('utf-8')):
            if not photo:
                photo = admin['photo']
            if password == admin['password']:
                pass
            else:
                password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            conn.execute(
                "UPDATE admin SET username = ?, password = ?, firstname = ?, lastname = ?, photo = ? WHERE id = ?",
                (username, password, firstname, lastname, photo, session['admin']))
            conn.commit()
            flash('Admin profile updated successfully', 'success')
        else:
            flash('Incorrect password', 'error')
    except Exception as e:
        conn.rollback()
        flash(str(e), 'error')
    finally:
        conn.close()
    return redirect(url_for(f'admin_{return_url}'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
