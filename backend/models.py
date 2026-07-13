import hashlib, os, datetime
from sqlalchemy import create_engine, Column, Integer, Text, Boolean, UniqueConstraint, Index, ForeignKey, event
from sqlalchemy.orm import DeclarativeBase, Session, scoped_session, sessionmaker, relationship
from config import DB_PATH

engine = create_engine(f'sqlite:///{DB_PATH}', connect_args={'check_same_thread': False})

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    try:
        # WAL: 允许读写并发，单写者模型下显著降低 'database is locked'
        cur.execute("PRAGMA journal_mode=WAL")
        # 写锁竞争时最长等待 15s 再报 locked（原 5s，给后台任务+请求线程更多缓冲）
        cur.execute("PRAGMA busy_timeout=15000")
        # WAL 模式下 NORMAL 是官方推荐：断电最多丢失最后几个事务，绝不损坏库，写入显著加速
        cur.execute("PRAGMA synchronous=NORMAL")
        # 64MB 页缓存（负值=KB）
        cur.execute("PRAGMA cache_size=-65536")
        # 临时表/索引放内存
        cur.execute("PRAGMA temp_store=MEMORY")
        # 256MB 内存映射读，减少大表查询的 syscall
        cur.execute("PRAGMA mmap_size=268435456")
    finally:
        cur.close()
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
    phash = Column(Text, default='')  # perceptual hash (64-bit hex), computed at scan time for visual similarity
    folder_path = Column(Text, default='')
    folder_ctime = Column('folder_mtime', Text, default='')
    scan_root_id = Column(Integer, ForeignKey('scan_root.id'), nullable=False)
    confirmed = Column(Boolean, default=True)
    status = Column(Text, default='active')
    last_scan_token = Column(Text, default='')  # token-based leftover detection
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())
    updated_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        Index('idx_barcode_type', 'barcode', 'image_type'),
        Index('idx_md5', 'md5_hash'),
        Index('idx_folder_ctime', 'folder_mtime'),
        Index('idx_status_barcode_type', 'status', 'barcode', 'image_type'),
        Index('idx_barcode_type_ctime', 'barcode', 'image_type', 'folder_mtime'),
        Index('idx_scanroot_status', 'scan_root_id', 'status'),
        Index('idx_scanroot_folderpath', 'scan_root_id', 'folder_path'),
        Index('idx_scanroot_token', 'scan_root_id', 'last_scan_token'),
    )

class ImageVersion(Base):
    __tablename__ = 'image_version'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, nullable=False, index=True)
    image_type = Column(Text, nullable=False, default='main')
    version_label = Column(Text, nullable=False)
    folder_ctime = Column('folder_mtime', Text, default='')
    content_hash = Column(Text, nullable=False)
    is_latest = Column(Boolean, default=False)
    duplicate_mtimes = Column(Text, default='')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        UniqueConstraint('barcode', 'image_type', 'content_hash', name='uq_barcode_type_content'),
        Index('idx_iv_barcode_type', 'barcode', 'image_type'),
        Index('idx_iv_barcode_type_latest', 'barcode', 'image_type', 'is_latest'),
    )

class ExportTask(Base):
    __tablename__ = 'export_task'
    id = Column(Integer, primary_key=True)
    status = Column(Text, default='pending')
    zip_path = Column(Text, default='')
    progress = Column(Integer, default=0)
    total_images = Column(Integer, default=0)
    error_message = Column(Text, default='')
    barcode_data = Column(Text, default='')  # JSON: {barcode: {main: N, detail: N}}
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

class BarcodeSetting(Base):
    __tablename__ = 'barcode_setting'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, unique=True, nullable=False, index=True)
    default_main_ctime = Column('default_main_mtime', Text, default='')
    default_detail_ctime = Column('default_detail_mtime', Text, default='')

class ScanLog(Base):
    __tablename__ = 'scan_log'
    id = Column(Integer, primary_key=True)
    action = Column(Text, default='scan')
    status = Column(Text, default='info')
    message = Column(Text, default='')
    details = Column(Text, default='')
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())


class BatchTask(Base):
    __tablename__ = 'batch_task'
    id = Column(Integer, primary_key=True)
    task_type = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default='queued')
    progress = Column(Integer, default=0)
    total = Column(Integer, default=0)
    result_count = Column(Integer, default=0)
    error_message = Column(Text, default='')
    params_json = Column(Text, default='{}')
    current_item = Column(Text, default='')  # 当前处理对象（删除任务等）
    failed_count = Column(Integer, default=0)  # 失败计数
    failed_items = Column(Text, default='[]')  # 失败样本 JSON（最多 20 条）
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())
    started_at = Column(Text, default='')
    finished_at = Column(Text, default='')

    __table_args__ = (
        Index('idx_task_type_status', 'task_type', 'status', 'created_at'),
    )


