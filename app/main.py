"""
Stamp Card App — MVP backend.

Run with:  uvicorn app.main:app --reload
Then visit http://localhost:8000/docs to test endpoints directly,
or http://localhost:8000/join/1 to simulate a customer scanning the shop's QR code.
"""
import os
import time
import uuid

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Form, Header, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


load_dotenv()

from app.database import get_db, Shop, Customer
from app import google_wallet

SECRET_KEY = os.environ.get("SECRET_KEY")
HOST_IP = os.environ.get("HOST_IP")

app = FastAPI(title="Stamp Card")

def get_client_ip(request: Request) -> str:
    # Behind Cloudflare Tunnel, the real client IP arrives in this header
    return request.headers.get("CF-Connecting-IP") or get_remote_address(request)


limiter = Limiter(key_func=get_client_ip)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        f"http://{HOST_IP}:5173",
        f"http://{HOST_IP}:5174",
        "https://localhost:5173",
        "https://localhost:5174",
        f"https://{HOST_IP}:5173",
        f"https://{HOST_IP}:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.on_event("startup")
def seed_shop():
    """Creates your one test shop automatically the first time you run this."""
    db = next(get_db())
    if not db.query(Shop).first():
        shop = Shop(name="My Test Cafe", reward_description="1 Free Coffee",
                    stamps_required=10, owner_pin=os.environ.get("OWNER_PIN"))
        db.add(shop)
        db.commit()
        print(f"Created test shop with id={shop.id}")


# ---------- CUSTOMER FLOW ----------

@app.get("/join/{shop_id}", response_class=HTMLResponse)
def join_shop(shop_id: int, db: Session = Depends(get_db)):
    """
    This is what the QR code / NFC tag on the counter points to.
    Sends the customer to the onboarding form — no Customer record yet.
    """
    shop = db.query(Shop).get(shop_id)
    if not shop:
        raise HTTPException(404, "Shop not found")

    return RedirectResponse(f"https://{HOST_IP}:5174/onboard/{shop_id}")


