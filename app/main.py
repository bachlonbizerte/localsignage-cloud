import os, sqlite3, secrets, hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import jwt
from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from passlib.context import CryptContext

BASE=Path(__file__).resolve().parent.parent
DB=Path(os.getenv('DB_PATH', BASE/'data'/'localsignage.db'))
DB.parent.mkdir(parents=True, exist_ok=True)
SECRET=os.getenv('JWT_SECRET','CHANGE_ME_LOCALSIGNAGE_SECRET')
ADMIN_USER=os.getenv('ADMIN_USER','admin')
ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','admin123!')
pwd=CryptContext(schemes=['bcrypt'], deprecated='auto')
app=FastAPI(title='LocalSignage Cloud API', version='2.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
clients=set()

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, device_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, platform TEXT, version TEXT, token_hash TEXT, last_seen TEXT, online INTEGER DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pair_codes(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL, used INTEGER DEFAULT 0, created_at TEXT NOT NULL);
    ''')
    if not c.execute('SELECT 1 FROM users WHERE username=?',(ADMIN_USER,)).fetchone():
        c.execute('INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)',(ADMIN_USER,pwd.hash(ADMIN_PASSWORD),now()))
    c.commit(); c.close()

def now(): return datetime.now(timezone.utc).isoformat()
def token(payload): return jwt.encode(payload, SECRET, algorithm='HS256')
def auth(authorization: str=Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Authentication required')
    try: return jwt.decode(authorization[7:], SECRET, algorithms=['HS256'])
    except Exception: raise HTTPException(401,'Invalid or expired token')

def hash_token(t): return hashlib.sha256(t.encode()).hexdigest()

class Login(BaseModel): username:str; password:str
class PairStart(BaseModel): pass
class PairRequest(BaseModel): code:str; device_id:str; name:str='LocalSignage Player'; platform:str='android'; version:str='2.0.0'
class Heartbeat(BaseModel): device_id:str; version:str='2.0.0'; status:str='online'

@app.on_event('startup')
def startup(): init_db()

@app.get('/')
def root(): return FileResponse(BASE/'app'/'static'/'index.html')
@app.get('/health')
def health(): return {'ok':True,'service':'LocalSignage Cloud','version':'2.0.0','time':now()}
@app.get('/api/state')
def state():
    c=db(); n=c.execute('SELECT COUNT(*) FROM devices').fetchone()[0]; online=c.execute('SELECT COUNT(*) FROM devices WHERE online=1').fetchone()[0]; c.close()
    return {'version':'2.0.0','devices':n,'online':online}

@app.post('/api/auth/login')
def login(x:Login):
    c=db(); u=c.execute('SELECT * FROM users WHERE username=?',(x.username,)).fetchone(); c.close()
    if not u or not pwd.verify(x.password,u['password_hash']): raise HTTPException(401,'Invalid credentials')
    return {'access_token':token({'sub':u['username'],'exp':datetime.now(timezone.utc)+timedelta(hours=24)}),'token_type':'bearer'}

@app.post('/api/devices/pair/start')
def pair_start(_:dict=Depends(auth)):
    code=f'{secrets.randbelow(1000000):06d}'; exp=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
    c=db(); c.execute('INSERT INTO pair_codes(code,expires_at,created_at) VALUES(?,?,?)',(code,exp,now())); c.commit(); c.close()
    return {'code':code,'expires_at':exp}

@app.post('/api/devices/pair')
def pair(x:PairRequest):
    c=db(); row=c.execute('SELECT * FROM pair_codes WHERE code=? AND used=0 ORDER BY id DESC LIMIT 1',(x.code,)).fetchone()
    if not row or datetime.fromisoformat(row['expires_at']) < datetime.now(timezone.utc): c.close(); raise HTTPException(400,'Invalid or expired pairing code')
    device_token=secrets.token_urlsafe(40); h=hash_token(device_token)
    old=c.execute('SELECT id FROM devices WHERE device_id=?',(x.device_id,)).fetchone()
    if old: c.execute('UPDATE devices SET name=?,platform=?,version=?,token_hash=?,last_seen=?,online=1 WHERE device_id=?',(x.name,x.platform,x.version,h,now(),x.device_id))
    else: c.execute('INSERT INTO devices(device_id,name,platform,version,token_hash,last_seen,online,created_at) VALUES(?,?,?,?,?,?,?,?)',(x.device_id,x.name,x.platform,x.version,h,now(),1,now()))
    c.execute('UPDATE pair_codes SET used=1 WHERE id=?',(row['id'],)); c.commit(); c.close()
    return {'device_id':x.device_id,'device_token':device_token,'server_url':'/','message':'paired'}

@app.post('/api/devices/heartbeat')
def heartbeat(x:Heartbeat, authorization:str=Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Device token required')
    t=authorization[7:]; c=db(); d=c.execute('SELECT * FROM devices WHERE device_id=? AND token_hash=?',(x.device_id,hash_token(t))).fetchone()
    if not d: c.close(); raise HTTPException(401,'Invalid device token')
    c.execute('UPDATE devices SET version=?,last_seen=?,online=1 WHERE device_id=?',(x.version,now(),x.device_id)); c.commit(); c.close(); return {'ok':True,'server_time':now()}

@app.get('/api/devices')
def devices(_:dict=Depends(auth)):
    c=db(); rows=[dict(r) for r in c.execute('SELECT id,device_id,name,platform,version,last_seen,online,created_at FROM devices ORDER BY id DESC')]; c.close(); return rows

@app.delete('/api/devices/{device_id}')
def delete_device(device_id:str, _:dict=Depends(auth)):
    c=db(); c.execute('DELETE FROM devices WHERE device_id=?',(device_id,)); c.commit(); c.close(); return {'ok':True}

@app.websocket('/ws/dashboard')
async def ws(ws:WebSocket):
    await ws.accept(); clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: clients.discard(ws)
