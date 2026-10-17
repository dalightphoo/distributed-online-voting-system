import io, os, pathlib, sys, tempfile, unittest

ROOT=pathlib.Path(__file__).resolve().parents[1]

class ArchitectureTests(unittest.TestCase):
    def test_voter_replicas_use_distinct_session_cookies(self):
        primary=(ROOT/"Voting_Service"/"app.py").read_text(encoding="utf-8")
        secondary=(ROOT/"Voting_Service_2"/"app.py").read_text(encoding="utf-8")
        self.assertIn('"voting_session_5000"',primary)
        self.assertIn('"voting_session_5005"',secondary)

    def test_only_database_service_uses_sqlite(self):
        for folder in ("Voting_Service","Voting_Service_2","Admin_Service"):
            text="\n".join(p.read_text(encoding="utf-8",errors="ignore") for p in (ROOT/folder).glob("*.py"))
            self.assertNotIn("sqlite3",text.lower())
            self.assertNotIn("sqlalchemy",text.lower())
        self.assertIn("sqlite3",(ROOT/"Database_Service"/"database_api.py").read_text(encoding="utf-8"))

    def test_database_api_core_flow(self):
        sys.path.insert(0,str(ROOT/"Database_Service"))
        import database_api
        handle,path=tempfile.mkstemp(suffix=".db"); os.close(handle)
        old=database_api.DB_PATH; database_api.DB_PATH=path
        try:
            database_api.init_db(); client=database_api.app.test_client(); headers={"X-Service-Token":database_api.SERVICE_TOKEN}
            self.assertEqual(client.get("/api/health").status_code,200)
            registration=client.post("/api/voters/register",headers=headers,json={"firstname":"Test","lastname":"Voter","password":"secret1"})
            self.assertEqual(registration.status_code,201)
            voter_id=registration.get_json()["id"]
            login=client.post("/api/auth/voter",headers=headers,json={"voters_id":registration.get_json()["voters_id"],"password":"secret1"})
            self.assertEqual(login.status_code,200)
            elections=client.get("/api/elections",headers=headers).get_json()["elections"]; eid=elections[0]["id"]
            position=client.post("/api/positions",headers=headers,json={"election_id":eid,"description":"President","max_vote":1}).get_json()["id"]
            candidate=client.post("/api/candidates",headers=headers,json={"position_id":position,"firstname":"Ada","lastname":"Lovelace"}).get_json()["id"]
            vote=client.post("/api/votes",headers=headers,json={"voter_id":voter_id,"election_id":eid,"selections":{str(position):[candidate]}})
            self.assertEqual(vote.status_code,201)
            self.assertEqual(client.get(f"/api/results?election_id={eid}",headers=headers).get_json()["results"][0]["candidates"][0]["vote_count"],1)
            self.assertEqual(client.post("/api/votes",headers=headers,json={"voter_id":voter_id,"election_id":eid,"selections":{str(position):[candidate]}}).status_code,409)
        finally:
            database_api.DB_PATH=old
            for suffix in ("","-wal","-shm"):
                try: os.unlink(path+suffix)
                except FileNotFoundError: pass

    def test_shared_candidate_media_upload_and_read(self):
        sys.path.insert(0,str(ROOT/"Database_Service"))
        import database_api
        old_media=database_api.MEDIA_DIR
        with tempfile.TemporaryDirectory() as media_dir:
            database_api.MEDIA_DIR=media_dir
            try:
                client=database_api.app.test_client()
                headers={"X-Service-Token":database_api.SERVICE_TOKEN}
                upload=client.post(
                    "/api/media",
                    headers=headers,
                    data={"photo":(io.BytesIO(b"demo-image"),"candidate.png")},
                    content_type="multipart/form-data",
                )
                self.assertEqual(upload.status_code,201)
                filename=upload.get_json()["filename"]
                self.assertEqual(client.get(f"/media/{filename}").status_code,200)
            finally:
                database_api.MEDIA_DIR=old_media

if __name__=="__main__": unittest.main()