@app.post("/api/onboard/{shop_id}")
def onboard_customer(
    shop_id: int,
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone_number: str = Form(...),
    db: Session = Depends(get_db)
):
    """Creates the Customer record once the onboarding form is submitted,
    or returns the existing card if this phone number already joined this shop."""
    shop = db.query(Shop).get(shop_id)
    if not shop:
        raise HTTPException(404, "Shop not found")

    existing = db.query(Customer).filter(
        Customer.shop_id == shop_id,
        Customer.phone_number == phone_number
    ).first()
    if existing:
        return {"identifier": existing.identifier}

    identifier = str(uuid.uuid4())[:8]
    customer = Customer(
        shop_id=shop.id,
        identifier=identifier,
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    return {"identifier": customer.identifier}


@app.get("/api/card/{identifier}")
def get_card_data(identifier: str, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer:
        raise HTTPException(404, "Card not found")
    shop = customer.shop
    return {
        "shop_name": shop.name,
        "first_name": customer.first_name,
        "last_name": customer.last_name,
        "stamp_count": customer.stamp_count,
        "stamps_required": shop.stamps_required,
        "reward_description": shop.reward_description,
        "active_redeem_code": customer.redeem_code if not customer.redeem_code_used else None
    }


@app.post("/api/card/{identifier}/generate-code")
def generate_redeem_code(identifier: str, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer or customer.stamp_count < customer.shop.stamps_required:
        raise HTTPException(400, "Reward not ready")

    code = str(uuid.uuid4())[:6].upper()
    customer.redeem_code = code
    customer.redeem_code_used = False
    db.commit()

    return {"code": code}


@app.get("/api/card/{identifier}/wallet-link")
def get_wallet_link(identifier: str, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer:
        raise HTTPException(404, "Card not found")
    shop = customer.shop

    wallet_link = google_wallet.create_or_update_pass(
        customer.identifier, shop.name, customer.stamp_count,
        shop.stamps_required, shop.reward_description
    )
    customer.google_wallet_object_id = customer.identifier
    db.commit()

    return {"wallet_link": wallet_link}


# ---------- OWNER AUTH ----------

@app.post("/owner/login")
@limiter.limit("5/minute")
def owner_login(request: Request, shop_id: int = Form(...), pin: str = Form(...), db: Session = Depends(get_db)):
    """Checks the shop's PIN and, if correct, issues a signed JWT the owner app
    uses for every subsequent request instead of resending the raw PIN."""
    shop = db.query(Shop).get(shop_id)
    if not shop or pin != shop.owner_pin:
        raise HTTPException(403, "Wrong PIN")

    claims = {
        "shop_id": shop_id,
        "role": "owner",
        "exp": int(time.time()) + 60 * 60 * 24
    }
    token = jwt.encode(claims, SECRET_KEY, algorithm="HS256")
    return {"token": token}


def verify_owner_token(authorization: str = Header(...)):
    """Dependency used by every protected owner route below. Reads the
    Authorization: Bearer <token> header, verifies it, and returns the
    shop_id embedded in it (or raises 401 if missing/expired/invalid)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid token")
    token = authorization.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired, please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    return payload["shop_id"]


def sync_wallet_pass(identifier, shop_name, stamp_count, stamps_required, reward_description):
    try:
        google_wallet.create_or_update_pass(
            identifier, shop_name, stamp_count,
            stamps_required, reward_description
        )
    except Exception as e:
        print(f"Wallet sync failed for {identifier}: {e}")

# ---------- OWNER FLOW (all routes below require a valid owner token) ----------

@app.post("/owner/{shop_id}/stamp")
def add_stamp(background_tasks: BackgroundTasks, shop_id: int, identifier: str = Form(...), db: Session = Depends(get_db),
               token_shop_id: int = Depends(verify_owner_token)):
    if token_shop_id != shop_id:
        raise HTTPException(403, "Not authorized for this shop")
    shop = db.query(Shop).get(shop_id)

    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer:
        raise HTTPException(404, "Customer not found")

    if customer.last_stamped_at is not None:
        seconds_since = (datetime.utcnow() - customer.last_stamped_at).total_seconds()
        if seconds_since < 10:
            raise HTTPException(429, "Card was just stamped")

    if customer.stamp_count >= shop.stamps_required:
        customer.stamp_count = 0
        customer.reward_ready = False
        customer.reward_redeemed_count += 1
        customer.last_stamped_at = datetime.utcnow()
        db.commit()
        background_tasks.add_task(
            sync_wallet_pass,
            customer.identifier, shop.name, customer.stamp_count,
            shop.stamps_required, shop.reward_description
        )
        return {"action": "redeemed", "reward_description": shop.reward_description}

    customer.stamp_count += 1
    if customer.stamp_count >= shop.stamps_required:
        customer.reward_ready = True
    customer.last_stamped_at = datetime.utcnow()
    db.commit()
    background_tasks.add_task(
        sync_wallet_pass,
        customer.identifier, shop.name, customer.stamp_count,
        shop.stamps_required, shop.reward_description
    )
    return {"action": "stamped", "stamp_count": customer.stamp_count, "stamps_required": shop.stamps_required}



@app.post("/owner/{shop_id}/verify-code")
def verify_redeem_code(shop_id: int, token_shop_id: int = Depends(verify_owner_token),
                        code: str = Form(...), db: Session = Depends(get_db)):
    if token_shop_id != shop_id:
        raise HTTPException(403, "Not authorized for this shop")
    customer = db.query(Customer).filter(
        Customer.redeem_code == code,
        Customer.redeem_code_used == False
    ).first()

    if not customer:
        raise HTTPException(400, "Invalid or already-used code")

    customer.stamp_count = 0
    customer.redeem_code_used = True
    customer.reward_ready = False
    customer.reward_redeemed_count += 1
    db.commit()

    return {"success": True}


@app.post("/owner/{shop_id}/set-name")
def set_customer_name(shop_id: int, token_shop_id: int = Depends(verify_owner_token),
                       identifier: str = Form(...), first_name: str = Form(...),
                       last_name: str = Form(...), db: Session = Depends(get_db)):
    if token_shop_id != shop_id:
        raise HTTPException(403, "Not authorized for this shop")

    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer:
        raise HTTPException(404, "Customer not found")

    customer.first_name = first_name
    customer.last_name = last_name
    db.commit()

    return {"success": True}


@app.get("/api/owner/{shop_id}/customers")
def get_customers(shop_id: int, token_shop_id: int = Depends(verify_owner_token),
                   db: Session = Depends(get_db)):
    shop = db.query(Shop).get(shop_id)
    if token_shop_id != shop_id:
        raise HTTPException(403, "Not authorized for this shop")

    customers = db.query(Customer).filter(Customer.shop_id == shop_id).all()
    return [
        {
            "identifier": c.identifier,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "stamp_count": c.stamp_count,
            "stamps_required": shop.stamps_required
        }
        for c in customers
    ]


# ---------- QR CODE GENERATION ----------

@app.get("/qr/{shop_id}")
def get_qr_code(shop_id: int, request_base_url: str = f"https://{HOST_IP}:8000"):
    import qrcode
    import io
    from fastapi.responses import StreamingResponse

    url = f"{request_base_url}/join/{shop_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")