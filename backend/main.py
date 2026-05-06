from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import anthropic, os, psycopg2, psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS choco_sales (
                    id VARCHAR(50) PRIMARY KEY,
                    date DATE NOT NULL,
                    customer VARCHAR(100),
                    card INTEGER DEFAULT 0,
                    cash INTEGER DEFAULT 0,
                    credit INTEGER DEFAULT 0,
                    total INTEGER DEFAULT 0,
                    memo TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS choco_purchases (
                    id VARCHAR(50) PRIMARY KEY,
                    date DATE NOT NULL,
                    vendor VARCHAR(100),
                    category VARCHAR(100),
                    amount INTEGER DEFAULT 0,
                    payment VARCHAR(10),
                    memo TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS choco_credits (
                    id VARCHAR(50) PRIMARY KEY,
                    name VARCHAR(100),
                    date DATE NOT NULL,
                    amount INTEGER DEFAULT 0,
                    type VARCHAR(10),
                    memo TEXT
                )
            """)
        conn.commit()


init_db()


# ── AI Parse ──────────────────────────────────────────────────────────────────

class ParseRequest(BaseModel):
    prompt: str

@app.post("/api/parse")
async def parse(req: ParseRequest):
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": req.prompt}],
        )
        return {"text": message.content[0].text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Sales ─────────────────────────────────────────────────────────────────────

class Sale(BaseModel):
    id: str
    date: str
    customer: Optional[str] = "손님"
    card: Optional[int] = 0
    cash: Optional[int] = 0
    credit: Optional[int] = 0
    total: Optional[int] = 0
    memo: Optional[str] = ""

@app.get("/api/sales")
def get_sales():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, date::text, customer, card, cash, credit, total, memo FROM choco_sales ORDER BY date DESC, id DESC")
            return cur.fetchall()

@app.post("/api/sales")
def add_sale(s: Sale):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO choco_sales (id,date,customer,card,cash,credit,total,memo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (s.id, s.date, s.customer, s.card, s.cash, s.credit, s.total, s.memo)
            )
        conn.commit()
    return {"ok": True}

@app.delete("/api/sales/{id}")
def del_sale(id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM choco_sales WHERE id=%s", (id,))
        conn.commit()
    return {"ok": True}


# ── Purchases ─────────────────────────────────────────────────────────────────

class Purchase(BaseModel):
    id: str
    date: str
    vendor: Optional[str] = ""
    category: Optional[str] = ""
    amount: Optional[int] = 0
    payment: Optional[str] = "cash"
    memo: Optional[str] = ""

@app.get("/api/purchases")
def get_purchases():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, date::text, vendor, category, amount, payment, memo FROM choco_purchases ORDER BY date DESC, id DESC")
            return cur.fetchall()

@app.post("/api/purchases")
def add_purchase(p: Purchase):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO choco_purchases (id,date,vendor,category,amount,payment,memo) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (p.id, p.date, p.vendor, p.category, p.amount, p.payment, p.memo)
            )
        conn.commit()
    return {"ok": True}

@app.delete("/api/purchases/{id}")
def del_purchase(id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM choco_purchases WHERE id=%s", (id,))
        conn.commit()
    return {"ok": True}


# ── Credits ───────────────────────────────────────────────────────────────────

class Credit(BaseModel):
    id: str
    name: str
    date: str
    amount: int
    type: str
    memo: Optional[str] = ""

@app.get("/api/credits")
def get_credits():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, date::text, amount, type, memo FROM choco_credits ORDER BY date ASC, id ASC")
            return cur.fetchall()

@app.post("/api/credits")
def add_credit(c: Credit):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO choco_credits (id,name,date,amount,type,memo) VALUES (%s,%s,%s,%s,%s,%s)",
                (c.id, c.name, c.date, c.amount, c.type, c.memo)
            )
        conn.commit()
    return {"ok": True}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}
