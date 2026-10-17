# Online Voting System - Python Flask Version

This is a Python Flask conversion of the original PHP Online Voting System.

## Requirements

- Python 3.7+
- SQLite database included as `voting.db`

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. The SQLite database is created automatically on first run. Static assets are already included.
```bash
# Start the application
python app.py
```

## Running the Application

```bash
python app.py
```

The application will be available at: http://127.0.0.1:5000

## Features

### Voter Features
- Login with Voter ID and Password
- View all open elections with live deadline countdowns
- Cast one ballot per open election
- View winners and results for closed elections
- Preview ballot before submission
- View already cast votes

### Admin Features
- Admin login at `/admin/`
- Create multiple elections with deadlines from the Elections page
- Select an election before managing its positions and candidates
- View all open and closed elections with winner summaries
- Dashboard with statistics
- Manage Voters (CRUD)
- Manage Positions (CRUD)
- Manage Candidates (CRUD)
- View and Reset Votes
- Reorder positions for the selected election
- Admin Profile Management

## Database

The application uses the local SQLite database `voting.db`:
- `admin` - Admin users
- `voters` - Registered voters
- `positions` - Election positions
- `positions.election_id` - Associates each position with one election
- `candidates` - Candidates for each position
- `votes` - Cast votes linked to an election
- `elections` - Election titles and deadlines

## Notes

- Existing databases are migrated automatically with an `elections` table and a default election for existing votes
- Passwords are hashed using bcrypt
- File uploads are stored in `static/images/`
