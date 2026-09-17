"""
Database setup for the Stamp Card app.
Using SQLite for now — easy to start, swap to Postgres later with zero code changes
(just change DATABASE_URL) since we're using SQLAlchemy.
"""
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

DATABASE_URL = "sqlite:///./stampcard.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Shop(Base):
    """
    A business using the app (a cafe, salon, etc).
    For now you'll just have ONE row here — your own test shop.
    """
    __tablename__ = "shops"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    reward_description = Column(String, default="1 Free Coffee")
    stamps_required = Column(Integer, default=10)
    owner_pin = Column(String, nullable=False)  # PIN must be provided explicitly — no hardcoded default

    customers = relationship("Customer", back_populates="shop")


class Customer(Base):
    """
    A single customer's loyalty card at a specific shop.
    Each row = one wallet pass tied to one person at one shop.
    """
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id"))
    identifier = Column(String, unique=True, index=True)  # random token, used in the QR/pass URL
    name = Column(String, nullable=True)  # optional, can be filled in later
    stamp_count = Column(Integer, default=0)
    reward_ready = Column(Boolean, default=False)
    redeem_code = Column(String, nullable=True)
    redeem_code_used = Column(Boolean, default=False)
    reward_redeemed_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Google Wallet object ID, filled in once a pass is issued (needed to push live updates)
    google_wallet_object_id = Column(String, nullable=True)

    shop = relationship("Shop", back_populates="customers")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
