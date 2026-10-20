"""
SQLAlchemy declarative base.

All ORM models inherit from Base. Kept in its own file so both
the models and Alembic can import it without circular dependencies.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass
