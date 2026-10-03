import os
import sqlite3
import datetime as dt
from functools import wraps
from urllib.parse import urlparse
from pathlib import Path
import json
import uuid

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from document.reader import extract_text
from document.chunker import chunk_text
from ai.generator import LocalAI
from rag.retriever import Retriever

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    Request = Credentials = InstalledAppFlow = build = None

app = Flask(__name__)
app.secret_key = os.environ.get("FRICTIONLESS_SECRET_KEY", "super_secret_frictionless_key")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WORK_START = 8 * 60
WORK_END = 20 * 60
REWARD_START = 19 * 60
COINS_PER_COMPLETION = 10


def db():
    conn = sqlite3.connect("frictionles.db")
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def add_column_if_missing(conn, table, column, definition):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            coins INTEGER NOT NULL DEFAULT 0,
            height_cm REAL,
            weight_kg REAL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL, name TEXT NOT NULL,
            start_time TEXT, end_time TEXT, schedule_after TEXT,
            priority TEXT, locked INTEGER DEFAULT 0, deadline TEXT,
            is_habit INTEGER DEFAULT 0, day TEXT, task_date TEXT,
            google_event_id TEXT, completed INTEGER DEFAULT 0,
            missed_deadline INTEGER DEFAULT 0, tab_id INTEGER DEFAULT 0,
            is_reward INTEGER DEFAULT 0, reward_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS user_tabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            name TEXT NOT NULL, icon TEXT DEFAULT '📄'
        );
        CREATE TABLE IF NOT EXISTS habit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            habit_name TEXT NOT NULL, log_date TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(username, habit_name, log_date)
        );
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            name TEXT NOT NULL, icon TEXT DEFAULT '🔥', frequency TEXT DEFAULT 'Daily',
            time TEXT DEFAULT '08:00', duration INTEGER DEFAULT 30, active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            metric_name TEXT NOT NULL, metric_value REAL, unit TEXT,
            log_date TEXT NOT NULL, category TEXT DEFAULT 'general',
            entry_note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT,
            name TEXT NOT NULL, description TEXT, cost INTEGER NOT NULL,
            duration INTEGER DEFAULT 0, reward_type TEXT DEFAULT 'task', icon TEXT DEFAULT '🎁'
        );
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            item_id INTEGER NOT NULL, purchased_at TEXT NOT NULL, task_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS quick_link_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            name TEXT NOT NULL, icon TEXT DEFAULT '📁'
        );
        CREATE TABLE IF NOT EXISTS quick_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            folder_id INTEGER, title TEXT NOT NULL, url TEXT NOT NULL, icon TEXT DEFAULT '🔗'
        );
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            name TEXT NOT NULL, amount REAL NOT NULL, paid REAL DEFAULT 0,
            due_date TEXT, note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            category TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'Aim',
            parent_id INTEGER, progress INTEGER DEFAULT 0, due_date TEXT, notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS page_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
            tab_id INTEGER NOT NULL, block_order INTEGER NOT NULL,
            block_type TEXT NOT NULL, content TEXT DEFAULT '', checked INTEGER DEFAULT 0,
            metadata TEXT DEFAULT ''
        );
    """)
    for col, definition in [("coins", "INTEGER NOT NULL DEFAULT 0"), ("height_cm", "REAL"), ("weight_kg", "REAL")]:
        add_column_if_missing(conn, "users", col, definition)
    for col, definition in [("task_date", "TEXT"), ("is_reward", "INTEGER DEFAULT 0"), ("reward_id", "INTEGER")]:
        add_column_if_missing(conn, "tasks", col, definition)
    for col, definition in [("category", "TEXT DEFAULT 'general'"), ("entry_note", "TEXT DEFAULT ''")]:
        add_column_if_missing(conn, "health_metrics", col, definition)

    # Remove the old General Schedule page completely.
    legacy = conn.execute("SELECT id FROM user_tabs WHERE name = 'General Schedule'").fetchall()
    for row in legacy:
        conn.execute("UPDATE tasks SET tab_id = 0 WHERE tab_id = ?", (row["id"],))
        conn.execute("DELETE FROM page_blocks WHERE tab_id = ?", (row["id"],))
        conn.execute("DELETE FROM user_tabs WHERE id = ?", (row["id"],))

    defaults = [
        (None, "Cartoon Avatar", "Unlock a fun cartoon avatar.", 50, 0, "cosmetic", "🎨"),
        (None, "1 Hour TV Time", "One hour of TV time, automatically scheduled after 19:00.", 30, 60, "task", "📺"),
        (None, "30 Minutes Gaming", "30 minutes of gaming time.", 20, 30, "task", "🎮"),
        (None, "Favourite Snack", "A small lifestyle reward.", 25, 0, "lifestyle", "🍿"),
    ]
    for item in defaults:
        exists = conn.execute("SELECT id FROM shop_items WHERE username IS NULL AND name = ?", (item[1],)).fetchone()
        if not exists:
            conn.execute("INSERT INTO shop_items(username,name,description,cost,duration,reward_type,icon) VALUES(?,?,?,?,?,?,?)", item)
    conn.commit()
    conn.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def get_calendar_service():
    if not (build and os.path.exists("credentials.json")):
        return None
    try:
        creds = None
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
            with open("token.json", "w") as token:
                token.write(creds.to_json())
        return build("calendar", "v3", credentials=creds)
    except Exception as exc:
        print(f"Calendar authentication error: {exc}")
        return None


def time_to_minutes(value):
    if not value:
        return 0
    try:
        h, m = map(int, str(value)[:5].split(":"))
        return h * 60 + m
    except (ValueError, TypeError):
        return 0


def minutes_to_time(minutes):
    minutes = max(0, minutes)
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def parse_date(value, fallback=None):
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        return fallback or dt.date.today()


def parse_deadline(value, target_date):
    if not value:
        return dt.datetime.combine(target_date + dt.timedelta(days=3), dt.time(23, 59))
    try:
        return dt.datetime.fromisoformat(value).replace(second=0, microsecond=0)
    except ValueError:
        try:
            return dt.datetime.combine(target_date, dt.time.fromisoformat(value[:5]))
        except ValueError:
            return dt.datetime.combine(target_date + dt.timedelta(days=3), dt.time(23, 59))


def detect_habit(name):
    keywords = ["workout", "gym", "meditate", "read", "water", "run", "stretch", "yoga", "exercise"]
    return any(k in name.lower() for k in keywords)


def task_duration(row):
    start = time_to_minutes(row["start_time"])
    end = time_to_minutes(row["end_time"])
    if end <= start:
        end += 1440
    return max(1, end - start)


def calendar_events_between(service, start_date, end_date):
    if not service:
        return []
    try:
        result = service.events().list(
            calendarId="primary",
            timeMin=dt.datetime.combine(start_date, dt.time.min).isoformat() + "Z",
            timeMax=dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min).isoformat() + "Z",
            singleEvents=True, orderBy="startTime"
        ).execute()
    except Exception as exc:
        print(f"Calendar fetch error: {exc}")
        return []
    events = []
    for ev in result.get("items", []):
        si, ei = ev.get("start", {}), ev.get("end", {})
        s, e = si.get("dateTime", si.get("date")), ei.get("dateTime", ei.get("date"))
        if not s:
            continue
        try:
            if "T" not in s:
                d = dt.date.fromisoformat(s)
                events.append({"id": ev.get("id"), "date": d, "start": 0, "end": 1440, "all_day": True, "summary": ev.get("summary", "Untitled")})
            else:
                sd = dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
                ed = dt.datetime.fromisoformat(e.replace("Z", "+00:00")).replace(tzinfo=None)
                end = 1440 if ed.date() > sd.date() else ed.hour * 60 + ed.minute
                events.append({"id": ev.get("id"), "date": sd.date(), "start": sd.hour * 60 + sd.minute, "end": end, "all_day": False, "summary": ev.get("summary", "Untitled")})
        except Exception:
            continue
    return events


def overlapping(a, b):
    return a[0] < b[1] and a[1] > b[0]


def get_task_intervals(conn, username, date_value, exclude_id=None):
    rows = conn.execute("SELECT * FROM tasks WHERE username=? AND task_date=? AND completed=0", (username, date_value.isoformat())).fetchall()
    result = []
    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        start = time_to_minutes(row["start_time"])
        result.append((start, min(1440, start + task_duration(row)), row))
    return result


def find_free_slot(conn, username, target_date, duration, earliest, deadline_dt, events, exclude_task_id=None, search_end=WORK_END):
    deadline_limit = search_end if deadline_dt.date() > target_date else min(search_end, deadline_dt.hour * 60 + deadline_dt.minute)
    if deadline_limit <= earliest or earliest + duration > deadline_limit:
        return None
    blocked = [(s, e) for s, e, _ in get_task_intervals(conn, username, target_date, exclude_task_id)]
    for ev in events:
        if ev["date"] == target_date:
            blocked.append((ev["start"], ev["end"]))
    blocked.sort()
    cursor = max(WORK_START, earliest)
    for start, end in blocked:
        if end <= cursor:
            continue
        if cursor + duration <= start:
            return cursor
        if cursor < end:
            cursor = end
        if cursor + duration > deadline_limit:
            return None
    return cursor if cursor + duration <= deadline_limit else None


def create_calendar_event(service, name, date_value, start, duration, priority, deadline):
    if not service:
        return None
    try:
        sdt = dt.datetime.combine(date_value, dt.time(start // 60, start % 60))
        edt = sdt + dt.timedelta(minutes=duration)
        event = service.events().insert(calendarId="primary", body={
            "summary": f"⚡ [{minutes_to_time(start)}] {name}",
            "description": f"Priority: {priority} | Duration: {duration}m | Deadline: {deadline}",
            "start": {"dateTime": sdt.isoformat()}, "end": {"dateTime": edt.isoformat()}
        }).execute()
        return event.get("id")
    except Exception as exc:
        print(f"Calendar sync error: {exc}")
        return None


def update_google_event(service, task, date_value, start, duration):
    if not service or not task["google_event_id"]:
        return
    try:
        sdt = dt.datetime.combine(date_value, dt.time(start // 60, start % 60))
        edt = sdt + dt.timedelta(minutes=duration)
        service.events().patch(calendarId="primary", eventId=task["google_event_id"], body={
            "summary": f"⚡ [{minutes_to_time(start)}] {task['name']}",
            "description": f"Priority: {task['priority']} | Duration: {duration}m | Deadline: {task['deadline']}",
            "start": {"dateTime": sdt.isoformat()}, "end": {"dateTime": edt.isoformat()}
        }).execute()
    except Exception as exc:
        print(f"Calendar update error: {exc}")


def reconcile_calendar_conflicts(conn, username, events, service):
    tasks = conn.execute("SELECT * FROM tasks WHERE username=? AND completed=0 ORDER BY task_date,start_time", (username,)).fetchall()
    for task in tasks:
        if task["locked"] or not task["task_date"] or not task["start_time"]:
            continue
        target = parse_date(task["task_date"])
        start = time_to_minutes(task["start_time"])
        duration = task_duration(task)
        conflicts = [e for e in events if e["date"] == target and e.get("id") != task["google_event_id"] and overlapping((start, start + duration), (e["start"], e["end"]))]
        if not conflicts:
            continue
        deadline = parse_deadline(task["deadline"], target)
        earliest = max(WORK_START, time_to_minutes(task["schedule_after"]))
        new_start = find_free_slot(conn, username, target, duration, earliest, deadline, events, task["id"])
        new_date = target
        if new_start is None:
            d = target + dt.timedelta(days=1)
            while d <= deadline.date() and new_start is None:
                new_start = find_free_slot(conn, username, d, duration, WORK_START, deadline, events, task["id"])
                if new_start is not None:
                    new_date = d
                d += dt.timedelta(days=1)
        if new_start is None:
            conn.execute("UPDATE tasks SET missed_deadline=1 WHERE id=?", (task["id"],))
            continue
        conn.execute("UPDATE tasks SET task_date=?,day=?,start_time=?,end_time=?,missed_deadline=0 WHERE id=?", (
            new_date.isoformat(), new_date.strftime("%A"), minutes_to_time(new_start), minutes_to_time(new_start + duration), task["id"]))
        update_google_event(service, task, new_date, new_start, duration)
    conn.commit()


def schedule_task(conn, username, name, duration, priority, locked, target_date, schedule_after, deadline,
                   tab_id=0, service=None, is_reward=False, reward_id=None, preferred_start=None, is_habit=False):
    events = calendar_events_between(service, target_date, deadline.date())
    reconcile_calendar_conflicts(conn, username, events, service)
    earliest = preferred_start if preferred_start is not None else max(WORK_START, time_to_minutes(schedule_after))
    if is_reward:
        earliest = max(REWARD_START, earliest)
    candidate_date = target_date
    while candidate_date <= deadline.date():
        start = find_free_slot(conn, username, candidate_date, duration, earliest, deadline, events)
        if start is not None:
            end = start + duration
            event_id = create_calendar_event(service, name, candidate_date, start, duration, priority, deadline.isoformat(timespec="minutes"))
            cur = conn.execute("""INSERT INTO tasks
                (username,name,start_time,end_time,schedule_after,priority,locked,deadline,is_habit,day,task_date,google_event_id,completed,missed_deadline,tab_id,is_reward,reward_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?)""", (
                username, name, minutes_to_time(start), minutes_to_time(end), minutes_to_time(earliest), priority, locked,
                deadline.isoformat(timespec="minutes"), int(is_habit or detect_habit(name)), candidate_date.strftime("%A"),
                candidate_date.isoformat(), event_id, tab_id, int(is_reward), reward_id))
            conn.commit()
            return cur.lastrowid
        candidate_date += dt.timedelta(days=1)
        earliest = REWARD_START if is_reward else WORK_START
    cur = conn.execute("""INSERT INTO tasks
        (username,name,start_time,end_time,schedule_after,priority,locked,deadline,is_habit,day,task_date,completed,missed_deadline,tab_id,is_reward,reward_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,0,1,?,?,?)""", (
        username, name, minutes_to_time(earliest), minutes_to_time(earliest + duration), minutes_to_time(earliest), priority,
        locked, deadline.isoformat(timespec="minutes"), int(is_habit or detect_habit(name)), target_date.strftime("%A"), target_date.isoformat(),
        tab_id, int(is_reward), reward_id))
    conn.commit()
    return cur.lastrowid


def user_coins(conn, username):
    row = conn.execute("SELECT coins FROM users WHERE username=?", (username,)).fetchone()
    return row["coins"] if row else 0


def dashboard_context(conn=None):
    own = conn is None
    conn = conn or db()
    username = session["user"]
    tabs = conn.execute("SELECT id,name,icon FROM user_tabs WHERE username=? ORDER BY id", (username,)).fetchall()
    tasks = conn.execute("SELECT * FROM tasks WHERE username=? ORDER BY task_date,start_time,id", (username,)).fetchall()
    coins = user_coins(conn, username)
    if own:
        conn.close()
    return {"tabs": tabs, "tasks": tasks, "coins": coins}


def build_schedule(conn, username, week_offset=0):
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday()) + dt.timedelta(weeks=week_offset)
    service = get_calendar_service()
    events = calendar_events_between(service, monday, monday + dt.timedelta(days=6))
    reconcile_calendar_conflicts(conn, username, events, service)
    events = calendar_events_between(service, monday, monday + dt.timedelta(days=6))
    schedules = {}
    for i, day in enumerate(DAY_NAMES):
        date_value = monday + dt.timedelta(days=i)
        items = []
        rows = conn.execute("SELECT * FROM tasks WHERE username=? AND task_date=? AND completed=0", (username, date_value.isoformat())).fetchall()
        for row in rows:
            s = time_to_minutes(row["start_time"]); e = s + task_duration(row)
            items.append({"kind": "task", "name": row["name"], "start": minutes_to_time(s), "end": minutes_to_time(e), "start_minutes": s, "end_minutes": e, "missed": bool(row["missed_deadline"]), "locked": bool(row["locked"]), "id": row["id"]})
        for ev in events:
            if ev["date"] == date_value and not ev["summary"].startswith("⚡"):
                items.append({"kind": "event", "name": ev["summary"], "start": minutes_to_time(ev["start"]), "end": minutes_to_time(ev["end"]), "start_minutes": ev["start"], "end_minutes": ev["end"], "missed": False, "locked": False, "id": 0})
        items.sort(key=lambda x: (x["start_minutes"], x["end_minutes"]))
        timeline=[]; cursor=WORK_START
        for item in items:
            if item["start_minutes"] > cursor:
                timeline.append({"kind":"gap","text":f"Free Gap: {minutes_to_time(cursor)} - {minutes_to_time(item['start_minutes'])}","id":0})
            timeline.append(item)
            cursor=max(cursor,item["end_minutes"])
        if cursor < WORK_END:
            timeline.append({"kind":"gap","text":f"Free Gap: {minutes_to_time(cursor)} - {minutes_to_time(WORK_END)}","id":0})
        if not items:
            timeline=[{"kind":"gap","text":"Entire Day Free (08:00 - 20:00)","id":0}]
        schedules[day]={"date":date_value.isoformat(),"timeline":timeline}
    return schedules, monday


@app.route("/")
def home():
    return redirect(url_for("main_menu" if "user" in session else "login"))


@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        username=request.form.get("username","").strip(); password=request.form.get("password","")
        if not username or not password: return "Username and password are required."
        conn=db()
        if conn.execute("SELECT 1 FROM users WHERE username=?",(username,)).fetchone():
            conn.close(); return "User already exists! <a href='/signup'>Try again</a>"
        conn.execute("INSERT INTO users(username,password) VALUES(?,?)",(username,generate_password_hash(password)))
        conn.commit(); conn.close(); return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username=request.form.get("username",""); password=request.form.get("password","")
        conn=db(); row=conn.execute("SELECT password FROM users WHERE username=?",(username,)).fetchone(); conn.close()
        if row and check_password_hash(row["password"],password):
            session["user"]=username; return redirect(url_for("main_menu"))
        return "Invalid credentials! <a href='/login'>Try again</a>"
    return render_template("login.html")


@app.route("/main-menu")
@login_required
def main_menu():
    conn=db(); context=dashboard_context(conn); context["task_count"]=conn.execute("SELECT COUNT(*) c FROM tasks WHERE username=? AND completed=0",(session["user"],)).fetchone()["c"]; context["goal_count"]=conn.execute("SELECT COUNT(*) c FROM goals WHERE username=?",(session["user"],)).fetchone()["c"]; conn.close()
    return render_template("main_menu.html",page="main",**context)


@app.route("/dashboard", methods=["GET","POST"])
@login_required
def dashboard():
    conn=db(); username=session["user"]
    if request.method=="POST":
        name=request.form.get("task_name","").strip()
        if name:
            try: duration=max(5,int(request.form.get("total_duration",60)))
            except ValueError: duration=60
            priority=request.form.get("priority","Medium")
            locked=1 if request.form.get("locked")=="on" else 0
            target=parse_date(request.form.get("task_date"),dt.date.today())
            deadline=parse_deadline(request.form.get("deadline",""),target)
            after=request.form.get("schedule_after","08:00") or "08:00"
            tab_id=int(request.form.get("tab_id",0) or 0)
            breakdown=request.form.get("break_down_enabled")=="on"
            try: session_duration=max(5,int(request.form.get("session_duration",30)))
            except ValueError: session_duration=30
            chunks=[]
            if breakdown and session_duration < duration:
                rem=duration; idx=1
                while rem>0:
                    c=min(rem,session_duration); chunks.append((f"{name} (Part {idx})",c)); rem-=c; idx+=1
            else: chunks=[(name,duration)]
            service=get_calendar_service()
            for chunk_name,chunk_duration in chunks:
                schedule_task(conn,username,chunk_name,chunk_duration,priority,locked,target,after,deadline,tab_id,service)
        conn.close(); return redirect(url_for("dashboard"))
    try: week_offset=int(request.args.get("week_offset",0))
    except ValueError: week_offset=0
    schedules,monday=build_schedule(conn,username,week_offset)
    context=dashboard_context(conn); context.update({"page":"dashboard","day_schedules":schedules,"week_offset":week_offset,"target_monday":monday.strftime("%B %d, %Y"),"today_date":dt.date.today().isoformat()})
    conn.close(); return render_template("tasks.html",**context)


@app.route("/page/<int:tab_id>")
@login_required
def blank_page(tab_id):
    conn=db(); tab=conn.execute("SELECT * FROM user_tabs WHERE id=? AND username=?",(tab_id,session["user"])).fetchone()
    if not tab: conn.close(); return redirect(url_for("main_menu"))
    blocks=conn.execute("SELECT * FROM page_blocks WHERE tab_id=? AND username=? ORDER BY block_order,id",(tab_id,session["user"])).fetchall()
    context=dashboard_context(conn); context.update({"page":"custom","page_tab":tab,"blocks":blocks}); conn.close()
    return render_template("custom_page.html",**context)


@app.route("/add-tab",methods=["POST"])
@login_required
def add_tab():
    name=request.form.get("tab_name","New Page").strip() or "New Page"; icon=request.form.get("tab_icon","📄")
    conn=db(); cur=conn.execute("INSERT INTO user_tabs(username,name,icon) VALUES(?,?,?)",(session["user"],name,icon)); conn.commit(); tid=cur.lastrowid; conn.close(); return redirect(url_for("blank_page",tab_id=tid))


@app.route("/rename-tab/<int:tab_id>",methods=["POST"])
@login_required
def rename_tab(tab_id):
    name=request.form.get("name","New Page").strip() or "New Page"; conn=db(); conn.execute("UPDATE user_tabs SET name=? WHERE id=? AND username=?",(name,tab_id,session["user"])); conn.commit(); conn.close(); return redirect(url_for("blank_page",tab_id=tab_id))


@app.route("/delete-tab/<int:tab_id>",methods=["POST"])
@login_required
def delete_tab(tab_id):
    conn=db(); conn.execute("DELETE FROM page_blocks WHERE tab_id=? AND username=?",(tab_id,session["user"])); conn.execute("DELETE FROM user_tabs WHERE id=? AND username=?",(tab_id,session["user"])); conn.execute("UPDATE tasks SET tab_id=0 WHERE tab_id=? AND username=?",(tab_id,session["user"])); conn.commit(); conn.close(); return redirect(url_for("main_menu"))


@app.route("/page/<int:tab_id>/save",methods=["POST"])
@login_required
def save_page(tab_id):
    data=request.get_json(silent=True) or {}; blocks=data.get("blocks",[])
    conn=db();
    if not conn.execute("SELECT 1 FROM user_tabs WHERE id=? AND username=?",(tab_id,session["user"])).fetchone(): conn.close(); return jsonify(success=False),404
    conn.execute("DELETE FROM page_blocks WHERE tab_id=? AND username=?",(tab_id,session["user"]))
    for i,b in enumerate(blocks):
        conn.execute("INSERT INTO page_blocks(username,tab_id,block_order,block_type,content,checked,metadata) VALUES(?,?,?,?,?,?,?)",(session["user"],tab_id,i,b.get("type","paragraph"),b.get("content",""),int(bool(b.get("checked",False))),b.get("metadata","")))
    conn.commit(); conn.close(); return jsonify(success=True)


@app.route("/edit-task/<int:task_id>",methods=["POST"])
@login_required
def edit_task(task_id):
    conn=db(); task=conn.execute("SELECT * FROM tasks WHERE id=? AND username=?",(task_id,session["user"])).fetchone()
    if not task: conn.close(); return redirect(url_for("dashboard"))
    name=request.form.get("name",task["name"]).strip() or task["name"]
    try: duration=max(5,int(request.form.get("duration",task_duration(task))))
    except ValueError: duration=task_duration(task)
    priority=request.form.get("priority",task["priority"]); target=parse_date(request.form.get("task_date"),parse_date(task["task_date"]))
    deadline=parse_deadline(request.form.get("deadline",""),target)
    start=time_to_minutes(task["start_time"]); end=start+duration
    conn.execute("UPDATE tasks SET name=?,priority=?,deadline=?,task_date=?,day=?,end_time=?,missed_deadline=0 WHERE id=? AND username=?",(name,priority,deadline.isoformat(timespec="minutes"),target.isoformat(),target.strftime("%A"),minutes_to_time(end),task_id,session["user"]))
    conn.commit(); updated=conn.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone(); update_google_event(get_calendar_service(),updated,target,start,duration); conn.close(); return redirect(url_for("dashboard"))


@app.route("/move-task",methods=["POST"])
@login_required
def move_task():
    data=request.get_json(silent=True) or {}; task_id=data.get("task_id"); new_day=data.get("new_day")
    if not task_id or new_day not in DAY_NAMES: return jsonify(success=False,error="Invalid task or day"),400
    conn=db(); task=conn.execute("SELECT * FROM tasks WHERE id=? AND username=?",(task_id,session["user"])).fetchone()
    if not task: conn.close(); return jsonify(success=False),404
    old=parse_date(task["task_date"]); new_date=old+dt.timedelta(days=DAY_NAMES.index(new_day)-old.weekday()); duration=task_duration(task); deadline=parse_deadline(task["deadline"],new_date); service=get_calendar_service(); events=calendar_events_between(service,new_date,deadline.date())
    start=find_free_slot(conn,session["user"],new_date,duration,max(WORK_START,time_to_minutes(task["schedule_after"])),deadline,events,task_id)
    if start is None: conn.close(); return jsonify(success=False,error="No free slot before deadline"),409
    conn.execute("UPDATE tasks SET task_date=?,day=?,start_time=?,end_time=? WHERE id=? AND username=?",(new_date.isoformat(),new_day,minutes_to_time(start),minutes_to_time(start+duration),task_id,session["user"])); conn.commit(); update_google_event(service,task,new_date,start,duration); conn.close(); return jsonify(success=True,start=minutes_to_time(start),end=minutes_to_time(start+duration))


@app.route("/toggle-complete/<int:task_id>",methods=["POST"])
@login_required
def toggle_complete(task_id):
    conn=db(); task=conn.execute("SELECT completed FROM tasks WHERE id=? AND username=?",(task_id,session["user"])).fetchone()
    if task:
        new=0 if task["completed"] else 1; conn.execute("UPDATE tasks SET completed=? WHERE id=?",(new,task_id)); delta=COINS_PER_COMPLETION if new else -COINS_PER_COMPLETION; conn.execute("UPDATE users SET coins=MAX(0,coins+?) WHERE username=?",(delta,session["user"])); conn.commit()
    conn.close(); return redirect(url_for("dashboard"))


@app.route("/delete-task/<int:task_id>",methods=["POST"])
@login_required
def delete_task(task_id):
    conn=db(); task=conn.execute("SELECT google_event_id FROM tasks WHERE id=? AND username=?",(task_id,session["user"])).fetchone()
    if task:
        conn.execute("DELETE FROM tasks WHERE id=? AND username=?",(task_id,session["user"])); conn.commit()
        if task["google_event_id"]:
            service=get_calendar_service()
            try:
                if service: service.events().delete(calendarId="primary",eventId=task["google_event_id"]).execute()
            except Exception as exc: print(f"Calendar delete error: {exc}")
    conn.close(); return redirect(url_for("dashboard"))


@app.route("/habits",methods=["GET","POST"])
@login_required
def habits():
    conn=db(); username=session["user"]
    if request.method=="POST":
        name=request.form.get("habit_name","").strip()
        if name:
            try: duration=max(5,int(request.form.get("duration",30)))
            except ValueError: duration=30
            time=request.form.get("habit_time","08:00"); frequency=request.form.get("frequency","Daily"); icon=request.form.get("icon","🔥")
            cur=conn.execute("INSERT INTO habits(username,name,icon,frequency,time,duration) VALUES(?,?,?,?,?,?)",(username,name,icon,frequency,time,duration)); habit_id=cur.lastrowid
            start_date=parse_date(request.form.get("start_date"),dt.date.today()); service=get_calendar_service()
            # Create the next 30 occurrences. Daily/Weekly are supported without hidden scheduling magic.
            for n in range(30):
                d=start_date+dt.timedelta(days=n)
                if frequency=="Weekly" and d.weekday()!=start_date.weekday(): continue
                deadline=dt.datetime.combine(d,dt.time(23,59))
                schedule_task(conn,username,name,duration,"Low",0,d,time,deadline,0,service,is_habit=True)
            conn.commit()
        conn.close(); return redirect(url_for("habits"))
    habits_rows=conn.execute("SELECT * FROM habits WHERE username=? AND active=1 ORDER BY id",(username,)).fetchall(); recent=conn.execute("SELECT name,task_date,completed FROM tasks WHERE username=? AND is_habit=1 ORDER BY task_date DESC LIMIT 100",(username,)).fetchall(); context=dashboard_context(conn); context.update({"page":"habits","habits":habits_rows,"recent_habits":recent,"today_date":dt.date.today().isoformat()}); conn.close(); return render_template("habits.html",**context)


@app.route("/shopping",methods=["GET","POST"])
@login_required
def shopping():
    conn=db(); username=session["user"]
    if request.method=="POST":
        name=request.form.get("name","").strip()
        try: cost=max(0,int(request.form.get("cost",0)))
        except ValueError: cost=0
        try: duration=max(0,int(request.form.get("duration",0)))
        except ValueError: duration=0
        if name and cost>=0: conn.execute("INSERT INTO shop_items(username,name,description,cost,duration,reward_type,icon) VALUES(?,?,?,?,?,?,?)",(username,name,request.form.get("description",""),cost,duration,request.form.get("reward_type","task"),request.form.get("icon","🎁"))); conn.commit()
        conn.close(); return redirect(url_for("shopping"))
    items=conn.execute("SELECT * FROM shop_items WHERE username IS NULL OR username=? ORDER BY cost,id",(username,)).fetchall(); context=dashboard_context(conn); context.update({"page":"shopping","shop_items":items}); conn.close(); return render_template("shopping.html",**context)


@app.route("/shop/redeem/<int:item_id>",methods=["POST"])
@login_required
def redeem_reward(item_id):
    conn=db(); item=conn.execute("SELECT * FROM shop_items WHERE id=? AND (username IS NULL OR username=?)",(item_id,session["user"])).fetchone(); balance=user_coins(conn,session["user"])
    if not item: conn.close(); return jsonify(success=False,error="Reward not found"),404
    if balance<item["cost"]: conn.close(); return jsonify(success=False,error="Not enough coins"),400
    conn.execute("UPDATE users SET coins=coins-? WHERE username=?",(item["cost"],session["user"]))
    purchase=conn.execute("INSERT INTO purchases(username,item_id,purchased_at) VALUES(?,?,?)",(session["user"],item_id,dt.datetime.now().isoformat(timespec="seconds")))
    task_id=None
    if item["duration"]>0:
        target=dt.date.today(); deadline=dt.datetime.combine(target+dt.timedelta(days=1),dt.time(23,59)); task_id=schedule_task(conn,session["user"],item["name"],item["duration"],"Low",0,target,"19:00",deadline,0,get_calendar_service(),True,item_id,REWARD_START)
    conn.execute("UPDATE purchases SET task_id=? WHERE id=?",(task_id,purchase.lastrowid)); conn.commit(); conn.close(); return jsonify(success=True,message=f"Redeemed {item['name']}!",task_id=task_id)


@app.route("/quick-links",methods=["GET","POST"])
@login_required
def quick_links():
    conn=db(); username=session["user"]
    if request.method=="POST":
        action=request.form.get("action")
        if action=="folder":
            name=request.form.get("folder_name","New Folder").strip() or "New Folder"; conn.execute("INSERT INTO quick_link_folders(username,name,icon) VALUES(?,?,?)",(username,name,request.form.get("folder_icon","📁")))
        elif action=="link":
            title=request.form.get("title","").strip(); raw=request.form.get("url","").strip(); folder=request.form.get("folder_id") or None
            if raw and not urlparse(raw).scheme: raw="https://"+raw
            if title and raw: conn.execute("INSERT INTO quick_links(username,folder_id,title,url,icon) VALUES(?,?,?,?,?)",(username,folder, title,raw,request.form.get("icon","🔗")))
        conn.commit(); conn.close(); return redirect(url_for("quick_links"))
    folders=conn.execute("SELECT * FROM quick_link_folders WHERE username=? ORDER BY name",(username,)).fetchall(); links=conn.execute("SELECT * FROM quick_links WHERE username=? ORDER BY title",(username,)).fetchall(); context=dashboard_context(conn); context.update({"page":"quick","link_folders":folders,"quick_links":links}); conn.close(); return render_template("quick_links.html",**context)


@app.route("/quick-links/delete/<int:link_id>",methods=["POST"])
@login_required
def delete_quick_link(link_id):
    conn=db(); conn.execute("DELETE FROM quick_links WHERE id=? AND username=?",(link_id,session["user"])); conn.commit(); conn.close(); return redirect(url_for("quick_links"))


@app.route("/quick-links/folder/delete/<int:folder_id>",methods=["POST"])
@login_required
def delete_quick_folder(folder_id):
    conn=db(); conn.execute("UPDATE quick_links SET folder_id=NULL WHERE folder_id=? AND username=?",(folder_id,session["user"])); conn.execute("DELETE FROM quick_link_folders WHERE id=? AND username=?",(folder_id,session["user"])); conn.commit(); conn.close(); return redirect(url_for("quick_links"))


@app.route("/loans",methods=["GET","POST"])
@login_required
def loans():
    conn=db(); username=session["user"]
    if request.method=="POST":
        try: amount=float(request.form.get("amount",0)); paid=float(request.form.get("paid",0))
        except ValueError: amount=paid=0
        name=request.form.get("name","").strip()
        if name and amount>=0: conn.execute("INSERT INTO loans(username,name,amount,paid,due_date,note) VALUES(?,?,?,?,?,?)",(username,name,amount,max(0,paid),request.form.get("due_date") or None,request.form.get("note",""))); conn.commit()
        conn.close(); return redirect(url_for("loans"))
    rows=conn.execute("SELECT * FROM loans WHERE username=? ORDER BY due_date IS NULL,due_date",(username,)).fetchall(); total=sum(r["amount"] for r in rows); paid=sum(r["paid"] for r in rows); remaining=max(0,total-paid); progress=round((paid/total)*100) if total else 0; context=dashboard_context(conn); context.update({"page":"loans","loans":rows,"loan_total":total,"loan_paid":paid,"loan_remaining":remaining,"loan_progress":progress}); conn.close(); return render_template("loans.html",**context)


@app.route("/loans/pay/<int:loan_id>",methods=["POST"])
@login_required
def pay_loan(loan_id):
    conn=db(); row=conn.execute("SELECT amount,paid FROM loans WHERE id=? AND username=?",(loan_id,session["user"])).fetchone()
    if row:
        try: payment=max(0,float(request.form.get("payment",0)))
        except ValueError: payment=0
        conn.execute("UPDATE loans SET paid=MIN(amount,paid+?) WHERE id=? AND username=?",(payment,loan_id,session["user"])); conn.commit()
    conn.close(); return redirect(url_for("loans"))


@app.route("/goals",methods=["GET","POST"])
@login_required
def goals():
    conn=db(); username=session["user"]
    if request.method=="POST":
        name=request.form.get("name","").strip(); category=request.form.get("category","Career"); kind=request.form.get("kind","Aim"); parent=request.form.get("parent_id") or None
        try: progress=min(100,max(0,int(request.form.get("progress",0))))
        except ValueError: progress=0
        if name: conn.execute("INSERT INTO goals(username,category,name,kind,parent_id,progress,due_date,notes) VALUES(?,?,?,?,?,?,?,?)",(username,category,name,kind,parent,progress,request.form.get("due_date") or None,request.form.get("notes",""))); conn.commit()
        conn.close(); return redirect(url_for("goals",tab=category.lower()))
    selected=request.args.get("tab","career").capitalize(); rows=conn.execute("SELECT * FROM goals WHERE username=? AND category=? ORDER BY id",(username,selected)).fetchall(); parents=[r for r in rows if r["kind"] in ("Aim","Objective")]; context=dashboard_context(conn); context.update({"page":"goals","goal_tab":selected,"goals":rows,"goal_parents":parents}); conn.close(); return render_template("goals.html",**context)


@app.route("/goals/update/<int:goal_id>",methods=["POST"])
@login_required
def update_goal(goal_id):
    try: progress=min(100,max(0,int(request.form.get("progress",0))))
    except ValueError: progress=0
    conn=db(); conn.execute("UPDATE goals SET progress=? WHERE id=? AND username=?",(progress,goal_id,session["user"])); conn.commit(); conn.close(); return redirect(url_for("goals",tab=request.form.get("tab","career")))


@app.route("/health/<section>", methods=["GET", "POST"])
@login_required
def health_section(section):
    """Health dashboard with body metrics, food history and exercise history."""
    if section not in {"body", "eating", "exercise"}:
        return redirect(url_for("health_section", section="body"))

    conn = db()
    username = session["user"]

    # The page can be opened for any day so users can inspect their history.
    selected_date = request.args.get("date", dt.date.today().isoformat())
    try:
        selected_date = dt.date.fromisoformat(selected_date).isoformat()
    except ValueError:
        selected_date = dt.date.today().isoformat()

    if request.method == "POST":
        if section == "body":
            try:
                h = float(request.form.get("height_cm", "0"))
                w = float(request.form.get("weight_kg", "0"))
                if h > 0 and w > 0:
                    conn.execute(
                        "UPDATE users SET height_cm=?, weight_kg=? WHERE username=?",
                        (h, w, username),
                    )
            except (ValueError, TypeError):
                pass

        elif section == "eating":
            food = request.form.get("food_name", "Food").strip() or "Food"
            try:
                calories = float(request.form.get("calories", "0"))
            except (ValueError, TypeError):
                calories = 0
            meal = request.form.get("meal", "Meal").strip() or "Meal"
            note = request.form.get("note", "").strip()
            if calories >= 0:
                conn.execute(
                    "INSERT INTO health_metrics(username,metric_name,metric_value,unit,log_date,category,entry_note) VALUES(?,?,?,?,?,?,?)",
                    (username, food, calories, "kcal", selected_date, "eating", f"{meal}: {note}".strip(": ")),
                )

        elif section == "exercise":
            name = request.form.get("exercise_name", "Exercise").strip() or "Exercise"
            try:
                minutes = float(request.form.get("minutes", "0"))
            except (ValueError, TypeError):
                minutes = 0
            try:
                calories = float(request.form.get("exercise_calories", request.form.get("running_calories", "0")))
            except (ValueError, TypeError):
                calories = 0
            note = request.form.get("note", "").strip()

            if minutes > 0:
                conn.execute(
                    "INSERT INTO health_metrics(username,metric_name,metric_value,unit,log_date,category,entry_note) VALUES(?,?,?,?,?,?,?)",
                    (username, name, minutes, "minutes", selected_date, "exercise", note),
                )
            if calories > 0:
                conn.execute(
                    "INSERT INTO health_metrics(username,metric_name,metric_value,unit,log_date,category,entry_note) VALUES(?,?,?,?,?,?,?)",
                    (username, f"{name} calories", calories, "kcal burned", selected_date, "exercise", note),
                )

        conn.commit()
        return redirect(url_for("health_section", section=section, date=selected_date))

    user = conn.execute(
        "SELECT height_cm, weight_kg FROM users WHERE username=?", (username,)
    ).fetchone()

    bmi = None
    bmi_category = "Not available"
    calorie_budget = None
    if user["height_cm"] and user["weight_kg"]:
        bmi = round(user["weight_kg"] / (user["height_cm"] / 100) ** 2, 1)
        if bmi < 18.5:
            bmi_category = "Below healthy BMI range"
            calorie_budget = 2400
        elif bmi < 25:
            bmi_category = "Healthy BMI range"
            calorie_budget = 2200
        elif bmi < 30:
            bmi_category = "Above healthy BMI range"
            calorie_budget = 2000
        else:
            bmi_category = "High BMI range"
            calorie_budget = 1800

    # Daily totals for the selected day.
    eating_total = conn.execute(
        "SELECT COALESCE(SUM(metric_value),0) FROM health_metrics WHERE username=? AND category='eating' AND log_date=?",
        (username, selected_date),
    ).fetchone()[0]
    exercise_calories = conn.execute(
        "SELECT COALESCE(SUM(metric_value),0) FROM health_metrics WHERE username=? AND category='exercise' AND unit='kcal burned' AND log_date=?",
        (username, selected_date),
    ).fetchone()[0]
    exercise_minutes = conn.execute(
        "SELECT COALESCE(SUM(metric_value),0) FROM health_metrics WHERE username=? AND category='exercise' AND unit='minutes' AND log_date=?",
        (username, selected_date),
    ).fetchone()[0]
    net_calories = eating_total - exercise_calories
    remaining = max(0, calorie_budget - eating_total) if calorie_budget is not None else None

    # Complete history, grouped in the template by date.
    history_rows = conn.execute(
        """SELECT log_date,
                  COALESCE(SUM(CASE WHEN category='eating' THEN metric_value ELSE 0 END),0) AS eaten,
                  COALESCE(SUM(CASE WHEN category='exercise' AND unit='kcal burned' THEN metric_value ELSE 0 END),0) AS burned,
                  COALESCE(SUM(CASE WHEN category='exercise' AND unit='minutes' THEN metric_value ELSE 0 END),0) AS minutes
           FROM health_metrics
           WHERE username=? AND category IN ('eating','exercise')
           GROUP BY log_date ORDER BY log_date DESC""",
        (username,),
    ).fetchall()

    selected_entries = conn.execute(
        "SELECT * FROM health_metrics WHERE username=? AND log_date=? AND category IN ('eating','exercise') ORDER BY id DESC",
        (username, selected_date),
    ).fetchall()

    context = dashboard_context(conn)
    context.update({
        "page": "health",
        "health_section": section,
        "health_user": user,
        "bmi": bmi,
        "bmi_category": bmi_category,
        "calorie_budget": calorie_budget,
        "selected_date": selected_date,
        "eating_total": round(eating_total),
        "exercise_calories": round(exercise_calories),
        "exercise_minutes": round(exercise_minutes),
        "net_calories": round(net_calories),
        "remaining_calories": round(remaining) if remaining is not None else None,
        "health_history": history_rows,
        "selected_entries": selected_entries,
    })
    conn.close()
    return render_template("health.html", **context)



# -------------------- Study Hub / Epoch --------------------

BASE_DIR = Path(__file__).resolve().parent
EPOCH_DATA_DIR = BASE_DIR / "epoch_data"
EPOCH_UPLOAD_DIR = BASE_DIR / "epoch_uploads"
EPOCH_DATA_DIR.mkdir(exist_ok=True)
EPOCH_UPLOAD_DIR.mkdir(exist_ok=True)
_epoch_ai = None


def get_epoch_ai():
    """Load the local transformer model only when the Study Hub is first used."""
    global _epoch_ai
    if _epoch_ai is None:
        _epoch_ai = LocalAI()
    return _epoch_ai


def epoch_user_paths():
    username = session["user"]
    safe_user = "".join(ch for ch in username if ch.isalnum() or ch in ("-", "_")) or "user"
    data_dir = EPOCH_DATA_DIR / safe_user
    upload_dir = EPOCH_UPLOAD_DIR / safe_user
    data_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, upload_dir


def load_epoch_sources():
    data_dir, _ = epoch_user_paths()
    source_file = data_dir / "sources.json"
    if not source_file.exists():
        return []
    try:
        return json.loads(source_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_epoch_sources(sources):
    data_dir, _ = epoch_user_paths()
    (data_dir / "sources.json").write_text(
        json.dumps(sources, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def epoch_retriever():
    data_dir, _ = epoch_user_paths()
    return Retriever(data_dir / "index.json")


@app.route("/study")
@login_required
def study_home():
    conn = db()
    context = dashboard_context(conn)
    conn.close()
    return render_template("study_home.html", page="study", **context)


@app.route("/study/sources")
@login_required
def study_sources():
    conn = db()
    context = dashboard_context(conn)
    conn.close()
    context.update({"page": "study", "sources": load_epoch_sources()})
    return render_template("study_sources.html", **context)


@app.route("/study/upload", methods=["POST"])
@login_required
def study_upload():
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        flash("Choose a PDF or TXT file first.", "error")
        return redirect(url_for("study_sources"))

    extension = Path(uploaded.filename).suffix.lower()
    if extension not in {".pdf", ".txt"}:
        flash("Only PDF and TXT files are supported.", "error")
        return redirect(url_for("study_sources"))

    source_id = uuid.uuid4().hex
    safe_name = Path(uploaded.filename).name
    stored_name = f"{source_id}{extension}"
    _, upload_dir = epoch_user_paths()
    path = upload_dir / stored_name
    uploaded.save(path)

    try:
        text = extract_text(path)
        if not text.strip():
            raise ValueError("No readable text was found in this file.")
        chunks = chunk_text(text)
        epoch_retriever().add_document(source_id, safe_name, chunks)

        sources = load_epoch_sources()
        sources.append({
            "id": source_id,
            "filename": safe_name,
            "stored_name": stored_name,
            "chunks": len(chunks),
            "characters": len(text),
        })
        save_epoch_sources(sources)
        flash(f"Added {safe_name} with {len(chunks)} searchable chunks.", "success")
    except Exception as exc:
        if path.exists():
            path.unlink()
        flash(f"Could not process the document: {exc}", "error")

    return redirect(url_for("study_sources"))


@app.route("/study/delete/<source_id>", methods=["POST"])
@login_required
def study_delete_source(source_id):
    sources = load_epoch_sources()
    source = next((s for s in sources if s["id"] == source_id), None)
    if source:
        _, upload_dir = epoch_user_paths()
        path = upload_dir / source["stored_name"]
        if path.exists():
            path.unlink()
        save_epoch_sources([s for s in sources if s["id"] != source_id])
        epoch_retriever().remove_document(source_id)
        flash("Source removed.", "success")
    return redirect(url_for("study_sources"))


@app.route("/study/workspace", methods=["GET", "POST"])
@login_required
def study_workspace():
    answer = None
    answer_sources = []
    result = None
    result_title = None
    selected_action = None

    if request.method == "POST":
        mode = request.form.get("mode", "")
        retriever = epoch_retriever()

        if mode == "ask":
            question = request.form.get("question", "").strip()
            if not question:
                flash("Enter a question.", "error")
            else:
                matches = retriever.search(question, top_k=5)
                if not matches:
                    answer = "Upload a source first so Epoch has material to search."
                else:
                    context_text = "\n\n".join(
                        f"[Source: {item['filename']}]\n{item['text']}" for item in matches
                    )
                    try:
                        answer = get_epoch_ai().answer_question(question, context_text)
                        answer_sources = [
                            {"filename": item["filename"], "score": round(item["score"], 3)}
                            for item in matches
                        ]
                    except Exception as exc:
                        flash(f"AI generation failed: {exc}", "error")

        elif mode == "generate":
            selected_action = request.form.get("action", "")
            source_ids = request.form.getlist("source_ids")
            if not source_ids:
                source_ids = [s["id"] for s in load_epoch_sources()]
            selected = retriever.get_chunks_for_sources(source_ids)
            if not selected:
                flash("Upload and select at least one source.", "error")
            else:
                context_text = "\n\n".join(
                    f"[{item['filename']}]\n{item['text']}" for item in selected
                )[:18000]
                try:
                    ai = get_epoch_ai()
                    if selected_action == "summary":
                        result, result_title = ai.summary(context_text), "Study Summary"
                    elif selected_action == "notes":
                        result, result_title = ai.study_notes(context_text), "Revision Notes"
                    elif selected_action == "flashcards":
                        result, result_title = ai.flashcards(context_text), "Generated Flashcards"
                    elif selected_action == "questions":
                        result, result_title = ai.practice_questions(context_text), "Practice Questions"
                    else:
                        flash("Choose a valid generation type.", "error")
                except Exception as exc:
                    flash(f"AI generation failed: {exc}", "error")

    conn = db()
    context = dashboard_context(conn)
    conn.close()
    context.update({
        "page": "study",
        "sources": load_epoch_sources(),
        "answer": answer,
        "answer_sources": answer_sources,
        "result": result,
        "result_title": result_title,
        "selected_action": selected_action,
        "model_name": os.getenv("EPOCH_MODEL", "google/flan-t5-base"),
    })
    return render_template("study_workspace.html", **context)


@app.route("/shop/redeem-form/<int:item_id>", methods=["POST"])
@login_required
def redeem_reward_form(item_id):
    conn = db()
    item = conn.execute(
        "SELECT * FROM shop_items WHERE id=? AND (username IS NULL OR username=?)",
        (item_id, session["user"]),
    ).fetchone()
    balance = user_coins(conn, session["user"])

    if not item:
        conn.close()
        flash("Reward not found.", "error")
        return redirect(url_for("shopping"))
    if balance < item["cost"]:
        conn.close()
        flash("Not enough coins.", "error")
        return redirect(url_for("shopping"))

    conn.execute("UPDATE users SET coins=coins-? WHERE username=?", (item["cost"], session["user"]))
    purchase = conn.execute(
        "INSERT INTO purchases(username,item_id,purchased_at) VALUES(?,?,?)",
        (session["user"], item_id, dt.datetime.now().isoformat(timespec="seconds")),
    )
    task_id = None
    if item["duration"] > 0:
        target = dt.date.today()
        deadline = dt.datetime.combine(target + dt.timedelta(days=1), dt.time(23, 59))
        task_id = schedule_task(
            conn, session["user"], item["name"], item["duration"], "Low", 0,
            target, "19:00", deadline, 0, get_calendar_service(), True, item_id, REWARD_START
        )
    conn.execute("UPDATE purchases SET task_id=? WHERE id=?", (task_id, purchase.lastrowid))
    conn.commit()
    conn.close()
    flash(f"Redeemed {item['name']}!", "success")
    return redirect(url_for("shopping"))


@app.route("/page/<int:tab_id>/block/add", methods=["POST"])
@login_required
def add_page_block(tab_id):
    block_type = request.form.get("block_type", "paragraph")
    content = request.form.get("content", "").strip()
    allowed = {"heading", "subheading", "paragraph", "bullet", "todo", "callout", "quote", "code", "divider", "link"}
    if block_type not in allowed:
        block_type = "paragraph"
    conn = db()
    if conn.execute("SELECT 1 FROM user_tabs WHERE id=? AND username=?", (tab_id, session["user"])).fetchone():
        order = conn.execute(
            "SELECT COALESCE(MAX(block_order), -1) + 1 FROM page_blocks WHERE tab_id=? AND username=?",
            (tab_id, session["user"]),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO page_blocks(username,tab_id,block_order,block_type,content,checked,metadata) VALUES(?,?,?,?,?,0,'')",
            (session["user"], tab_id, order, block_type, content),
        )
        conn.commit()
    conn.close()
    return redirect(url_for("blank_page", tab_id=tab_id))


@app.route("/page/<int:tab_id>/block/<int:block_id>/edit", methods=["POST"])
@login_required
def edit_page_block(tab_id, block_id):
    content = request.form.get("content", "")
    conn = db()
    conn.execute(
        "UPDATE page_blocks SET content=? WHERE id=? AND tab_id=? AND username=?",
        (content, block_id, tab_id, session["user"]),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("blank_page", tab_id=tab_id))


@app.route("/page/<int:tab_id>/block/<int:block_id>/delete", methods=["POST"])
@login_required
def delete_page_block(tab_id, block_id):
    conn = db()
    conn.execute(
        "DELETE FROM page_blocks WHERE id=? AND tab_id=? AND username=?",
        (block_id, tab_id, session["user"]),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("blank_page", tab_id=tab_id))


@app.route("/page/<int:tab_id>/block/<int:block_id>/move/<direction>", methods=["POST"])
@login_required
def move_page_block(tab_id, block_id, direction):
    conn = db()
    current = conn.execute(
        "SELECT id,block_order FROM page_blocks WHERE id=? AND tab_id=? AND username=?",
        (block_id, tab_id, session["user"]),
    ).fetchone()
    if current:
        op = "<" if direction == "up" else ">"
        sort = "DESC" if direction == "up" else "ASC"
        neighbour = conn.execute(
            f"SELECT id,block_order FROM page_blocks WHERE tab_id=? AND username=? AND block_order {op} ? ORDER BY block_order {sort} LIMIT 1",
            (tab_id, session["user"], current["block_order"]),
        ).fetchone()
        if neighbour:
            conn.execute("UPDATE page_blocks SET block_order=? WHERE id=?", (neighbour["block_order"], current["id"]))
            conn.execute("UPDATE page_blocks SET block_order=? WHERE id=?", (current["block_order"], neighbour["id"]))
            conn.commit()
    conn.close()
    return redirect(url_for("blank_page", tab_id=tab_id))


@app.route("/logout")
def logout():
    session.pop("user",None); return redirect(url_for("login"))


if __name__=="__main__":
    init_db(); app.run(debug=True)
