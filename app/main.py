"""
Stamp Card App — MVP backend.

Run with:  uvicorn app.main:app --reload
Then visit http://localhost:8000/docs to test endpoints directly,
or http://localhost:8000/join/1 to simulate a customer scanning the shop's QR code.
"""
import uuid
from fastapi import FastAPI, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import init_db, get_db, Shop, Customer
from app import google_wallet
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI(title="Stamp Card")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://192.168.1.148:5173",
        "http://192.168.1.148:5174",
        "https://localhost:5173",
        "https://localhost:5174",
        "https://192.168.1.148:5173",
        "https://192.168.1.148:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


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
    First-time scan: creates a new customer record + shows the 'Add to Wallet' button.
    """
    shop = db.query(Shop).get(shop_id)
    if not shop:
        raise HTTPException(404, "Shop not found")

    identifier = str(uuid.uuid4())[:8]
    customer = Customer(shop_id=shop.id, identifier=identifier)
    db.add(customer)
    db.commit()
    db.refresh(customer)

    return RedirectResponse(f"https://192.168.1.148:5174/card/{customer.identifier}")


@app.get("/card/{identifier}", response_class=HTMLResponse)
def view_card(identifier: str, db: Session = Depends(get_db)):
    """The customer-facing stamp card page (also doubles as the 'Add to Wallet' page)."""
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

    filled = "☕" * customer.stamp_count
    empty = "⚪" * max(shop.stamps_required - customer.stamp_count, 0)
    status = (
        f"<h2 style='color:green'>🎉 Reward ready: {shop.reward_description}!</h2>"
        if customer.stamp_count >= shop.stamps_required else ""
    )

    return f"""
    <html><body style="font-family: sans-serif; text-align:center; padding:40px;">
        <h1>{shop.name}</h1>
        <p>Your card: <strong>{customer.identifier}</strong></p>
        <p style="font-size:32px;">{filled}{empty}</p>
        <p>{customer.stamp_count} / {shop.stamps_required} stamps</p>
        {status}
        <a href="{wallet_link}">
            <button style="padding:12px 24px; font-size:16px;">Add to Google Wallet</button>
        </a>
    </body></html>
    """

@app.get("/api/card/{identifier}")
def get_card_data(identifier: str, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer:
        raise HTTPException(404, "Card not found")
    shop = customer.shop
    return {
        "shop_name": shop.name,
        "customer_name": customer.name,
        "stamp_count": customer.stamp_count,
        "stamps_required": shop.stamps_required,
        "reward_description": shop.reward_description,
        "active_redeem_code": customer.redeem_code if not customer.redeem_code_used else None
    }

# ---------- OWNER FLOW ----------

@app.get("/owner/{shop_id}", response_class=HTMLResponse)
def owner_panel(shop_id: int, pin: str = "", db: Session = Depends(get_db)):
    """Simple PIN-protected panel to add a stamp to a customer's card."""
    shop = db.query(Shop).get(shop_id)
    if not shop:
        raise HTTPException(404, "Shop not found")
    if pin != shop.owner_pin:
        return """
        <html><body style="font-family:sans-serif; text-align:center; padding:40px;">
            <h2>Enter owner PIN</h2>
            <form method="get">
                <input type="password" name="pin" placeholder="PIN">
                <button type="submit">Enter</button>
            </form>
        </body></html>
        """

    customers = db.query(Customer).filter(Customer.shop_id == shop_id).all()
    rows = "".join(
        f"<tr><td>{c.identifier}</td><td>{c.name or '—'}</td><td>{c.stamp_count}/{shop.stamps_required}</td>"
        f"<td>{'✅ READY' if c.stamp_count >= shop.stamps_required else ''}</td>"
        f"<td><form method='post' action='/owner/{shop_id}/stamp?pin={pin}'>"
        f"<input type='hidden' name='identifier' value='{c.identifier}'>"
        f"<button type='submit'>+1 Stamp</button></form></td>"
        f"<td></td></tr>"
        for c in customers
    )

    return f"""
    <html><body style="font-family: sans-serif; padding:40px;">
        <h1>{shop.name} — Owner Panel</h1>
        <table border="1" cellpadding="8" style="border-collapse:collapse;">
            <tr><th>Customer ID</th><th>Name</th><th>Stamps</th><th>Status</th><th>Action</th><th></th></tr>
            {rows}
        </table>
        <h2>Redeem a code</h2>
<form method='post' action='/owner/{shop_id}/verify-code?pin={pin}'>
    <input type='text' name='code' placeholder='Enter customer code'>
    <button type='submit'>Verify & Redeem</button>
</form>
<h2>Set customer name</h2>
<form method='post' action='/owner/{shop_id}/set-name?pin={pin}'>
    <input type='text' name='identifier' placeholder='Customer ID'>
    <input type='text' name='name' placeholder='Customer name'>
    <button type='submit'>Save Name</button>
</form>
    </body></html>
    """


