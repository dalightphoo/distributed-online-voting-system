import os
import sqlite3
import tempfile

import bcrypt

import app as voting_app


fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(fd)

try:
    voting_app.app.config["TESTING"] = True
    voting_app.app.config["DB_PATH"] = db_path
    voting_app.init_db()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO voters (voters_id, password, firstname, lastname) VALUES (?, ?, ?, ?)",
        ("VOTER-1", bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode(), "Test", "Voter"),
    )
    conn.execute("INSERT INTO positions (description, max_vote, priority) VALUES (?, ?, ?)", ("President", 1, 1))
    conn.execute("INSERT INTO candidates (position_id, firstname, lastname) VALUES (?, ?, ?)", (1, "Ada", "Candidate"))
    conn.execute("INSERT INTO positions (description, max_vote, priority) VALUES (?, ?, ?)", ("Council", 2, 2))
    conn.execute("INSERT INTO candidates (position_id, firstname, lastname) VALUES (?, ?, ?)", (2, "Grace", "Hopper"))
    conn.execute("INSERT INTO candidates (position_id, firstname, lastname) VALUES (?, ?, ?)", (2, "Katherine", "Johnson"))
    conn.commit()
    voter_id = conn.execute("SELECT id FROM voters WHERE voters_id = ?", ("VOTER-1",)).fetchone()[0]
    conn.close()

    client = voting_app.app.test_client()
    landing = client.get("/")
    assert b"Voter Login" in landing.data
    assert b"Admin Login" in landing.data

    with client.session_transaction() as session:
        session["voter"] = voter_id
    submit_first = client.post("/submit_ballot", data={"vote": "1", "president": "1"}, follow_redirects=True)
    assert b"Ballot Submitted" in submit_first.data
    ballot_page = client.get("/home")
    assert b"Ada Candidate" in ballot_page.data
    assert b"You have already voted for this election." not in ballot_page.data
    assert b"used all 1 vote" in ballot_page.data

    second_submit = client.post(
        "/submit_ballot",
        data={"vote": "1", "president": "1"},
        follow_redirects=True,
    )
    assert b"You can only choose 1 candidates for President" in second_submit.data

    council_first = client.post(
        "/submit_ballot",
        data={"vote": "1", "council": "2"},
        follow_redirects=True,
    )
    assert b"Ballot Submitted" in council_first.data
    duplicate_candidate = client.post(
        "/submit_ballot",
        data={"vote": "1", "council": "2"},
        follow_redirects=True,
    )
    assert b"You already voted for a candidate in Council" in duplicate_candidate.data
    council_second = client.post(
        "/submit_ballot",
        data={"vote": "1", "council": "3"},
        follow_redirects=True,
    )
    assert b"Ballot Submitted" in council_second.data
    assert b"used all 2 votes" in council_second.data

    with client.session_transaction() as session:
        session.clear()
        session["admin"] = 1
    create_voter = client.post(
        "/admin/voters/add",
        data={"firstname": "New", "lastname": "Voter", "password": "demo123"},
        follow_redirects=True,
    )
    assert b"Voter ID" in create_voter.data
    assert b"demo123" in create_voter.data

    with client.session_transaction() as session:
        session.clear()
        session["admin"] = 1
    reset = client.post("/admin/votes/reset", follow_redirects=True)
    assert b"All voters can vote again" in reset.data

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 0
    conn.close()

    duplicate_position = client.post(
        "/admin/positions/add",
        data={"description": "the President", "max_vote": "1"},
        follow_redirects=True,
    )
    assert b"already exists" in duplicate_position.data
    print("Ballot view, vote reset, and position validation: PASS")
finally:
    os.unlink(db_path)
