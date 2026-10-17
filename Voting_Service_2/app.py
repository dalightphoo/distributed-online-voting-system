"""Second independent voter-service process, sharing no database connection."""
import os
import sys

PRIMARY_SERVICE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "Voting_Service")
)
sys.path.insert(0, PRIMARY_SERVICE)

from app import app  # reuses the preserved voter UI and REST client

# Both portals have the same hostname, so the browser would otherwise share
# Flask's default `session` cookie between ports 5000 and 5005.
app.config["SESSION_COOKIE_NAME"] = "voting_session_5005"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5005, debug=False)
