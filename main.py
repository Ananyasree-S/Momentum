import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Render provides postgres:// but psycopg2 needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS habits (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL,
                emoji      TEXT DEFAULT '⭐',
                color      TEXT DEFAULT '#7c3aed',
                frequency  INTEGER DEFAULT 7,
                created_at TEXT NOT NULL DEFAULT to_char(CURRENT_DATE, 'YYYY-MM-DD')
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS completions (
                id       SERIAL PRIMARY KEY,
                habit_id INTEGER NOT NULL,
                date     TEXT NOT NULL,
                UNIQUE(habit_id, date),
                FOREIGN KEY (habit_id) REFERENCES habits(id) ON DELETE CASCADE
            )
        """)
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name='habits' AND column_name='frequency'
                ) THEN
                    ALTER TABLE habits ADD COLUMN frequency INTEGER DEFAULT 7;
                END IF;
            END $$;
        """)
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='tasks' AND column_name='period'
        """)
        if cur.fetchone():
            cur.execute("DROP TABLE tasks")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id         SERIAL PRIMARY KEY,
                title      TEXT NOT NULL,
                due_date   TEXT DEFAULT '',
                status     TEXT DEFAULT 'todo',
                note       TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
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
    with get_db() as cur:
        cur.execute("SELECT * FROM habits ORDER BY created_at ASC, id ASC")
        return [dict(r) for r in cur.fetchall()]


@app.post("/api/habits", status_code=201)
def create_habit(h: HabitIn):
    if not h.name.strip():
        raise HTTPException(400, "name cannot be empty")
    freq = max(1, min(7, h.frequency))
    with get_db() as cur:
        cur.execute(
            "INSERT INTO habits (name, emoji, color, frequency) VALUES (%s,%s,%s,%s) RETURNING id",
            (h.name.strip(), h.emoji, h.color, freq),
        )
        new_id = cur.fetchone()["id"]
    return {"id": new_id, **h.model_dump()}


@app.put("/api/habits/{habit_id}")
def update_habit(habit_id: int, h: HabitIn):
    if not h.name.strip():
        raise HTTPException(400, "name cannot be empty")
    freq = max(1, min(7, h.frequency))
    with get_db() as cur:
        cur.execute(
            "UPDATE habits SET name=%s, emoji=%s, color=%s, frequency=%s WHERE id=%s",
            (h.name.strip(), h.emoji, h.color, freq, habit_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Not found")
    return {"id": habit_id, **h.model_dump()}


@app.delete("/api/habits/{habit_id}")
def delete_habit(habit_id: int):
    with get_db() as cur:
        cur.execute("DELETE FROM habits WHERE id=%s", (habit_id,))
    return {"ok": True}


@app.post("/api/completions/toggle")
def toggle_completion(habit_id: int, date_str: str):
    with get_db() as cur:
        cur.execute(
            "SELECT id FROM completions WHERE habit_id=%s AND date=%s",
            (habit_id, date_str),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "DELETE FROM completions WHERE habit_id=%s AND date=%s",
                (habit_id, date_str),
            )
            return {"done": False}
        else:
            cur.execute(
                "INSERT INTO completions (habit_id, date) VALUES (%s,%s)",
                (habit_id, date_str),
            )
            return {"done": True}


@app.get("/api/completions")
def get_completions(start_date: Optional[str] = None, end_date: Optional[str] = None):
    with get_db() as cur:
        query = "SELECT * FROM completions WHERE 1=1"
        params = []
        if start_date:
            query += " AND date>=%s"; params.append(start_date)
        if end_date:
            query += " AND date<=%s"; params.append(end_date)
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


@app.get("/api/tasks")
def list_tasks():
    with get_db() as cur:
        cur.execute(
            "SELECT * FROM tasks ORDER BY "
            "CASE WHEN status='done' THEN 2 ELSE 0 END, "
            "CASE WHEN due_date='' THEN 1 ELSE 0 END, "
            "due_date ASC, created_at ASC"
        )
        return [dict(r) for r in cur.fetchall()]


@app.post("/api/tasks", status_code=201)
def create_task(t: TaskIn):
    if not t.title.strip():
        raise HTTPException(400, "title cannot be empty")
    status = t.status if t.status in ('todo', 'in_progress', 'done') else 'todo'
    with get_db() as cur:
        cur.execute(
            "INSERT INTO tasks (title, due_date, status, note) VALUES (%s,%s,%s,%s) RETURNING id",
            (t.title.strip(), t.due_date, status, t.note),
        )
        new_id = cur.fetchone()["id"]
    return {"id": new_id, "title": t.title.strip(), "due_date": t.due_date, "status": status, "note": t.note}


@app.put("/api/tasks/{task_id}")
def update_task(task_id: int, t: TaskIn):
    if not t.title.strip():
        raise HTTPException(400, "title cannot be empty")
    status = t.status if t.status in ('todo', 'in_progress', 'done') else 'todo'
    with get_db() as cur:
        cur.execute(
            "UPDATE tasks SET title=%s, due_date=%s, status=%s, note=%s WHERE id=%s",
            (t.title.strip(), t.due_date, status, t.note, task_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Not found")
    return {"id": task_id, **t.model_dump()}


@app.post("/api/tasks/{task_id}/toggle")
def toggle_task(task_id: int):
    with get_db() as cur:
        cur.execute("SELECT status FROM tasks WHERE id=%s", (task_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        new_status = 'todo' if row["status"] == 'done' else 'done'
        cur.execute("UPDATE tasks SET status=%s WHERE id=%s", (new_status, task_id))
    return {"id": task_id, "status": new_status}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with get_db() as cur:
        cur.execute("DELETE FROM tasks WHERE id=%s", (task_id,))
    return {"ok": True}


@app.get("/api/stats")
def get_stats():
    today = date.today()
    today_str = str(today)
    start_365 = str(today - timedelta(days=364))
    week_start = today - timedelta(days=today.weekday())

    with get_db() as cur:
        cur.execute("SELECT * FROM habits")
        habits = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM completions WHERE date>=%s AND date<=%s",
            (start_365, today_str),
        )
        completions = [dict(r) for r in cur.fetchall()]

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
