"""Administrator UI service. It never imports a database driver or executes SQL."""
import datetime as dt
import os
from urllib.parse import quote
from functools import wraps
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename
from api_client import APIError, DatabaseUnavailable, call
import base64
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

app=Flask(__name__); app.secret_key=os.environ.get("ADMIN_SECRET_KEY","admin-service-demo-secret")
app.config["UPLOAD_FOLDER"]=os.path.join(os.path.dirname(__file__),"static","images")

def database_media_url(filename):
    if not filename:
        return url_for("static", filename="images/profile.jpg")
    public_url=os.environ.get("DATABASE_MEDIA_PUBLIC_URL")
    if not public_url:
        api_url=os.environ.get("DATABASE_SERVICE_URL","http://127.0.0.1:5002/api").rstrip("/")
        public_url=api_url[:-4] if api_url.endswith("/api") else api_url
    return f'{public_url.rstrip("/")}/media/{quote(filename)}'

def required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        if not session.get("admin"): flash("Your admin session has expired. Please log in again.","error"); return redirect(url_for("admin_index"))
        return fn(*a,**kw)
    return wrap
def safe(method,path,**kwargs): return call(method,path,**kwargs)
def error_redirect(exc,endpoint): flash(str(exc),"error"); return redirect(url_for(endpoint))
def selected_id(elections):
    ids=[int(e["id"]) for e in elections]
    try: value=int(request.values.get("election_id") or session.get("selected_election_id") or (ids[0] if ids else 0))
    except ValueError: value=ids[0] if ids else 0
    if value not in ids and ids: value=ids[0]
    session["selected_election_id"]=value; return value
def admin_context():
    admin=None; elections=[]
    if session.get("admin"):
        try:
            admin=safe("GET",f'/admin/{session["admin"]}')["admin"]
            if admin.get("created_on"):
                try: admin["created_on"]=dt.datetime.fromisoformat(admin["created_on"])
                except ValueError: admin["created_on"]=None
            elections=safe("GET","/elections")["elections"]
        except (APIError,DatabaseUnavailable): pass
    sid=session.get("selected_election_id") or (elections[0]["id"] if elections else None)
    return dict(admin_user=admin,admin_elections=elections,selected_admin_election_id=sid,election_title="ONLINE VOTING SYSTEM",database_media_url=database_media_url)
app.context_processor(admin_context)

@app.get("/")
@app.get("/admin/")
def admin_index(): return redirect(url_for("admin_home")) if session.get("admin") else render_template("admin/index.html")
@app.get("/register")
def register():
    voting_url=os.environ.get("VOTING_PUBLIC_URL","http://127.0.0.1:5000").rstrip("/")
    return redirect(f"{voting_url}/register")
@app.get("/voter")
def index():
    voting_url=os.environ.get("VOTING_PUBLIC_URL","http://127.0.0.1:5000").rstrip("/")
    return redirect(f"{voting_url}/")
@app.post("/admin/login")
def admin_login():
    try:
        data=safe("POST","/auth/admin",json={"username":request.form.get("username",""),"password":request.form.get("password","")})
        session["admin"]=data["admin"]["id"]; return redirect(url_for("admin_home"))
    except (APIError,DatabaseUnavailable) as exc: return error_redirect(exc,"admin_index")
@app.get("/admin/logout")
def admin_logout(): session.clear(); return redirect(url_for("admin_index"))

@app.get("/admin/home")
@required
def admin_home():
    try:
        elections = safe("GET", "/elections")["elections"]
        eid = selected_id(elections)

        data = safe("GET", f"/dashboard?election_id={eid}")
        results = data.get("results", [])

        pie_charts = {}

        for result in results:
            candidates = result.get("candidates", [])

            # Only include candidates who actually received votes
            chart_candidates = [
                candidate
                for candidate in candidates
                if int(candidate.get("vote_count", 0)) > 0
            ]

            if not chart_candidates:
                pie_charts[result["position"]["id"]] = None
                continue

            labels = [
                f'{candidate["firstname"]} {candidate["lastname"]}'
                for candidate in chart_candidates
            ]

            values = [
                int(candidate["vote_count"])
                for candidate in chart_candidates
            ]

            fig, ax = plt.subplots(figsize=(4.2, 3.0))

            ax.pie(
                values,
                labels=labels,
                autopct="%1.1f%%",
                startangle=90,
                textprops={"fontsize": 8}
            )

            ax.set_title(
                result["position"]["description"],
                fontsize=10,
                fontweight="bold"
            )

            plt.tight_layout()

            image = io.BytesIO()

            plt.savefig(
                image,
                format="png",
                bbox_inches="tight",
                dpi=100
            )

            plt.close(fig)

            image.seek(0)

            chart_image = base64.b64encode(
                image.getvalue()
            ).decode("utf-8")

            pie_charts[result["position"]["id"]] = chart_image

        return render_template(
            "admin/home.html",
            pie_charts=pie_charts,
            **data
        )

    except (APIError, DatabaseUnavailable) as exc:
        return error_redirect(exc, "admin_index")