class DuplicateScanResult(Base):
    __tablename__ = 'duplicate_scan_result'
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('batch_task.id'), nullable=False)
    barcode = Column(Text, nullable=False)
    image_type = Column(Text, nullable=False)
    version_label = Column(Text)
    version_folder_ctime = Column(Text)
    folder_ctime = Column(Text, nullable=False)
    image_count = Column(Integer, default=0)
    total_file_size = Column(Integer, default=0)
    delete_status = Column(Text, default='pending')
    delete_message = Column(Text, default='')
    deleted_at = Column(Text, default='')

    __table_args__ = (
        Index('idx_dup_task_id', 'task_id'),
        Index('idx_dup_task_barcode', 'task_id', 'barcode'),
    )


class LowVersionScanResult(Base):
    __tablename__ = 'low_version_scan_result'
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('batch_task.id'), nullable=False)
    barcode = Column(Text, nullable=False)
    image_type = Column(Text, nullable=False)
    version_label = Column(Text)
    folder_ctime = Column(Text, nullable=False)
    image_count = Column(Integer, default=0)
    total_file_size = Column(Integer, default=0)
    is_latest = Column(Boolean, default=False)
    is_only_version = Column(Boolean, default=False)
    meets_threshold = Column(Boolean, default=False)
    main_threshold = Column(Integer, default=0)
    detail_threshold = Column(Integer, default=0)
    status_tag = Column(Text, default='will_delete')
    delete_status = Column(Text, default='pending')
    delete_message = Column(Text, default='')
    deleted_at = Column(Text, default='')

    __table_args__ = (
        Index('idx_lv_task_id', 'task_id'),
        Index('idx_lv_task_barcode', 'task_id', 'barcode'),
    )


class RejectedBarcode(Base):
    __tablename__ = 'rejected_barcode'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, nullable=False)
    file_path = Column(Text, nullable=False)
    filename = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    scan_root_id = Column(Integer, ForeignKey('scan_root.id'), nullable=False)
    created_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        Index('idx_rejected_barcode', 'barcode'),
        Index('idx_rejected_scan_root', 'scan_root_id'),
        Index('idx_rejected_created', 'created_at'),
        UniqueConstraint('scan_root_id', 'barcode', 'file_path', name='uq_rejected_root_barcode_path'),
    )


class DuplicateVersionScanResult(Base):
    """Results of duplicate-version detection scan.
    Each row represents one version within a duplicate group."""
    __tablename__ = 'duplicate_version_scan_result'
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('batch_task.id'), nullable=False)
    group_id = Column(Integer, nullable=False)  # versions sharing the same group_id are duplicates
    barcode = Column(Text, nullable=False)
    image_type = Column(Text, nullable=False)
    folder_ctime = Column(Text, nullable=False)
    version_label = Column(Text, default='')
    image_count = Column(Integer, default=0)
    total_file_size = Column(Integer, default=0)
    total_pixels = Column(Integer, default=0)  # sum of width*height for all images
    is_latest = Column(Boolean, default=False)
    role = Column(Text, default='clean')  # 'keep' | 'clean' | 'user_selected'
    keep_reason = Column(Text, default='')  # reason for recommendation
    delete_status = Column(Text, default='pending')  # 'pending' | 'deleted' | 'skipped' | 'failed'
    delete_message = Column(Text, default='')
    deleted_at = Column(Text, default='')
    kept_version_ctime = Column(Text, default='')  # folder_ctime of the kept version

    __table_args__ = (
        Index('idx_dvsr_task_id', 'task_id'),
        Index('idx_dvsr_task_group', 'task_id', 'group_id'),
        Index('idx_dvsr_task_barcode', 'task_id', 'barcode'),
    )


class DeletedFolder(Base):
    """Tracks deleted (barcode, image_type, folder_ctime, scan_root_id) so the
    scanner can skip them and avoid re-adding files the user intentionally
    deleted — scoped per scan root so roots do not blacklist each other."""
    __tablename__ = 'deleted_folders'
    id = Column(Integer, primary_key=True)
    barcode = Column(Text, nullable=False)
    image_type = Column(Text, nullable=False)
    folder_ctime = Column(Text, nullable=False)
    # 0 = legacy rows (pre-root-scoped); real roots use ScanRoot.id
    scan_root_id = Column(Integer, nullable=False, default=0)
    deleted_at = Column(Text, default=lambda: datetime.datetime.now().isoformat())

    __table_args__ = (
        UniqueConstraint(
            'barcode', 'image_type', 'folder_ctime', 'scan_root_id',
            name='uq_deleted_folder_root',
        ),
        Index('idx_df_barcode_type', 'barcode', 'image_type'),
        Index('idx_df_root', 'scan_root_id'),
    )
