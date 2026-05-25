import hashlib, os, datetime
from sqlalchemy import create_engine, Column, Integer, Text, Boolean, UniqueConstraint, Index, ForeignKey, event
from sqlalchemy.orm import DeclarativeBase, Session, scoped_session, sessionmaker, relationship
from config import DB_PATH

engine = create_engine(f'sqlite:///{DB_PATH}', connect_args={'check_same_thread': False})

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA busy_timeout=5000")
session_factory = sessionmaker(bind=engine)
session = scoped_session(session_factory)

class Base(DeclarativeBase):
    pass

class ScanRoot(Base):
    __tablename__ = 'scan_root'
    id = Column(Integer, primary_key=True)
    path = Column(Text, nullable=False)
    recursive = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True)
    allow_fuzzy = Column(Boolean, default=False)
    fuzzy_image_type = Column(Text, default='main')

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
    md5_hash = Column(Text, default='')  # size_mtime fingerprint for fast change detection (name retained for compat)
    content_md5 = Column(Text, default='')  # real MD5 of file content, computed at scan time; survives DB portability
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
        Index('idx_status_barcode_type', 'status', 'barcode', 'image_type'),
    )

class ImageVersion(Base):
    __tablename__ = 'image_version'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, nullable=False, index=True)
    image_type = Column(Text, nullable=False, default='main')
    version_label = Column(Text, nullable=False)
    folder_mtime = Column(Text, default='')
    content_hash = Column(Text, nullable=False)
    is_latest = Column(Boolean, default=False)
    duplicate_mtimes = Column(Text, default='')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        UniqueConstraint('barcode', 'image_type', 'content_hash', name='uq_barcode_type_content'),
    )

class ExportTask(Base):
    __tablename__ = 'export_task'
    id = Column(Integer, primary_key=True)
    status = Column(Text, default='pending')
    zip_path = Column(Text, default='')
    progress = Column(Integer, default=0)
    total_images = Column(Integer, default=0)
    error_message = Column(Text, default='')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

class BarcodeSetting(Base):
    __tablename__ = 'barcode_setting'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, unique=True, nullable=False, index=True)
    default_main_mtime = Column(Text, default='')
    default_detail_mtime = Column(Text, default='')

class ScanLog(Base):
    __tablename__ = 'scan_log'
    id = Column(Integer, primary_key=True)
    action = Column(Text, default='scan')
    status = Column(Text, default='info')
    message = Column(Text, default='')
    details = Column(Text, default='')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())
