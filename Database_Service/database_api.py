"""Database Service: the only process allowed to access SQLite."""
import datetime as dt
import os
import random
import sqlite3
import string
import uuid
from functools import wraps

import bcrypt
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("VOTING_DB_PATH", os.path.join(BASE_DIR, "database.db"))
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "university-demo-token")
MEDIA_DIR = os.path.join(BASE_DIR, "media")
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


def db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def rows(items):
    return [dict(item) for item in items]


def protected(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Service-Token") != SERVICE_TOKEN:
            return jsonify(error="Unauthorized service"), 401
        return fn(*args, **kwargs)
    return wrapper


def fail(message, status=400):
    return jsonify(error=message), status


def init_db():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS admin (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
      password TEXT NOT NULL, firstname TEXT NOT NULL, lastname TEXT NOT NULL, photo TEXT DEFAULT '', created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS voters (id INTEGER PRIMARY KEY AUTOINCREMENT, voters_id TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL, firstname TEXT NOT NULL, lastname TEXT NOT NULL, photo TEXT DEFAULT '', has_voted INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS elections (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
      deadline TIMESTAMP NOT NULL, created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY AUTOINCREMENT, election_id INTEGER NOT NULL,
      description TEXT NOT NULL, max_vote INTEGER NOT NULL DEFAULT 1, priority INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY(election_id) REFERENCES elections(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER NOT NULL,
      firstname TEXT NOT NULL, lastname TEXT NOT NULL, photo TEXT DEFAULT '', platform TEXT DEFAULT '', biography TEXT DEFAULT '',
      FOREIGN KEY(position_id) REFERENCES positions(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS votes (id INTEGER PRIMARY KEY AUTOINCREMENT, voters_id INTEGER NOT NULL,
      election_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL, position_id INTEGER NOT NULL,
      FOREIGN KEY(voters_id) REFERENCES voters(id) ON DELETE CASCADE,
      FOREIGN KEY(election_id) REFERENCES elections(id) ON DELETE CASCADE,
      FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE,
      FOREIGN KEY(position_id) REFERENCES positions(id) ON DELETE CASCADE);
    CREATE UNIQUE INDEX IF NOT EXISTS uq_vote_choice ON votes(voters_id,election_id,position_id,candidate_id);
    """)
    if not con.execute("SELECT 1 FROM elections LIMIT 1").fetchone():
        con.execute("INSERT INTO elections(title,deadline) VALUES(?,?)", ("Original Election", dt.datetime.now() + dt.timedelta(days=3650)))
    if not con.execute("SELECT 1 FROM admin LIMIT 1").fetchone():
        password = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
        con.execute("INSERT INTO admin(username,password,firstname,lastname) VALUES(?,?,?,?)", ("admin", password, "Admin", "User"))
    con.commit(); con.close()


@app.get("/api/health")
def health():
    try:
        con = db(); con.execute("SELECT 1"); con.close()
        return jsonify(service="database", status="ok")
    except sqlite3.Error as exc:
        return fail(str(exc), 503)


@app.post("/api/media")
@protected
def media_upload():
    """Receive shared candidate media from a UI service."""
    photo = request.files.get("photo")
    if not photo or not photo.filename:
        return fail("An image file is required")
    original = secure_filename(photo.filename)
    extension = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return fail("Only JPG, JPEG, PNG, GIF, and WEBP images are allowed")
    filename = f"{uuid.uuid4().hex[:12]}_{original}"
    os.makedirs(MEDIA_DIR, exist_ok=True)
    photo.save(os.path.join(MEDIA_DIR, filename))
    return jsonify(filename=filename, url=f"/media/{filename}"), 201


@app.get("/media/<path:filename>")
def media_file(filename):
    """Public read endpoint used by both voter and admin browsers."""
    return send_from_directory(MEDIA_DIR, filename)


@app.post("/api/auth/voter")
@protected
def voter_auth():
    data = request.get_json(silent=True) or {}; con = db()
    voter = con.execute("SELECT * FROM voters WHERE voters_id=?", ((data.get("voters_id") or "").strip(),)).fetchone(); con.close()
    if not voter or not bcrypt.checkpw((data.get("password") or "").encode(), voter["password"].encode()):
        return fail("Invalid Voter ID or password", 401)
    result = dict(voter); result.pop("password", None)
    return jsonify(voter=result)


@app.post("/api/auth/admin")
@protected
def admin_auth():
    data = request.get_json(silent=True) or {}; con = db()
    admin = con.execute("SELECT * FROM admin WHERE username=?", ((data.get("username") or "").strip(),)).fetchone(); con.close()
    if not admin or not bcrypt.checkpw((data.get("password") or "").encode(), admin["password"].encode()):
        return fail("Invalid admin credentials", 401)
    result = dict(admin); result.pop("password", None)
    return jsonify(admin=result)


@app.post("/api/voters/register")
@protected
def voter_register():
    data = request.get_json(silent=True) or {}
    if not data.get("firstname") or not data.get("lastname") or len(data.get("password", "")) < 6:
        return fail("First name, last name, and a 6-character password are required")
    con = db()
    try:
        voter_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=15))
        password = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
        cur = con.execute("INSERT INTO voters(voters_id,password,firstname,lastname,photo) VALUES(?,?,?,?,?)",
                          (voter_id, password, data["firstname"].strip(), data["lastname"].strip(), data.get("photo", "")))
        con.commit(); return jsonify(id=cur.lastrowid, voters_id=voter_id), 201
    except sqlite3.IntegrityError as exc:
        con.rollback(); return fail(str(exc), 409)
    finally: con.close()


@app.route("/api/voters/<int:item_id>", methods=["GET", "PUT", "DELETE"])
@protected
def voter_item(item_id):
    con = db()
    if request.method == "GET":
        item = con.execute("SELECT id,voters_id,firstname,lastname,photo,has_voted FROM voters WHERE id=?", (item_id,)).fetchone(); con.close()
        return jsonify(voter=dict(item)) if item else fail("Voter not found", 404)
    if request.method == "DELETE":
        con.execute("DELETE FROM voters WHERE id=?", (item_id,)); con.commit(); con.close(); return jsonify(message="Voter deleted")
    data = request.get_json(silent=True) or {}; fields=[]; values=[]
    for name in ("firstname", "lastname", "photo"):
        if name in data: fields.append(name+"=?"); values.append(data[name])
    if data.get("password"):
        fields.append("password=?"); values.append(bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode())
    if not fields: con.close(); return fail("No fields supplied")
    values.append(item_id); con.execute("UPDATE voters SET "+",".join(fields)+" WHERE id=?", values); con.commit(); con.close()
    return jsonify(message="Voter updated")


@app.route("/api/voters", methods=["GET", "POST"])
@protected
def voters():
    if request.method == "POST": return voter_register()
    con=db(); data=rows(con.execute("SELECT id,voters_id,firstname,lastname,photo,has_voted FROM voters ORDER BY lastname,firstname")); con.close()
    return jsonify(voters=data)


@app.get("/api/elections")
@protected
def elections():
    con=db(); data=rows(con.execute("SELECT * FROM elections ORDER BY deadline DESC")); con.close(); return jsonify(elections=data)


@app.post("/api/elections")
@protected
def elections_add():
    data=request.get_json(silent=True) or {}
    if not data.get("title") or not data.get("deadline"): return fail("Title and deadline are required")
    try: dt.datetime.fromisoformat(data["deadline"].replace("T", " "))
    except ValueError: return fail("Invalid deadline")
    con=db(); cur=con.execute("INSERT INTO elections(title,deadline) VALUES(?,?)", (data["title"].strip(), data["deadline"].replace("T", " "))); con.commit(); con.close()
    return jsonify(id=cur.lastrowid, message="Election added"), 201


@app.delete("/api/elections/<int:item_id>")
@protected
def elections_delete(item_id):
    con=db(); con.execute("DELETE FROM elections WHERE id=?", (item_id,)); con.commit(); con.close(); return jsonify(message="Election deleted")


@app.route("/api/positions", methods=["GET", "POST"])
@protected
def positions():
    if request.method == "GET":
        election_id=request.args.get("election_id"); con=db()
        query="SELECT * FROM positions"+(" WHERE election_id=?" if election_id else "")+" ORDER BY priority,id"
        data=rows(con.execute(query, (election_id,) if election_id else ())); con.close(); return jsonify(positions=data)
    data=request.get_json(silent=True) or {}
    try: max_vote=int(data.get("max_vote",1)); election_id=int(data["election_id"])
    except (ValueError,TypeError,KeyError): return fail("Valid election and maximum vote are required")
    if not data.get("description") or max_vote < 1: return fail("Description and maximum vote are required")
    con=db(); priority=con.execute("SELECT COALESCE(MAX(priority),0)+1 FROM positions WHERE election_id=?",(election_id,)).fetchone()[0]
    cur=con.execute("INSERT INTO positions(election_id,description,max_vote,priority) VALUES(?,?,?,?)",(election_id,data["description"].strip(),max_vote,priority)); con.commit(); con.close()
    return jsonify(id=cur.lastrowid,message="Position added"),201


@app.route("/api/positions/<int:item_id>", methods=["GET","PUT","DELETE"])
@protected
def position_item(item_id):
    con=db()
    if request.method=="GET":
        item=con.execute("SELECT * FROM positions WHERE id=?",(item_id,)).fetchone(); con.close(); return jsonify(position=dict(item)) if item else fail("Position not found",404)
    if request.method=="DELETE": con.execute("DELETE FROM positions WHERE id=?",(item_id,)); con.commit(); con.close(); return jsonify(message="Position deleted")
    data=request.get_json(silent=True) or {}
    try: max_vote=int(data.get("max_vote"))
    except (ValueError,TypeError): con.close(); return fail("Maximum vote must be a whole number")
    con.execute("UPDATE positions SET description=?,max_vote=? WHERE id=?",(data.get("description",""),max_vote,item_id)); con.commit(); con.close(); return jsonify(message="Position updated")


@app.post("/api/positions/<int:item_id>/move")
@protected
def position_move(item_id):
    direction=(request.get_json(silent=True) or {}).get("direction"); con=db(); current=con.execute("SELECT * FROM positions WHERE id=?",(item_id,)).fetchone()
    if not current: con.close(); return fail("Position not found",404)
    op="<" if direction=="up" else ">"; order="DESC" if direction=="up" else "ASC"
    other=con.execute(f"SELECT * FROM positions WHERE election_id=? AND priority {op} ? ORDER BY priority {order} LIMIT 1",(current["election_id"],current["priority"])).fetchone()
    if other:
        con.execute("UPDATE positions SET priority=? WHERE id=?",(other["priority"],item_id)); con.execute("UPDATE positions SET priority=? WHERE id=?",(current["priority"],other["id"])); con.commit()
    con.close(); return jsonify(message="Position order updated")


@app.route("/api/candidates", methods=["GET","POST"])
@protected
def candidates():
    if request.method=="GET":
        election_id=request.args.get("election_id"); con=db(); params=()
        query="SELECT c.*,p.description AS position_desc,p.election_id FROM candidates c JOIN positions p ON p.id=c.position_id"
        if election_id: query+=" WHERE p.election_id=?"; params=(election_id,)
        query+=" ORDER BY p.priority,c.lastname,c.firstname"; data=rows(con.execute(query,params)); con.close(); return jsonify(candidates=data)
    data=request.get_json(silent=True) or {}
    if not all(data.get(x) for x in ("position_id","firstname","lastname")): return fail("Position, first name, and last name are required")
    con=db(); cur=con.execute("INSERT INTO candidates(position_id,firstname,lastname,photo,platform,biography) VALUES(?,?,?,?,?,?)",
      (data["position_id"],data["firstname"].strip(),data["lastname"].strip(),data.get("photo",""),data.get("platform",""),data.get("biography",""))); con.commit(); con.close()
    return jsonify(id=cur.lastrowid,message="Candidate added"),201


@app.route("/api/candidates/<int:item_id>", methods=["GET","PUT","DELETE"])
@protected
def candidate_item(item_id):
    con=db()
    if request.method=="GET":
        item=con.execute("SELECT c.*,p.description AS position_desc,p.election_id FROM candidates c JOIN positions p ON p.id=c.position_id WHERE c.id=?",(item_id,)).fetchone(); con.close(); return jsonify(candidate=dict(item)) if item else fail("Candidate not found",404)
    if request.method=="DELETE": con.execute("DELETE FROM candidates WHERE id=?",(item_id,)); con.commit(); con.close(); return jsonify(message="Candidate deleted")
    data=request.get_json(silent=True) or {}; old=con.execute("SELECT * FROM candidates WHERE id=?",(item_id,)).fetchone()
    if not old: con.close(); return fail("Candidate not found",404)
    con.execute("UPDATE candidates SET position_id=?,firstname=?,lastname=?,photo=?,platform=?,biography=? WHERE id=?",
      tuple(data.get(k,old[k]) for k in ("position_id","firstname","lastname","photo","platform","biography"))+(item_id,)); con.commit(); con.close(); return jsonify(message="Candidate updated")


def build_results(con,election_id):
    output=[]
    for pos in con.execute("SELECT * FROM positions WHERE election_id=? ORDER BY priority,id",(election_id,)):
        cs=rows(con.execute("SELECT c.*,COUNT(v.id) vote_count FROM candidates c LEFT JOIN votes v ON v.candidate_id=c.id AND v.election_id=? WHERE c.position_id=? GROUP BY c.id ORDER BY vote_count DESC,c.lastname,c.firstname",(election_id,pos["id"])))
        high=int(cs[0]["vote_count"]) if cs else -1; winners=[c for c in cs if int(c["vote_count"])==high] if cs else []
        output.append({"position":dict(pos),"candidates":cs,"winner":winners[0] if len(winners)==1 else None,"winners":winners,"is_tie":len(winners)>1,"total_votes":sum(int(c["vote_count"]) for c in cs)})
    return output


@app.get("/api/ballot")
@protected
def ballot():
    voter_id=request.args.get("voter_id"); con=db(); election_data=[]
    for election in con.execute("SELECT * FROM elections ORDER BY deadline DESC"):
        e=dict(election); deadline=dt.datetime.fromisoformat(str(e["deadline"])); e["is_open"]=deadline>dt.datetime.now(); e["deadline_ms"]=int(deadline.timestamp()*1000); e["positions"]=[]
        for pos in con.execute("SELECT * FROM positions WHERE election_id=? ORDER BY priority,id",(e["id"],)):
            p=dict(pos); p["candidates"]=rows(con.execute("SELECT * FROM candidates WHERE position_id=? ORDER BY lastname,firstname",(p["id"],)))
            previous=rows(con.execute("SELECT candidate_id FROM votes WHERE voters_id=? AND election_id=? AND position_id=?",(voter_id,e["id"],p["id"]))) if voter_id else []
            p["previous_candidate_ids"]=[x["candidate_id"] for x in previous]; p["votes_used"]=len(previous); p["position_locked"]=len(previous)>=p["max_vote"]; e["positions"].append(p)
        e["has_voted"]=bool(e["positions"]) and all(p["position_locked"] for p in e["positions"]); e["results"]=[] if e["is_open"] else build_results(con,e["id"]); election_data.append(e)
    con.close(); return jsonify(elections=election_data)


@app.post("/api/votes")
@protected
def submit_votes():
    data=request.get_json(silent=True) or {}; voter_id=data.get("voter_id"); election_id=data.get("election_id"); selections=data.get("selections") or {}
    con=db()
    try:
        con.execute("BEGIN IMMEDIATE"); election=con.execute("SELECT * FROM elections WHERE id=?",(election_id,)).fetchone()
        if not election or dt.datetime.fromisoformat(str(election["deadline"]))<=dt.datetime.now(): raise ValueError("This election is closed and no longer accepts votes")
        pending=[]
        for position_id,candidate_ids in selections.items():
            pos=con.execute("SELECT * FROM positions WHERE id=? AND election_id=?",(position_id,election_id)).fetchone()
            if not pos: raise ValueError("Invalid position")
            candidate_ids=list(dict.fromkeys(int(x) for x in candidate_ids)); used=con.execute("SELECT COUNT(*) FROM votes WHERE voters_id=? AND election_id=? AND position_id=?",(voter_id,election_id,position_id)).fetchone()[0]
            if used+len(candidate_ids)>pos["max_vote"]: raise ValueError(f'You can only choose {pos["max_vote"]} candidates for {pos["description"]}')
            for cid in candidate_ids:
                if not con.execute("SELECT 1 FROM candidates WHERE id=? AND position_id=?",(cid,position_id)).fetchone(): raise ValueError("Invalid candidate selection")
                pending.append((voter_id,election_id,cid,position_id))
        if not pending: raise ValueError("Please vote for at least one candidate")
        con.executemany("INSERT INTO votes(voters_id,election_id,candidate_id,position_id) VALUES(?,?,?,?)",pending); con.commit(); return jsonify(message="Ballot Submitted"),201
    except (ValueError,sqlite3.IntegrityError) as exc: con.rollback(); return fail(str(exc),409)
    finally: con.close()


@app.get("/api/votes")
@protected
def votes():
    election_id=request.args.get("election_id"); con=db(); params=()
    query="""SELECT v.*,e.title election_title,p.description position_desc,c.firstname canfirst,c.lastname canlast,u.firstname votfirst,u.lastname votlast FROM votes v JOIN elections e ON e.id=v.election_id JOIN positions p ON p.id=v.position_id JOIN candidates c ON c.id=v.candidate_id JOIN voters u ON u.id=v.voters_id"""
    if election_id: query+=" WHERE v.election_id=?"; params=(election_id,)
    data=rows(con.execute(query+" ORDER BY v.id DESC",params)); con.close(); return jsonify(votes=data)


@app.delete("/api/votes")
@protected
def votes_reset():
    election_id=request.args.get("election_id"); con=db()
    if election_id: con.execute("DELETE FROM votes WHERE election_id=?",(election_id,))
    else: con.execute("DELETE FROM votes")
    con.execute("UPDATE voters SET has_voted=0"); con.commit(); con.close(); return jsonify(message="Votes reset; all voters can vote again")


@app.get("/api/results")
@protected
def results():
    election_id=request.args.get("election_id"); con=db()
    if not election_id:
        latest=con.execute("SELECT id FROM elections ORDER BY deadline DESC LIMIT 1").fetchone(); election_id=latest["id"] if latest else None
    result=build_results(con,election_id) if election_id else []; con.close(); return jsonify(election_id=int(election_id) if election_id else None,results=result)


@app.get("/api/dashboard")
@protected
def dashboard():
    election_id=request.args.get("election_id"); con=db(); election=con.execute("SELECT * FROM elections WHERE id=?",(election_id,)).fetchone() if election_id else con.execute("SELECT * FROM elections ORDER BY deadline DESC LIMIT 1").fetchone()
    if not election: con.close(); return fail("Election not found",404)
    eid=election["id"]; payload={"selected_election":dict(election),"positions_count":con.execute("SELECT COUNT(*) FROM positions WHERE election_id=?",(eid,)).fetchone()[0],"candidates_count":con.execute("SELECT COUNT(*) FROM candidates c JOIN positions p ON p.id=c.position_id WHERE p.election_id=?",(eid,)).fetchone()[0],"voters_count":con.execute("SELECT COUNT(*) FROM voters").fetchone()[0],"votes_count":con.execute("SELECT COUNT(DISTINCT voters_id) FROM votes WHERE election_id=?",(eid,)).fetchone()[0],"results":build_results(con,eid)}; con.close(); return jsonify(payload)


@app.get("/api/admin/<int:item_id>")
@protected
def admin_get(item_id):
    con=db(); item=con.execute("SELECT id,username,firstname,lastname,photo,created_on FROM admin WHERE id=?",(item_id,)).fetchone(); con.close(); return jsonify(admin=dict(item)) if item else fail("Admin not found",404)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5002, debug=False)
