"""Voter-facing Flask service. All persistent data comes from REST calls."""
import os, re
from urllib.parse import quote
from functools import wraps
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename
from api_client import APIError, DatabaseUnavailable, call

app=Flask(__name__); app.secret_key=os.environ.get("VOTING_SECRET_KEY","voting-service-demo-secret")
app.config["UPLOAD_FOLDER"]=os.path.join(os.path.dirname(__file__),"static","images")
# Browser cookies are scoped by host, not by TCP port. A unique name keeps
# this portal's login independent when another replica runs on 127.0.0.1.
app.config["SESSION_COOKIE_NAME"] = os.environ.get(
    "VOTING_SESSION_COOKIE_NAME", "voting_session_5000"
)

def slugify(text):
    text=re.sub(r'\b(?:in|at|on|by|into|off|onto|from|to|with|a|an|the|using|for)\b','',text or '',flags=re.I)
    return re.sub(r'[-\s]+','-',re.sub(r'[^\w\s-]','',text).strip().lower())
def database_media_url(filename):
    if not filename:
        return url_for("static", filename="images/profile.jpg")
    api_url=os.environ.get("DATABASE_SERVICE_URL","http://127.0.0.1:5002/api").rstrip("/")
    return f'{api_url[:-4] if api_url.endswith("/api") else api_url}/media/{quote(filename)}'
@app.context_processor
def media_context(): return {"database_media_url": database_media_url}
def required(fn):
    @wraps(fn)
    def wrap(*a,**kw): return fn(*a,**kw) if session.get("voter") else redirect(url_for("index"))
    return wrap
def unavailable(exc, endpoint="index"):
    flash(str(exc),"error"); return redirect(url_for(endpoint))

@app.get("/")
def index(): return redirect(url_for("home")) if session.get("voter") else render_template("index.html")
@app.get("/admin/")
def admin_index(): return redirect("http://127.0.0.1:5001/admin/")
@app.post("/login")
def login():
    try:
        data=call("POST","/auth/voter",json={"voters_id":request.form.get("voter",""),"password":request.form.get("password","")})
        session["voter"]=data["voter"]["id"]; return redirect(url_for("home"))
    except (APIError,DatabaseUnavailable) as exc: return unavailable(exc)
@app.route("/register",methods=["GET","POST"])
def register():
    if request.method=="GET": return render_template("register.html")
    password=request.form.get("password","")
    if password != request.form.get("confirm_password",""): flash("Passwords do not match.","error"); return redirect(url_for("register"))
    try:
        data=call("POST","/voters/register",json={"firstname":request.form.get("firstname",""),"lastname":request.form.get("lastname",""),"password":password})
        flash(f'Registration successful. Your Voter ID is {data["voters_id"]}. Please save it.',"success"); return redirect(url_for("index"))
    except (APIError,DatabaseUnavailable) as exc: return unavailable(exc,"register")
@app.get("/logout")
def logout(): session.clear(); return redirect(url_for("index"))

@app.get("/home")
@required
def home():
    try:
        voter=call("GET",f'/voters/{session["voter"]}')["voter"]
        elections=call("GET",f'/ballot?voter_id={session["voter"]}')["elections"]
        return render_template("home.html",voter=voter,elections=elections,slugify=slugify,election_title="Online Voting System")
    except (APIError,DatabaseUnavailable) as exc: return unavailable(exc)
@app.get("/profile")
@required
def profile():
    try: return render_template("profile.html",voter=call("GET",f'/voters/{session["voter"]}')["voter"])
    except (APIError,DatabaseUnavailable) as exc: return unavailable(exc,"home")
@app.post("/profile/update")
@required
def profile_update():
    payload={"firstname":request.form.get("firstname",""),"lastname":request.form.get("lastname","")}
    password=request.form.get("password","")
    if password:
        if password != request.form.get("confirm_password",""): flash("The passwords do not match.","error"); return redirect(url_for("profile"))
        payload["password"]=password
    photo=request.files.get("photo")
    if photo and photo.filename:
        filename=secure_filename(photo.filename); photo.save(os.path.join(app.config["UPLOAD_FOLDER"],filename)); payload["photo"]=filename
    try: call("PUT",f'/voters/{session["voter"]}',json=payload); flash("Your profile has been updated successfully.","success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("profile"))

def selections_from_form(election):
    selected={}
    for position in election["positions"]:
        values=request.form.getlist(slugify(position["description"]))
        if values: selected[str(position["id"])]=values
    return selected
@app.post("/preview")
@required
def preview():
    try:
        elections=call("GET",f'/ballot?voter_id={session["voter"]}')["elections"]
        election=next((x for x in elections if str(x["id"])==request.form.get("election_id")),None)
        if not election or not election["is_open"]: return jsonify(error=True,message=["This election is closed."])
        html=""; errors=[]
        for position in election["positions"]:
            chosen=request.form.getlist(slugify(position["description"]))
            if len(chosen)>position["max_vote"]: errors.append(f'<li>You can only choose {position["max_vote"]} candidates for {position["description"]}</li>')
            for cid in chosen:
                candidate=next((c for c in position["candidates"] if str(c["id"])==cid),None)
                if candidate: html+=f'<div class="row votelist"><span class="col-sm-4"><span class="pull-right"><b>{position["description"]} :</b></span></span><span class="col-sm-8">{candidate["firstname"]} {candidate["lastname"]}</span></div>'
        return jsonify(error=bool(errors),message=errors,list=html)
    except (APIError,DatabaseUnavailable) as exc: return jsonify(error=True,message=[str(exc)]),503
@app.post("/submit_ballot")
@required
def submit_ballot():
    try:
        elections=call("GET",f'/ballot?voter_id={session["voter"]}')["elections"]
        election=next((x for x in elections if str(x["id"])==request.form.get("election_id")),None)
        if not election: raise APIError("Invalid election")
        data=call("POST","/votes",json={"voter_id":session["voter"],"election_id":election["id"],"selections":selections_from_form(election)})
        flash(data["message"],"success")
    except (APIError,DatabaseUnavailable) as exc: flash(str(exc),"error")
    return redirect(url_for("home"))

if __name__=="__main__":
    app.run(host="127.0.0.1",port=int(os.environ.get("VOTING_SERVICE_PORT","5000")),debug=False)
