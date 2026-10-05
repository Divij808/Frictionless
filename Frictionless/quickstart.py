import subprocess
import sys
import sqlite3
from pathlib import Path


def update_pip():
    print("Updating pip to the latest version...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip"]
        )
        print("pip updated successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Warning: Could not update pip automatically: {e}")


def install_requirements():
    print("Checking dependencies...")

    req_file = Path("requirements.txt")
    default_requirements = [
        "Flask",
        "requests",
        "werkzeug",
        "pypdf",
        "youtube-transcript-api",
        "google-auth",
        "google-auth-oauthlib",
        "google-api-python-client",
    ]

    if req_file.exists():
        print("Found requirements.txt. Installing dependencies...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
            )
            print("Dependencies installed successfully from requirements.txt.")
            return
        except subprocess.CalledProcessError as e:
            print(f"Error installing from requirements.txt: {e}")
            print("Falling back to default package installation...")

    print("Installing essential packages...")

    for package in default_requirements:
        try:
            print(f"Installing {package}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            print(f"Could not install {package}: {e}")


def column_exists(conn, table, column):
    query = f"PRAGMA table_info({table})"
    return any(row[1] == column for row in conn.execute(query))


def add_column_if_missing(conn, table, column, definition):
    if not column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    print("Initializing the database (epoch.db)...")

    conn = sqlite3.connect("epoch.db")
    conn.row_factory = sqlite3.Row

    schema_statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            coins INTEGER NOT NULL DEFAULT 0,
            height_cm REAL,
            weight_kg REAL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            schedule_after TEXT,
            priority TEXT,
            locked INTEGER DEFAULT 0,
            deadline TEXT,
            is_habit INTEGER DEFAULT 0,
            day TEXT,
            task_date TEXT,
            google_event_id TEXT,
            completed INTEGER DEFAULT 0,
            missed_deadline INTEGER DEFAULT 0,
            tab_id INTEGER DEFAULT 0,
            is_reward INTEGER DEFAULT 0,
            reward_id INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_tabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            icon TEXT DEFAULT '📄'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS habit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            habit_name TEXT NOT NULL,
            log_date TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(username, habit_name, log_date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            icon TEXT DEFAULT '🔥',
            frequency TEXT DEFAULT 'Daily',
            time TEXT DEFAULT '08:00',
            duration INTEGER DEFAULT 30,
            active INTEGER DEFAULT 1
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL,
            unit TEXT,
            log_date TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            entry_note TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            name TEXT NOT NULL,
            description TEXT,
            cost INTEGER NOT NULL,
            duration INTEGER DEFAULT 0,
            reward_type TEXT DEFAULT 'task',
            icon TEXT DEFAULT '🎁'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            purchased_at TEXT NOT NULL,
            task_id INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS quick_link_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            icon TEXT DEFAULT '📁'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS quick_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            folder_id INTEGER,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            icon TEXT DEFAULT '🔗'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            paid REAL DEFAULT 0,
            due_date TEXT,
            note TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'Aim',
            parent_id INTEGER,
            progress INTEGER DEFAULT 0,
            due_date TEXT,
            notes TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS page_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            tab_id INTEGER NOT NULL,
            block_order INTEGER NOT NULL,
            block_type TEXT NOT NULL,
            content TEXT DEFAULT '',
            checked INTEGER DEFAULT 0,
            metadata TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS study_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '📚'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS notebooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            group_id INTEGER,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS notebook_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            notebook_id INTEGER NOT NULL,
            source_id TEXT,
            title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            url TEXT DEFAULT '',
            chunks INTEGER DEFAULT 0,
            characters INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS study_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            notebook_id INTEGER NOT NULL,
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            interval_days INTEGER NOT NULL DEFAULT 0,
            ease REAL NOT NULL DEFAULT 2.5,
            due_at TEXT NOT NULL,
            reps INTEGER NOT NULL DEFAULT 0,
            exam_deadline TEXT,
            review_task_id INTEGER,
            calendar_enabled INTEGER NOT NULL DEFAULT 0
        )
        """,
    ]

    for statement in schema_statements:
        conn.execute(statement)

    user_migrations = [
        ("coins", "INTEGER NOT NULL DEFAULT 0"),
        ("height_cm", "REAL"),
        ("weight_kg", "REAL"),
    ]

    for column, definition in user_migrations:
        add_column_if_missing(conn, "users", column, definition)

    task_migrations = [
        ("task_date", "TEXT"),
        ("is_reward", "INTEGER DEFAULT 0"),
        ("reward_id", "INTEGER"),
    ]

    for column, definition in task_migrations:
        add_column_if_missing(conn, "tasks", column, definition)

    health_migrations = [
        ("category", "TEXT DEFAULT 'general'"),
        ("entry_note", "TEXT DEFAULT ''"),
    ]

    for column, definition in health_migrations:
        add_column_if_missing(conn, "health_metrics", column, definition)

    add_column_if_missing(conn, "study_cards", "exam_deadline", "TEXT")
    add_column_if_missing(conn, "study_cards", "review_task_id", "INTEGER")
    add_column_if_missing(
        conn,
        "study_cards",
        "calendar_enabled",
        "INTEGER NOT NULL DEFAULT 0",
    )

    legacy = conn.execute(
        "SELECT id FROM user_tabs WHERE name = 'General Schedule'"
    ).fetchall()

    for row in legacy:
        conn.execute(
            "UPDATE tasks SET tab_id = 0 WHERE tab_id = ?",
            (row["id"],),
        )
        conn.execute(
            "DELETE FROM page_blocks WHERE tab_id = ?",
            (row["id"],),
        )
        conn.execute(
            "DELETE FROM user_tabs WHERE id = ?",
            (row["id"],),
        )

    defaults = [
        (
            None,
            "Cartoon Avatar",
            "Unlock a fun cartoon avatar.",
            50,
            0,
            "cosmetic",
            "🎨",
        ),
        (
            None,
            "1 Hour TV Time",
            "One hour of TV time, automatically scheduled after 19:00.",
            30,
            60,
            "task",
            "📺",
        ),
        (
            None,
            "30 Minutes Gaming",
            "30 minutes of gaming time.",
            20,
            30,
            "task",
            "🎮",
        ),
        (
            None,
            "Favourite Snack",
            "A small lifestyle reward.",
            25,
            0,
            "lifestyle",
            "🍿",
        ),
    ]

    for item in defaults:
        exists = conn.execute(
            "SELECT id FROM shop_items WHERE username IS NULL AND name = ?",
            (item[1],),
        ).fetchone()

        if not exists:
            conn.execute(
                """
                INSERT INTO shop_items(
                    username,
                    name,
                    description,
                    cost,
                    duration,
                    reward_type,
                    icon
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                item,
            )

    conn.commit()
    conn.close()

    print("Database initialized successfully.")


def run_setup():
    print("=== Running Epoch Quickstart Setup ===")
    update_pip()
    install_requirements()
    init_db()
    print("Setup complete!")


if __name__ == "__main__":
    run_setup()
