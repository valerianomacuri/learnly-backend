from sqlalchemy import Column, ForeignKey, String, Table, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column(
        "user_id",
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        Uuid,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column(
        "role_id",
        Uuid,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        Uuid,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class Permission(IdMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class Role(IdMixin, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True)

    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, order_by=Permission.code
    )


__all__ = ["Permission", "Role", "role_permissions", "user_roles"]
