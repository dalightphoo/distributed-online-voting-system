import os
import requests
BASE_URL=os.environ.get("DATABASE_SERVICE_URL","http://127.0.0.1:5002/api").rstrip("/")
TOKEN=os.environ.get("SERVICE_TOKEN","university-demo-token")
class DatabaseUnavailable(Exception): pass
class APIError(Exception): pass
def call(method,path,**kwargs):
    try: response=requests.request(method,BASE_URL+path,headers={"X-Service-Token":TOKEN},timeout=3,**kwargs)
    except requests.RequestException as exc: raise DatabaseUnavailable("Database Service is currently unavailable. Please try again later.") from exc
    try: data=response.json()
    except ValueError: data={}
    if not response.ok: raise APIError(data.get("error",f"Database Service returned HTTP {response.status_code}"))
    return data
