from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Replace with your MySQL credentials
DATABASE_URL = "mysql+aiomysql://root:root@localhost:3306/products_db"

engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

async def get_async_Session():
    async with async_Session() as session:
        yield session