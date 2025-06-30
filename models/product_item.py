from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    listing_price = Column(String)
    currency = Column(String, default="USD")
    url = Column(String, unique=True)
    image_url = Column(String, nullable=True)
    condition = Column(String, default="Unknown")
    seller = Column(String, default="Unknown")
    shipping = Column(String, default="Varies")
    location = Column(String, default="Unknown")
    top_rated = Column(Boolean, default=False)
    source = Column(String)
    original_price = Column(String, nullable=True)
    discount = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
