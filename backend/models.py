import hashlib, os, datetime
from sqlalchemy import create_engine, Column, Integer, Text, Boolean, UniqueConstraint, Index, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Session, relationship
from config import DB_PATH

engine = create_engine(f'sqlite:///{DB_PATH}', connect_args={'check_same_thread': False})
session = Session(engine)

class Base(DeclarativeBase):
    pass

class ScanRoot(Base):
    __tablename__ = 'scan_root'
    id = Column(Integer, primary_key=True)
    path = Column(Text, nullable=False)
    recursive = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True)

class Image(Base):
    __tablename__ = 'image'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, nullable=False, index=True)
    image_type = Column(Text, nullable=False, default='main')
    sequence = Column(Integer, default=0)
    filename = Column(Text, nullable=False)
    ext = Column(Text, nullable=False)
    file_path = Column(Text, unique=True, nullable=False)
    file_size = Column(Integer, default=0)
    md5_hash = Column(Text, default='')
    folder_path = Column(Text, default='')
    folder_mtime = Column(Text, default='')
    scan_root_id = Column(Integer, ForeignKey('scan_root.id'), nullable=False)
    confirmed = Column(Boolean, default=True)
    status = Column(Text, default='active')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())
    updated_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        Index('idx_barcode_type', 'barcode', 'image_type'),
        Index('idx_md5', 'md5_hash'),
        Index('idx_folder_mtime', 'folder_mtime'),
    )

class ImageVersion(Base):
    __tablename__ = 'image_version'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, nullable=False, index=True)
    version_label = Column(Text, nullable=False)
    folder_mtime = Column(Text, default='')
    content_hash = Column(Text, nullable=False)
    is_latest = Column(Boolean, default=False)
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        UniqueConstraint('barcode', 'content_hash', name='uq_barcode_content'),
    )

class ExportTask(Base):
    __tablename__ = 'export_task'
    id = Column(Integer, primary_key=True)
    status = Column(Text, default='pending')
    zip_path = Column(Text, default='')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

class ScanLog(Base):
    __tablename__ = 'scan_log'
    id = Column(Integer, primary_key=True)
    action = Column(Text, default='scan')
    status = Column(Text, default='info')
    message = Column(Text, default='')
    details = Column(Text, default='')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())
