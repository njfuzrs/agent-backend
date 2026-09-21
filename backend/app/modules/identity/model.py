"""身份模块 ORM：组织、团队、设备、凭据、一次性注册码。

规划 PR-1.3 点名四张业务表。`enroll_codes` 是管理员预置一次性码的落点，
没有它「单次使用」无法原子兑现，所以一并建出。
凭据表**只存 sha256**，明文只在签发响应里出现一次。
"""

from sqlalchemy import Column, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.db import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    org_id = Column(Text, nullable=False, unique=True, index=True)  # 对外 slug，如 corp-shanghai
    name = Column(Text, nullable=False, default="")
    created_at = Column(Text, nullable=False)

    teams = relationship("Team", back_populates="organization")
    devices = relationship("Device", back_populates="organization")
    enroll_codes = relationship("EnrollCode", back_populates="organization")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(Text, nullable=False)  # 组织内 slug，如 infra-platform
    name = Column(Text, nullable=False, default="")
    created_at = Column(Text, nullable=False)

    organization = relationship("Organization", back_populates="teams")
    devices = relationship("Device", back_populates="team")
    enroll_codes = relationship("EnrollCode", back_populates="team")

    __table_args__ = (
        UniqueConstraint("organization_id", "team_id", name="uq_teams_org_slug"),
        Index("idx_teams_organization_id", "organization_id"),
    )


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Text, nullable=False, unique=True, index=True)  # 客户端持久 UUIDv4
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(Text, nullable=False, default="")
    platform = Column(Text, nullable=False, default="")
    ver = Column(Text, nullable=False, default="")
    last_seen_at = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)

    organization = relationship("Organization", back_populates="devices")
    team = relationship("Team", back_populates="devices")
    credentials = relationship("DeviceCredential", back_populates="device")

    __table_args__ = (
        Index("idx_devices_organization_id", "organization_id"),
        Index("idx_devices_last_seen_at", "last_seen_at"),
        Index("idx_devices_user_id", "user_id"),
    )


class DeviceCredential(Base):
    """设备凭据。token_hash = sha256(明文)，库里永远没有原文。"""

    __tablename__ = "device_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(Text, nullable=False, unique=True, index=True)
    expires_at = Column(Text, nullable=False)
    revoked_at = Column(Text, nullable=True)
    last_used_at = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)

    device = relationship("Device", back_populates="credentials")

    __table_args__ = (
        Index("idx_device_credentials_device_id", "device_id"),
        Index("idx_device_credentials_expires_at", "expires_at"),
    )


class EnrollCode(Base):
    """一次性注册码。code_hash = sha256(明文)，用过即写 used_at。"""

    __tablename__ = "enroll_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code_hash = Column(Text, nullable=False, unique=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    expires_at = Column(Text, nullable=False)
    used_at = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)
    created_by = Column(Text, nullable=False, default="")
    note = Column(Text, nullable=False, default="")

    organization = relationship("Organization", back_populates="enroll_codes")
    team = relationship("Team", back_populates="enroll_codes")

    __table_args__ = (
        Index("idx_enroll_codes_organization_id", "organization_id"),
        Index("idx_enroll_codes_used_at", "used_at"),
    )
