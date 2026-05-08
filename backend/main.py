from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import anthropic, os, psycopg2, psycopg2.extras, uuid, random, json
from datetime import date, timedelta, datetime, timezone
from dotenv import load_dotenv
from fastapi.responses import PlainTextResponse

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
                    memo TEXT,
                    sale_id VARCHAR(50)
                )
            """)
            cur.execute("ALTER TABLE choco_credits ADD COLUMN IF NOT EXISTS sale_id VARCHAR(50)")
        conn.commit()


init_db()


# ── AI Parse ──────────────────────────────────────────────────────────────────

LOG_FILE = "/app/parse_log.txt"

def write_log(input_text: str, ai_raw: str, parsed: list, error: str = ""):
    KST = timezone(timedelta(hours=9))
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"\n{'='*60}",
        f"[시각] {ts}",
        f"[입력]\n{input_text}",
        f"[AI 원본 응답]\n{ai_raw}",
    ]
    if error:
        lines.append(f"[오류] {error}")
    else:
        for i, item in enumerate(parsed, 1):
            extracted = []
            memo_val = item.get('memo', '')
            if item.get('customer'): extracted.append(f"고객: {item['customer']}")
            if item.get('card'):     extracted.append(f"카드: {item['card']:,}")
            if item.get('cash'):     extracted.append(f"현금: {item['cash']:,}")
            if item.get('credit'):   extracted.append(f"외상: {item['credit']:,}")
            if item.get('vendor'):   extracted.append(f"거래처: {item['vendor']}")
            if item.get('category'): extracted.append(f"품목: {item['category']}")
            if item.get('amount'):   extracted.append(f"금액: {item['amount']:,}")
            lines.append(f"[항목{i} 추출] {' | '.join(extracted)}")
            lines.append(f"[항목{i} 메모] {memo_val if memo_val else '(없음)'}")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass

class ParseRequest(BaseModel):
    prompt: str

@app.post("/api/parse")
async def parse(req: ParseRequest):
    # 입력 텍스트만 추출 (프롬프트 마지막 줄)
    input_text = req.prompt.split("반환:\n")[-1].strip() if "반환:\n" in req.prompt else req.prompt
    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": req.prompt}],
        )
        ai_raw = message.content[0].text
        # 로그용 파싱 시도
        try:
            match = __import__('re').search(r'\[[\s\S]*\]', ai_raw)
            parsed = json.loads(match.group()) if match else []
        except Exception:
            parsed = []
        write_log(input_text, ai_raw, parsed)
        return {"text": ai_raw}
    except Exception as e:
        write_log(input_text, "", [], str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/parse_log")
def get_parse_log():
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return PlainTextResponse(f.read())
    except FileNotFoundError:
        return PlainTextResponse("(로그 없음)")

@app.delete("/api/parse_log")
def clear_parse_log():
    try:
        open(LOG_FILE, "w").close()
    except Exception:
        pass
    return {"ok": True}


# ── Sales ─────────────────────────────────────────────────────────────────────

class Sale(BaseModel):
    id: str
    date: str
    customer: Optional[str] = "고객"
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

@app.put("/api/sales/{id}")
def update_sale(id: str, s: Sale):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE choco_sales SET date=%s,customer=%s,card=%s,cash=%s,credit=%s,total=%s,memo=%s WHERE id=%s",
                (s.date, s.customer, s.card, s.cash, s.credit, s.total, s.memo, id)
            )
            # 연결된 외상 항목 동기화
            cur.execute("SELECT id FROM choco_credits WHERE sale_id=%s AND type='debit'", (id,))
            existing = cur.fetchone()
            new_credit = s.credit or 0
            if existing:
                if new_credit > 0:
                    cur.execute(
                        "UPDATE choco_credits SET name=%s, date=%s, amount=%s WHERE sale_id=%s AND type='debit'",
                        (s.customer, s.date, new_credit, id)
                    )
                else:
                    cur.execute("DELETE FROM choco_credits WHERE sale_id=%s AND type='debit'", (id,))
            elif new_credit > 0:
                new_id = uuid.uuid4().hex[:15]
                cur.execute(
                    "INSERT INTO choco_credits (id,name,date,amount,type,memo,sale_id) VALUES (%s,%s,%s,%s,'debit','매출 외상',%s)",
                    (new_id, s.customer, s.date, new_credit, id)
                )
        conn.commit()
    return {"ok": True}

@app.delete("/api/sales/{id}")
def del_sale(id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM choco_sales WHERE id=%s", (id,))
            cur.execute("DELETE FROM choco_credits WHERE sale_id=%s AND type='debit'", (id,))
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

@app.put("/api/purchases/{id}")
def update_purchase(id: str, p: Purchase):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE choco_purchases SET date=%s,vendor=%s,category=%s,amount=%s,payment=%s,memo=%s WHERE id=%s",
                (p.date, p.vendor, p.category, p.amount, p.payment, p.memo, id)
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
    sale_id: Optional[str] = None

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
                "INSERT INTO choco_credits (id,name,date,amount,type,memo,sale_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (c.id, c.name, c.date, c.amount, c.type, c.memo, c.sale_id)
            )
        conn.commit()
    return {"ok": True}


# ── Reset ─────────────────────────────────────────────────────────────────────

@app.delete("/api/reset")
def reset_all():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM choco_credits")
            cur.execute("DELETE FROM choco_sales")
            cur.execute("DELETE FROM choco_purchases")
        conn.commit()
    return {"ok": True}


# ── Seed ──────────────────────────────────────────────────────────────────────

_CUSTOMERS = [
    "김철수","이민준","박성호","최준혁","정재원","한상우","오승환","임태준","강민수","신동현",
    "유재원","이성훈","장우진","현승민","손태양","공준서","전성민","이병수","김태수","고승현",
    "차민호","박준영","변성훈","천민준","정해수","박민석","위성준","전재민","오준혁","김선민",
    "류준성","김준호","조재원","정민준","박성민","주영선","양준혁","이재훈","김지영","이수연"
]
_SALE_MEMOS = [
    "단골 고객","오랜 단골","생일 케이크 주문","법인카드 사용","단체 주문",
    "특별 할인 적용","첫 방문 고객","VIP 고객","예약 고객","재구매 고객",
    "기념일 선물","직장 동료 선물용","온라인 예약 후 방문","포장 요청","선물 포장 추가",
    "","","","",""
]
_VENDORS = [
    "대선주류","롯데칠성음료","동서식품","해태제과","오뚜기","농심","삼양식품","빙그레",
    "매일유업","남양유업","풀무원","CJ제일제당","하이트진로","크라운해태","서울우유",
    "롯데제과","해태음료","동원F&B","사조대림","청정원"
]
_CATEGORIES = [
    "주류","음료","과자","식품","유제품","아이스크림","냉동식품","수입과자","안주류","초콜릿",
    "커피","차류","탄산음료","생수","건강음료"
]
_PURCH_MEMOS = [
    "정기 납품","특별 주문","할인 행사 상품","재고 보충","신제품 입고",
    "계절 상품","긴급 주문","프로모션 상품","거래처 행사","",""
]

@app.post("/api/seed")
def seed_data():
    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).date()
    start = today.replace(year=today.year - 1) + timedelta(days=1)

    sales_rows, purch_rows, credit_rows = [], [], []

    cur_day = start
    while cur_day <= today:
        ds = cur_day.isoformat()

        for _ in range(random.randint(0, 10)):
            sid = uuid.uuid4().hex[:15]
            customer = random.choice(_CUSTOMERS)
            total = random.randint(5, 150) * 10000
            ptype = random.choices(['card','cash','credit'], weights=[65,33,2])[0]
            card  = total if ptype=='card'   else 0
            cash  = total if ptype=='cash'   else 0
            credit= total if ptype=='credit' else 0
            memo  = random.choice(_SALE_MEMOS)
            sales_rows.append((sid, ds, customer, card, cash, credit, total, memo))
            if credit > 0:
                cid = uuid.uuid4().hex[:15]
                credit_rows.append((cid, customer, ds, credit, 'debit', '매출 외상', sid))

        for _ in range(random.randint(0, 3)):
            pid      = uuid.uuid4().hex[:15]
            vendor   = random.choice(_VENDORS)
            category = random.choice(_CATEGORIES)
            amount   = random.randint(10, 100) * 10000
            payment  = random.choices(['card','cash'], weights=[70,30])[0]
            memo     = random.choice(_PURCH_MEMOS)
            purch_rows.append((pid, ds, vendor, category, amount, payment, memo))

        cur_day += timedelta(days=1)

    with get_conn() as conn:
        with conn.cursor() as cur:
            if sales_rows:
                psycopg2.extras.execute_batch(cur,
                    "INSERT INTO choco_sales (id,date,customer,card,cash,credit,total,memo) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    sales_rows)
            if purch_rows:
                psycopg2.extras.execute_batch(cur,
                    "INSERT INTO choco_purchases (id,date,vendor,category,amount,payment,memo) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    purch_rows)
            if credit_rows:
                psycopg2.extras.execute_batch(cur,
                    "INSERT INTO choco_credits (id,name,date,amount,type,memo,sale_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    credit_rows)
        conn.commit()
    return {"ok": True, "sales": len(sales_rows), "purchases": len(purch_rows), "credits": len(credit_rows)}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}