@app.route("/admin/elections/select", methods=["GET", "POST"])
@required
def admin_elections_select():
    session["selected_election_id"]=request.values.get("election_id",type=int); target=request.values.get("next","")
    return redirect(target if target.startswith("/admin/") else url_for("admin_home"))

@app.get("/admin/elections")
@required
def admin_elections():
    try:
        elections=safe("GET","/elections")["elections"]; return render_template("admin/elections.html",elections=elections,selected_election_id=selected_id(elections))
    except (APIError,DatabaseUnavailable) as exc: return error_redirect(exc,"admin_home")
@app.post("/admin/elections/add")
@required
def admin_elections_add():
    try: safe("POST","/elections",json={"title":request.form.get("title"),"deadline":request.form.get("deadline")}); flash("Election added successfully","success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_elections"))
@app.post("/admin/elections/delete")
@required
def admin_elections_delete():
    try: safe("DELETE",f'/elections/{request.form.get("id")}'); flash("Election deleted successfully","success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_elections"))

@app.get("/admin/voters")
@required
def admin_voters():
    try: return render_template("admin/voters.html",voters=safe("GET","/voters")["voters"])
    except (APIError,DatabaseUnavailable) as exc: return error_redirect(exc,"admin_home")
@app.post("/admin/voters/add")
@required
def admin_voters_add():
    try:
        data=safe("POST","/voters/register",json={"firstname":request.form.get("firstname"),"lastname":request.form.get("lastname"),"password":request.form.get("password","")})
        flash(f'Voter added. Voter ID: {data["voters_id"]}; Password: {request.form.get("password")}',"success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_voters"))
@app.post("/admin/voters/edit")
@required
def admin_voters_edit(): return update_voter({"firstname":request.form.get("firstname"),"lastname":request.form.get("lastname")})
@app.post("/admin/voters/photo")
@required
def admin_voters_photo():
    photo=request.files.get("photo"); payload={}
    if photo and photo.filename:
        name=secure_filename(photo.filename); photo.save(os.path.join(app.config["UPLOAD_FOLDER"],name)); payload["photo"]=name
    return update_voter(payload)
def update_voter(payload):
    try: safe("PUT",f'/voters/{request.form.get("id")}',json=payload); flash("Voter updated successfully","success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_voters"))
@app.post("/admin/voters/delete")
@required
def admin_voters_delete():
    try: safe("DELETE",f'/voters/{request.form.get("id")}'); flash("Voter deleted successfully","success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_voters"))
@app.post("/admin/voters/row")
@required
def admin_voters_row():
    try: return jsonify(safe("GET",f'/voters/{request.form.get("id")}')["voter"])
    except (APIError,DatabaseUnavailable) as exc: return jsonify(error=str(exc)),503

@app.get("/admin/positions")
@required
def admin_positions():
    try:
        elections=safe("GET","/elections")["elections"]; eid=selected_id(elections); items=safe("GET",f"/positions?election_id={eid}")["positions"]
        return render_template("admin/positions.html",positions=items,selected_election_id=eid)
    except (APIError,DatabaseUnavailable) as exc: return error_redirect(exc,"admin_home")
@app.post("/admin/positions/add")
@required
def admin_positions_add(): return position_change("POST","/positions","Position added successfully")
@app.post("/admin/positions/edit")
@required
def admin_positions_edit(): return position_change("PUT",f'/positions/{request.form.get("id")}',"Position updated successfully")
def position_change(method,path,message):
    try: safe(method,path,json={"election_id":request.form.get("election_id") or session.get("selected_election_id"),"description":request.form.get("description"),"max_vote":request.form.get("max_vote")}); flash(message,"success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_positions"))
@app.post("/admin/positions/delete")
@required
def admin_positions_delete():
    try: safe("DELETE",f'/positions/{request.form.get("id")}'); flash("Position deleted successfully","success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_positions"))
@app.post("/admin/positions/row")
@required
def admin_positions_row():
    try: return jsonify(safe("GET",f'/positions/{request.form.get("id")}')["position"])
    except (APIError,DatabaseUnavailable) as exc: return jsonify(error=str(exc)),503

