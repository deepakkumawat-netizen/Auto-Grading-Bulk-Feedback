import sqlite3, json, time, os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "exam_configs.db")

def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS exam_configs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            board       TEXT NOT NULL,
            grade       INTEGER NOT NULL,
            subject     TEXT NOT NULL,
            chapter     TEXT,
            exam_type   TEXT,
            paper_total INTEGER NOT NULL,
            questions   TEXT NOT NULL,
            instructions TEXT,
            eval_order  TEXT,
            strictness  TEXT,
            rules       TEXT NOT NULL,
            feedback    TEXT NOT NULL,
            created     INTEGER NOT NULL
        )
    """)
    c.commit()
    return c

def save_config(name, board, grade, subject, chapter, exam_type, paper_total,
                questions, instructions, eval_order, strictness, rules, feedback) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO exam_configs
               (name,board,grade,subject,chapter,exam_type,paper_total,
                questions,instructions,eval_order,strictness,rules,feedback,created)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, board, int(grade), subject, chapter, exam_type, int(paper_total),
             json.dumps(questions), instructions, eval_order, strictness,
             json.dumps(rules), json.dumps(feedback), int(time.time()))
        )
        return cur.lastrowid

def list_configs():
    with _conn() as c:
        rows = c.execute(
            "SELECT id,name,board,grade,subject,chapter,exam_type,paper_total,created "
            "FROM exam_configs ORDER BY created DESC"
        ).fetchall()
        return [dict(r) for r in rows]

def get_config(cid: int):
    with _conn() as c:
        row = c.execute("SELECT * FROM exam_configs WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["questions"] = json.loads(d["questions"])
        d["rules"]     = json.loads(d["rules"])
        d["feedback"]  = json.loads(d["feedback"])
        return d

def delete_config(cid: int):
    with _conn() as c:
        c.execute("DELETE FROM exam_configs WHERE id=?", (cid,))