@app.post("/owner/{shop_id}/stamp")
def add_stamp(shop_id: int, pin: str, identifier: str = Form(...), db: Session = Depends(get_db)):
    shop = db.query(Shop).get(shop_id)
    if pin != shop.owner_pin:
        raise HTTPException(403, "Wrong PIN")

    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer:
        raise HTTPException(404, "Customer not found")

    if customer.stamp_count < shop.stamps_required:
        customer.stamp_count += 1
        if customer.stamp_count >= shop.stamps_required:
            customer.reward_ready = True
    db.commit()

    # Push the live update to their wallet pass
    google_wallet.create_or_update_pass(
        customer.identifier, shop.name, customer.stamp_count,
        shop.stamps_required, shop.reward_description
    )

    return RedirectResponse(f"/owner/{shop_id}?pin={pin}", status_code=303)

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

@app.post("/owner/{shop_id}/verify-code")
def verify_redeem_code(shop_id: int, pin: str, code: str = Form(...), db: Session = Depends(get_db)):
    shop = db.query(Shop).get(shop_id)
    if pin != shop.owner_pin:
        raise HTTPException(403, "Wrong PIN")

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

    return RedirectResponse(f"/owner/{shop_id}?pin={pin}", status_code=303)

@app.post("/owner/{shop_id}/set-name")
def set_customer_name(shop_id: int, pin: str, identifier: str = Form(...), name: str = Form(...), db: Session = Depends(get_db)):
    shop = db.query(Shop).get(shop_id)
    if pin != shop.owner_pin:
        raise HTTPException(403, "Wrong PIN")

    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer:
        raise HTTPException(404, "Customer not found")

    customer.name = name
    db.commit()

    return RedirectResponse(f"/owner/{shop_id}?pin={pin}", status_code=303)

@app.post("/api/card/{identifier}/set-name")
def set_customer_name_self(identifier: str, name: str = Form(...), db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.identifier == identifier).first()
    if not customer:
        raise HTTPException(404, "Card not found")

    customer.name = name
    db.commit()

    return {"success": True, "name": customer.name}

@app.get("/api/owner/{shop_id}/customers")
def get_customers(shop_id: int, pin: str, db: Session = Depends(get_db)):
    shop = db.query(Shop).get(shop_id)
    if pin != shop.owner_pin:
        raise HTTPException(403, "Wrong PIN")

    customers = db.query(Customer).filter(Customer.shop_id == shop_id).all()
    return [
        {
            "identifier": c.identifier,
            "name": c.name,
            "stamp_count": c.stamp_count,
            "stamps_required": shop.stamps_required
        }
        for c in customers
    ]


# ---------- QR CODE GENERATION ----------

@app.get("/qr/{shop_id}")
def get_qr_code(shop_id: int, request_base_url: str = "https://192.168.1.148:8000"):
    url = f"{request_base_url}/join/{shop_id}"
    import qrcode
    import io
    from fastapi.responses import StreamingResponse

    url = f"{request_base_url}/join/{shop_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

