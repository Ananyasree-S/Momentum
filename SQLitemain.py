import sqlite3
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI()
DB = "habits.db"


def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS habits (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                emoji      TEXT DEFAULT '⭐',
                color      TEXT DEFAULT '#7c3aed',
                frequency  INTEGER DEFAULT 7,
                created_at TEXT NOT NULL DEFAULT (date('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS completions (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                date     TEXT NOT NULL,
                UNIQUE(habit_id, date),
                FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
            )
        """)
        try:
            conn.execute("ALTER TABLE habits ADD COLUMN frequency INTEGER DEFAULT 7")
        except Exception:
            pass
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if 'period' in cols:
            conn.execute("DROP TABLE tasks")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                due_date   TEXT DEFAULT '',
                status     TEXT DEFAULT 'todo',
                note       TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


init_db()


class HabitIn(BaseModel):
    name: str
    emoji: str = "⭐"
    color: str = "#7c3aed"
    frequency: int = 7


class TaskIn(BaseModel):
    title: str
    due_date: str = ''
    status: str = 'todo'
    note: str = ''


@app.get("/api/habits")
def list_habits():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM habits ORDER BY created_at ASC, id ASC"
        ).fetchall()]


@app.post("/api/habits", status_code=201)
def create_habit(h: HabitIn):
    if not h.name.strip():
        raise HTTPException(400, "name cannot be empty")
    freq = max(1, min(7, h.frequency))
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO habits (name, emoji, color, frequency) VALUES (?,?,?,?)",
            (h.name.strip(), h.emoji, h.color, freq),
        )
        return {"id": cur.lastrowid, **h.model_dump()}


@app.put("/api/habits/{habit_id}")
def update_habit(habit_id: int, h: HabitIn):
    if not h.name.strip():
        raise HTTPException(400, "name cannot be empty")
    freq = max(1, min(7, h.frequency))
    with get_conn() as conn:
        res = conn.execute(
            "UPDATE habits SET name=?, emoji=?, color=?, frequency=? WHERE id=?",
            (h.name.strip(), h.emoji, h.color, freq, habit_id),
        )
        if res.rowcount == 0:
            raise HTTPException(404, "Not found")
    return {"id": habit_id, **h.model_dump()}


@app.delete("/api/habits/{habit_id}")
def delete_habit(habit_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM habits WHERE id=?", (habit_id,))
    return {"ok": True}


@app.post("/api/completions/toggle")
def toggle_completion(habit_id: int, date_str: str):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM completions WHERE habit_id=? AND date=?",
            (habit_id, date_str),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM completions WHERE habit_id=? AND date=?",
                (habit_id, date_str),
            )
            return {"done": False}
        else:
            conn.execute(
                "INSERT INTO completions (habit_id, date) VALUES (?,?)",
                (habit_id, date_str),
            )
            return {"done": True}


@app.get("/api/completions")
def get_completions(start_date: Optional[str] = None, end_date: Optional[str] = None):
    with get_conn() as conn:
        query = "SELECT * FROM completions WHERE 1=1"
        params = []
        if start_date:
            query += " AND date>=?"; params.append(start_date)
        if end_date:
            query += " AND date<=?"; params.append(end_date)
        return [dict(r) for r in conn.execute(query, params).fetchall()]


@app.get("/api/tasks")
def list_tasks():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY "
            "CASE WHEN status='done' THEN 2 ELSE 0 END, "
            "CASE WHEN due_date='' THEN 1 ELSE 0 END, "
            "due_date ASC, created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/tasks", status_code=201)
def create_task(t: TaskIn):
    if not t.title.strip():
        raise HTTPException(400, "title cannot be empty")
    status = t.status if t.status in ('todo', 'in_progress', 'done') else 'todo'
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, due_date, status, note) VALUES (?,?,?,?)",
            (t.title.strip(), t.due_date, status, t.note),
        )
        return {"id": cur.lastrowid, "title": t.title.strip(), "due_date": t.due_date, "status": status, "note": t.note}


@app.put("/api/tasks/{task_id}")
def update_task(task_id: int, t: TaskIn):
    if not t.title.strip():
        raise HTTPException(400, "title cannot be empty")
    status = t.status if t.status in ('todo', 'in_progress', 'done') else 'todo'
    with get_conn() as conn:
        res = conn.execute(
            "UPDATE tasks SET title=?, due_date=?, status=?, note=? WHERE id=?",
            (t.title.strip(), t.due_date, status, t.note, task_id),
        )
        if res.rowcount == 0:
            raise HTTPException(404, "Not found")
    return {"id": task_id, **t.model_dump()}


@app.post("/api/tasks/{task_id}/toggle")
def toggle_task(task_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        new_status = 'todo' if row["status"] == 'done' else 'done'
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (new_status, task_id))
        return {"id": task_id, "status": new_status}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return {"ok": True}


@app.get("/api/stats")
def get_stats():
    today = date.today()
    today_str = str(today)
    start_365 = str(today - timedelta(days=364))
    week_start = today - timedelta(days=today.weekday())

    with get_conn() as conn:
        habits = [dict(r) for r in conn.execute("SELECT * FROM habits").fetchall()]
        completions = [dict(r) for r in conn.execute(
            "SELECT * FROM completions WHERE date>=? AND date<=?",
            (start_365, today_str),
        ).fetchall()]

    heatmap = {}
    for c in completions:
        heatmap[c["date"]] = heatmap.get(c["date"], 0) + 1

    habit_stats = []
    for h in habits:
        habit_dates = sorted({c["date"] for c in completions if c["habit_id"] == h["id"]})
        habit_set   = set(habit_dates)
        freq        = h.get("frequency") or 7

        week_done = sum(1 for ds in habit_set if str(week_start) <= ds <= today_str)

        if freq == 7:
            streak = 0
            d = today
            while str(d) in habit_set:
                streak += 1
                d -= timedelta(days=1)
            best = cur_run = 0
            for i, ds in enumerate(habit_dates):
                if i == 0:
                    cur_run = 1
                else:
                    prev = date.fromisoformat(habit_dates[i - 1])
                    curr = date.fromisoformat(ds)
                    cur_run = cur_run + 1 if (curr - prev).days == 1 else 1
                best = max(best, cur_run)
        else:
            def week_count(ws):
                we = ws + timedelta(days=6)
                return sum(1 for ds in habit_set if str(ws) <= ds <= str(we))

            streak = 0
            check = week_start
            while True:
                if week_count(check) >= freq:
                    streak += 1
                    check -= timedelta(days=7)
                else:
                    break

            best = cur_run = 0
            w = week_start - timedelta(weeks=51)
            while w <= week_start:
                if week_count(w) >= freq:
                    cur_run += 1
                    best = max(best, cur_run)
                else:
                    cur_run = 0
                w += timedelta(days=7)

        habit_stats.append({
            **h,
            "streak":      streak,
            "best_streak": best,
            "total":       len(habit_set),
            "done_today":  today_str in habit_set,
            "week_done":   week_done,
        })

    today_done            = sum(1 for h in habit_stats if h["done_today"])
    total_completions     = sum(h["total"] for h in habit_stats)
    best_streak_overall   = max((h["best_streak"] for h in habit_stats), default=0)

    return {
        "habits":            habit_stats,
        "heatmap":           heatmap,
        "total_habits":      len(habits),
        "today_done":        today_done,
        "total_completions": total_completions,
        "best_streak":       best_streak_overall,
    }


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    with open("templates/index.html", encoding="utf-8") as f:
        return f.read()