@app.get("/admin/candidates")
@required
def admin_candidates():
    try:
        elections=safe("GET","/elections")["elections"]; eid=selected_id(elections)
        return render_template("admin/candidates.html",candidates=safe("GET",f"/candidates?election_id={eid}")["candidates"],positions=safe("GET",f"/positions?election_id={eid}")["positions"],selected_election_id=eid)
    except (APIError,DatabaseUnavailable) as exc: return error_redirect(exc,"admin_home")
def candidate_payload(include_photo=True):
    payload={k:request.form.get(k) for k in ("firstname","lastname","platform","biography") if request.form.get(k) is not None}
    if request.form.get("position") is not None: payload["position_id"]=request.form.get("position")
    photo=request.files.get("photo")
    if include_photo and photo and photo.filename:
        uploaded=safe("POST","/media",files={"photo":(secure_filename(photo.filename),photo.stream,photo.mimetype)})
        payload["photo"]=uploaded["filename"]
    return payload
@app.post("/admin/candidates/add")
@required
def admin_candidates_add(): return candidate_change("POST","/candidates",candidate_payload(),"Candidate added successfully")
@app.post("/admin/candidates/edit")
@required
def admin_candidates_edit(): return candidate_change("PUT",f'/candidates/{request.form.get("id")}',candidate_payload(False),"Candidate updated successfully")
@app.post("/admin/candidates/photo")
@required
def admin_candidates_photo(): return candidate_change("PUT",f'/candidates/{request.form.get("id")}',candidate_payload(),"Candidate photo updated")
def candidate_change(method,path,payload,message):
    try: safe(method,path,json=payload); flash(message,"success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_candidates"))
@app.post("/admin/candidates/delete")
@required
def admin_candidates_delete():
    try: safe("DELETE",f'/candidates/{request.form.get("id")}'); flash("Candidate deleted successfully","success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_candidates"))
@app.post("/admin/candidates/row")
@required
def admin_candidates_row():
    try:
        c=safe("GET",f'/candidates/{request.form.get("id")}')["candidate"]; c["position_id"]=c.get("position_id"); c["position_desc"]=c.get("position_desc"); return jsonify(c)
    except (APIError,DatabaseUnavailable) as exc: return jsonify(error=str(exc)),503

@app.get("/admin/votes")
@required
def admin_votes():
    try:
        elections=safe("GET","/elections")["elections"]; eid=selected_id(elections); return render_template("admin/votes.html",votes=safe("GET",f"/votes?election_id={eid}")["votes"])
    except (APIError,DatabaseUnavailable) as exc: return error_redirect(exc,"admin_home")
@app.post("/admin/votes/reset")
@required
def admin_votes_reset():
    try: safe("DELETE",f'/votes?election_id={session.get("selected_election_id","")}'); flash("All voters can vote again; votes were reset.","success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("admin_votes"))
@app.get("/admin/ballot")
@required
def admin_ballot():
    try:
        elections=safe("GET","/elections")["elections"]; eid=selected_id(elections); selected=next((e for e in elections if e["id"]==eid),{})
        return render_template("admin/ballot.html",elections=elections,selected_election_id=eid,selected_election=selected)
    except (APIError,DatabaseUnavailable) as exc: return error_redirect(exc,"admin_home")
@app.post("/admin/ballot/fetch")
@required
def admin_ballot_fetch():
    try:
        positions=safe("GET",f'/positions?election_id={request.form.get("election_id")}')["positions"]
        html='<div class="box box-solid"><div class="box-body"><table class="table table-bordered"><tr><th>Position</th><th>Max Votes</th><th>Order</th></tr>'
        for p in positions: html+=f'<tr><td>{p["description"]}</td><td>{p["max_vote"]}</td><td><button class="btn btn-sm btn-default move-up" data-id="{p["id"]}">Up</button> <button class="btn btn-sm btn-default move-down" data-id="{p["id"]}">Down</button></td></tr>'
        return jsonify(html+'</table></div></div>')
    except (APIError,DatabaseUnavailable) as exc: return jsonify(f'<div class="alert alert-danger">{exc}</div>'), 503
@app.post("/admin/ballot/up")
@required
def admin_ballot_up(): return move("up")
@app.post("/admin/ballot/down")
@required
def admin_ballot_down(): return move("down")
def move(direction):
    try: return jsonify(safe("POST",f'/positions/{request.form.get("id")}/move',json={"direction":direction}))
    except (APIError,DatabaseUnavailable) as exc: return jsonify(error=str(exc)),503

# Compatibility endpoints retained for unchanged templates.
@app.post("/admin/config/save")
@required
def admin_config_save(): flash("Display settings saved for this service session.","success"); return redirect(url_for("admin_home"))
@app.post("/admin/profile/update")
@required
def admin_profile_update(): flash("Admin profile editing remains database-service controlled.","error"); return redirect(url_for("admin_home"))

if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5001")),debug=False)
