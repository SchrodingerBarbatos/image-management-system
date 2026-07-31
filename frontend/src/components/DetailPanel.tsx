import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Checkbox, Image, Spin, Button, Modal, Select, App, Popover,
} from 'antd';
import {
  DeleteOutlined, EyeOutlined, InfoCircleOutlined, PictureOutlined,
} from '@ant-design/icons';
import {
  ImageRec, ImageVersion, imageApi, barcodeApi, barcodeSettingApi, taskApi,
} from '../services/api';
import { useTaskPolling } from '../hooks/useTaskPolling';
import { LightBar, Led } from './ui';

const PLACEHOLDER_SVG =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">' +
      '<rect fill="#131619" width="100" height="100" rx="6"/>' +
      '<text x="50" y="55" text-anchor="middle" fill="#5f6a74" font-size="36" font-family="sans-serif">?</text>' +
      '</svg>',
  );

const THUMBNAIL_PAGE_SIZE = 100;
const IMAGE_PAGE_SIZE = 200;
const DUP_MTIME_COLLAPSE = 20;

interface Props {
  barcode: string | null;
  selectedMainIds: Set<number>;
  selectedDetailIds: Set<number>;
  onMainSelectionChange: (ids: Set<number>) => void;
  onDetailSelectionChange: (ids: Set<number>) => void;
  onDeleted: () => void;
}

const DetailPanel: React.FC<Props> = ({
  barcode,
  selectedMainIds,
  selectedDetailIds,
  onMainSelectionChange,
  onDetailSelectionChange,
  onDeleted,
}) => {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [mainLoadingMore, setMainLoadingMore] = useState(false);
  const [detailLoadingMore, setDetailLoadingMore] = useState(false);
  const [images, setImages] = useState<ImageRec[]>([]);
  const [versions, setVersions] = useState<ImageVersion[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<ImageRec | null>(null);
  const [versionDeleteTarget, setVersionDeleteTarget] = useState<ImageVersion | null>(null);
  const [dupDeleteTarget, setDupDeleteTarget] = useState<{
    barcode: string; folderCtime: string; imageType: string;
  } | null>(null);
  const [mainShowCount, setMainShowCount] = useState(THUMBNAIL_PAGE_SIZE);
  const [detailShowCount, setDetailShowCount] = useState(THUMBNAIL_PAGE_SIZE);
  const [collapsedDupVersions, setCollapsedDupVersions] = useState<Set<number>>(new Set());

  // Per-type server-side pagination
  const [mainImagePage, setMainImagePage] = useState(1);
  const [mainImageTotal, setMainImageTotal] = useState(0);
  const [detailImagePage, setDetailImagePage] = useState(1);
  const [detailImageTotal, setDetailImageTotal] = useState(0);

  // Per-type version selection (stored as folder_ctime)
  const [mainVersion, setMainVersion] = useState<string>('');
  const [detailVersion, setDetailVersion] = useState<string>('');
  const [defaultMainVersion, setDefaultMainVersion] = useState<string>('');
  const [defaultDetailVersion, setDefaultDetailVersion] = useState<string>('');
  const barcodeRef = useRef(barcode);
  barcodeRef.current = barcode;
  const deletingRef = useRef(false);
  const mainLoadingMoreRef = useRef(false);
  const detailLoadingMoreRef = useRef(false);

  const loadBarcodeImages = useCallback(async (options?: { resetSelection?: boolean }) => {
    if (!barcode) return;
    setLoading(true);
    try {
      const [mainRes, detailRes, settings] = await Promise.all([
        imageApi.list({ barcode_exact: barcode, image_type: 'main', page: 1, page_size: IMAGE_PAGE_SIZE, sort: 'sequence', order: 'asc' }),
        imageApi.list({ barcode_exact: barcode, image_type: 'detail', page: 1, page_size: IMAGE_PAGE_SIZE, sort: 'sequence', order: 'asc' }),
        barcodeSettingApi.get(barcode),
      ]);
      if (barcodeRef.current !== barcode) return;
      // Merge and dedup by id
      const idSet = new Set<number>();
      const merged: ImageRec[] = [];
      for (const img of [...mainRes.items, ...detailRes.items]) {
        if (!idSet.has(img.id)) {
          idSet.add(img.id);
          merged.push(img);
        }
      }
      setImages(merged);
      setMainImageTotal(mainRes.total);
      setDetailImageTotal(detailRes.total);
      setMainImagePage(1);
      setDetailImagePage(1);
      setDefaultMainVersion(settings.default_main_ctime || '');
      setDefaultDetailVersion(settings.default_detail_ctime || '');
      if (merged.length > 0) {
        // GET /images/:id returns version history for the image's entire barcode, not just the single image
        const detail = await imageApi.get(merged[0].id);
        if (barcodeRef.current !== barcode) return;
        setVersions(detail.versions);
        const resolveVersion = (
          imageType: 'main' | 'detail',
          preferred: string,
        ) => {
          const candidates = detail.versions.filter((v) => v.image_type === imageType);
          const preferredExists = preferred && candidates.some((v) => v.folder_ctime === preferred);
          if (preferredExists) return preferred;
          return candidates.find((v) => v.is_latest)?.folder_ctime || '';
        };
        if (options?.resetSelection) {
          setMainVersion(resolveVersion('main', settings.default_main_ctime || ''));
          setDetailVersion(resolveVersion('detail', settings.default_detail_ctime || ''));
        } else {
          setMainVersion((prev) => resolveVersion('main', prev || settings.default_main_ctime || ''));
          setDetailVersion((prev) => resolveVersion('detail', prev || settings.default_detail_ctime || ''));
        }
      } else {
        setVersions([]);
        setMainVersion('');
        setDetailVersion('');
      }
    } catch {
      if (barcodeRef.current === barcode) {
        message.error('加载图片失败');
      }
    } finally {
      if (barcodeRef.current === barcode) {
        setLoading(false);
      }
    }
  }, [barcode]);

  useEffect(() => {
    if (!barcode) return;
    mainLoadingMoreRef.current = false;
    detailLoadingMoreRef.current = false;
    setMainLoadingMore(false);
    setDetailLoadingMore(false);
    setMainShowCount(THUMBNAIL_PAGE_SIZE);
    setDetailShowCount(THUMBNAIL_PAGE_SIZE);
    setCollapsedDupVersions(new Set());
    setMainImagePage(1);
    setMainImageTotal(0);
    setDetailImagePage(1);
    setDetailImageTotal(0);
    loadBarcodeImages({ resetSelection: true });
  }, [barcode, loadBarcodeImages]);

  const mainImages = useMemo(
    () =>
      images.filter(
        (i) =>
          i.image_type === 'main' &&
          (!mainVersion || i.folder_ctime === mainVersion),
      ),
    [images, mainVersion],
  );
  const detailImages = useMemo(
    () =>
      images.filter(
        (i) =>
          i.image_type === 'detail' &&
          (!detailVersion || i.folder_ctime === detailVersion),
      ),
    [images, detailVersion],
  );

  // Build version options for dropdowns (filtered by image_type)
  const mainVersionOptions = useMemo(
    () =>
      versions
        .filter((v) => v.image_type === 'main')
        .map((v) => ({
          value: v.folder_ctime,
          label: `${v.version_label}${v.is_latest ? ' (最新)' : ''}${defaultMainVersion && v.folder_ctime === defaultMainVersion ? ' (默认)' : ''}`,
        })),
    [versions, defaultMainVersion],
  );
  const detailVersionOptions = useMemo(
    () =>
      versions
        .filter((v) => v.image_type === 'detail')
        .map((v) => ({
          value: v.folder_ctime,
          label: `${v.version_label}${v.is_latest ? ' (最新)' : ''}${defaultDetailVersion && v.folder_ctime === defaultDetailVersion ? ' (默认)' : ''}`,
        })),
    [versions, defaultDetailVersion],
  );

  const handleVersionChange = useCallback(
    (type: 'main' | 'detail', ctime: string) => {
      if (type === 'main') {
        setMainVersion(ctime);
      } else {
        setDetailVersion(ctime);
      }
    },
    [],
  );

  const handleSetDefault = useCallback(
    async (type: 'main' | 'detail') => {
      if (!barcode) return;
      const ctime = type === 'main' ? mainVersion : detailVersion;
      if (!ctime) return;
      try {
        const data = type === 'main'
          ? { default_main_ctime: ctime }
          : { default_detail_ctime: ctime };
        await barcodeSettingApi.update(barcode, data);
        if (type === 'main') {
          setDefaultMainVersion(ctime);
        } else {
          setDefaultDetailVersion(ctime);
        }
        message.success(type === 'main' ? '已设为默认主图版本' : '已设为默认详情图版本');
      } catch {
        message.error('设置默认版本失败');
      }
    },
    [barcode, mainVersion, detailVersion],
  );

  const mainHasMoreServer = mainImagePage * IMAGE_PAGE_SIZE < mainImageTotal;
  const detailHasMoreServer = detailImagePage * IMAGE_PAGE_SIZE < detailImageTotal;

  const loadMoreMainImages = useCallback(async () => {
    if (!barcode || mainLoadingMoreRef.current || !mainHasMoreServer) return;
    mainLoadingMoreRef.current = true;
    setMainLoadingMore(true);
    try {
      const nextPage = mainImagePage + 1;
      const res = await imageApi.list({ barcode_exact: barcode, image_type: 'main', page: nextPage, page_size: IMAGE_PAGE_SIZE, sort: 'sequence', order: 'asc' });
      if (barcodeRef.current !== barcode) return;
      setImages((prev) => {
        const idSet = new Set(prev.map((i) => i.id));
        const newItems = res.items.filter((i) => !idSet.has(i.id));
        return [...prev, ...newItems];
      });
      setMainImagePage(nextPage);
      setMainImageTotal(res.total);
    } catch {
      if (barcodeRef.current === barcode) {
        message.error('加载更多主图失败');
      }
    } finally {
      mainLoadingMoreRef.current = false;
      if (barcodeRef.current === barcode) {
        setMainLoadingMore(false);
      }
    }
  }, [barcode, mainImagePage, mainHasMoreServer]);

  const loadMoreDetailImages = useCallback(async () => {
    if (!barcode || detailLoadingMoreRef.current || !detailHasMoreServer) return;
    detailLoadingMoreRef.current = true;
    setDetailLoadingMore(true);
    try {
      const nextPage = detailImagePage + 1;
      const res = await imageApi.list({ barcode_exact: barcode, image_type: 'detail', page: nextPage, page_size: IMAGE_PAGE_SIZE, sort: 'sequence', order: 'asc' });
      if (barcodeRef.current !== barcode) return;
      setImages((prev) => {
        const idSet = new Set(prev.map((i) => i.id));
        const newItems = res.items.filter((i) => !idSet.has(i.id));
        return [...prev, ...newItems];
      });
      setDetailImagePage(nextPage);
      setDetailImageTotal(res.total);
    } catch {
      if (barcodeRef.current === barcode) {
        message.error('加载更多详情图失败');
      }
    } finally {
      detailLoadingMoreRef.current = false;
      if (barcodeRef.current === barcode) {
        setDetailLoadingMore(false);
      }
    }
  }, [barcode, detailImagePage, detailHasMoreServer]);

  const toggleCheck = useCallback(
    (id: number, type: 'main' | 'detail') => {
      const selected =
        type === 'main' ? new Set(selectedMainIds) : new Set(selectedDetailIds);
      if (selected.has(id)) selected.delete(id);
      else selected.add(id);
      type === 'main'
        ? onMainSelectionChange(selected)
        : onDetailSelectionChange(selected);
    },
    [
      selectedMainIds,
      selectedDetailIds,
      onMainSelectionChange,
      onDetailSelectionChange,
    ],
  );

  const toggleAll = useCallback(
    (imgs: ImageRec[], type: 'main' | 'detail') => {
      // Merge with existing selection so cross-version picks are preserved
      const currentSet = type === 'main' ? selectedMainIds : selectedDetailIds;
      const next = new Set(currentSet);
      const allSelected = imgs.length > 0 && imgs.every((i) => currentSet.has(i.id));
      if (allSelected) {
        imgs.forEach((i) => next.delete(i.id));
      } else {
        imgs.forEach((i) => next.add(i.id));
      }
      type === 'main'
        ? onMainSelectionChange(next)
        : onDetailSelectionChange(next);
    },
    [
      selectedMainIds,
      selectedDetailIds,
      onMainSelectionChange,
      onDetailSelectionChange,
    ],
  );

  const handleDelete = async (deleteFile: boolean) => {
    if (!deleteTarget || deletingRef.current) return;
    deletingRef.current = true;
    try {
      await imageApi.delete(deleteTarget.id, deleteFile);
      message.success(deleteFile ? '已删除索引和文件' : '已删除索引');
      setDeleteTarget(null);
      setImages((prev) => prev.filter((i) => i.id !== deleteTarget.id));
      const newMain = new Set(selectedMainIds);
      newMain.delete(deleteTarget.id);
      const newDetail = new Set(selectedDetailIds);
      newDetail.delete(deleteTarget.id);
      onMainSelectionChange(newMain);
      onDetailSelectionChange(newDetail);
      onDeleted();
    } catch {
      message.error('删除失败，请重试');
    } finally {
      deletingRef.current = false;
    }
  };

  const reloadImages = useCallback(async () => {
    await loadBarcodeImages();
  }, [loadBarcodeImages]);

  // Delete task polling
  const deletePolling = useTaskPolling({
    onComplete: () => {
      reloadImages();
      onDeleted();
    },
    successMessage: (task) => `删除完成，共删除 ${task.result_count} 项`,
  });

  const handleVersionDelete = async (deleteFile: boolean) => {
    if (!versionDeleteTarget || deletingRef.current) return;
    deletingRef.current = true;
    const deletedCtime = versionDeleteTarget.folder_ctime;
    const deletedImageType = versionDeleteTarget.image_type;
    try {
      const task = await taskApi.createDeleteVersionTask(versionDeleteTarget.id, deleteFile);
      deletePolling.startPolling(task.id);
      setVersionDeleteTarget(null);
      // Reset selection if we deleted the currently selected version
      if (deletedImageType === 'main' && mainVersion === deletedCtime) {
        setMainVersion('');
      }
      if (deletedImageType === 'detail' && detailVersion === deletedCtime) {
        setDetailVersion('');
      }
      if (deletedImageType === 'main' && defaultMainVersion === deletedCtime) {
        setDefaultMainVersion('');
        if (barcode) {
          barcodeSettingApi.update(barcode, { default_main_ctime: '' }).catch(() => {});
        }
      }
      if (deletedImageType === 'detail' && defaultDetailVersion === deletedCtime) {
        setDefaultDetailVersion('');
        if (barcode) {
          barcodeSettingApi.update(barcode, { default_detail_ctime: '' }).catch(() => {});
        }
      }
    } catch {
      message.error('删除版本失败，请重试');
    } finally {
      deletingRef.current = false;
    }
  };

  const handleDuplicateDelete = async (deleteFile: boolean) => {
    if (!dupDeleteTarget || deletingRef.current) return;
    const { folderCtime: deletedCtime, imageType: deletedType } = dupDeleteTarget;
    deletingRef.current = true;
    try {
      await barcodeApi.deleteDuplicateImages(
        dupDeleteTarget.barcode,
        deletedCtime,
        deletedType,
        deleteFile,
      );
      message.success(deleteFile ? '已删除重复图片和文件' : '已删除重复图片索引');
      setDupDeleteTarget(null);
      // Reset version selection if we deleted the currently selected version
      if (deletedType === 'main' && mainVersion === deletedCtime) {
        setMainVersion('');
      }
      if (deletedType === 'detail' && detailVersion === deletedCtime) {
        setDetailVersion('');
      }
      if (deletedType === 'main' && defaultMainVersion === deletedCtime) {
        setDefaultMainVersion('');
        if (barcode) {
          barcodeSettingApi.update(barcode, { default_main_ctime: '' }).catch(() => {});
        }
      }
      if (deletedType === 'detail' && defaultDetailVersion === deletedCtime) {
        setDefaultDetailVersion('');
        if (barcode) {
          barcodeSettingApi.update(barcode, { default_detail_ctime: '' }).catch(() => {});
        }
      }
      await reloadImages();
      onDeleted();
    } catch {
      message.error('删除重复图片失败，请重试');
    } finally {
      deletingRef.current = false;
    }
  };

  /* ---------- 缩略图卡片 ---------- */
  const renderImage = (img: ImageRec, index: number) => {
    const selected = selectedMainIds.has(img.id) || selectedDetailIds.has(img.id);
    return (
      <div
        key={img.id}
        className={`thumb${selected ? ' selected' : ''}`}
        style={{ animationDelay: `${Math.min(index, 24) * 18}ms` }}
      >
        <Image
          src={imageApi.thumbnailUrl(img.id)}
          style={{ objectFit: 'cover' }}
          fallback={PLACEHOLDER_SVG}
          preview={{
            src: imageApi.fileUrl(img.id),
            mask: (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                <EyeOutlined /> 预览
              </span>
            ),
          }}
        />
        <Checkbox
          className={`thumb-check${selected ? ' always' : ''}`}
          checked={selected}
          onChange={() => toggleCheck(img.id, img.image_type as 'main' | 'detail')}
          onClick={(e) => e.stopPropagation()}
        />
        <div className="thumb-tools">
          <Popover
            title={
              <span className="mono" style={{ fontSize: 12 }}>{img.filename}</span>
            }
            content={
              <div style={{ maxWidth: 340, fontSize: 12 }}>
                <div style={{ marginBottom: 6 }}>
                  <span style={{ color: 'var(--t3)' }}>路径：</span>
                  <span className="mono" style={{ wordBreak: 'break-all', userSelect: 'text' }}>{img.file_path}</span>
                </div>
                <div style={{ marginBottom: 6 }}>
                  <span style={{ color: 'var(--t3)' }}>文件夹：</span>
                  <span className="mono" style={{ wordBreak: 'break-all' }}>{img.folder_path}</span>
                </div>
                <div>
                  <span style={{ color: 'var(--t3)' }}>版本时间：</span>
                  <span className="mono">{img.folder_ctime?.replace('T', ' ').slice(0, 19)}</span>
                </div>
              </div>
            }
            trigger="click"
            placement="left"
          >
            <button
              className="thumb-tool-btn"
              onClick={(e) => e.stopPropagation()}
              title="详情"
            >
              <InfoCircleOutlined />
            </button>
          </Popover>
          <button
            className="thumb-tool-btn danger"
            onClick={(e) => { e.stopPropagation(); setDeleteTarget(img); }}
            title="删除"
          >
            <DeleteOutlined />
          </button>
        </div>
      </div>
    );
  };

  if (!barcode) {
    return (
      <div className="panel" style={{ flex: 1, display: 'flex' }}>
        <div className="detail-empty" style={{ margin: 'auto' }}>
          <div className="detail-empty-glyph">
            <PictureOutlined />
          </div>
          <span>点击左侧表格中的条码</span>
          <span className="hint">查看主图、详情图与历史版本</span>
        </div>
      </div>
    );
  }

  /* ---------- 版本胶片条 ---------- */
  const renderFilmstrip = () => {
    if (versions.length === 0) return null;
    return (
      <div className="filmstrip-wrap">
        <div className="hint" style={{ marginBottom: 6 }}>版本历史 · {versions.length}</div>
        <div className="filmstrip">
          {versions.map((v) => {
            const isDefault =
              (v.image_type === 'main' && defaultMainVersion && v.folder_ctime === defaultMainVersion) ||
              (v.image_type === 'detail' && defaultDetailVersion && v.folder_ctime === defaultDetailVersion);
            const isActive =
              (v.image_type === 'main' && mainVersion === v.folder_ctime) ||
              (v.image_type === 'detail' && detailVersion === v.folder_ctime);
            return (
              <span
                key={v.id}
                className={`film-frame${isActive ? ' active' : ''}`}
                onClick={() => {
                  if (v.image_type === 'main') setMainVersion(v.folder_ctime);
                  else setDetailVersion(v.folder_ctime);
                }}
              >
                <span className="ff-type">{v.image_type === 'main' ? '主' : '详'}</span>
                {v.version_label}
                {v.is_latest && <span className="ver-tag latest">最新</span>}
                {isDefault && <span className="ver-tag default">默认</span>}
                <button
                  className="ff-x"
                  title="删除此版本"
                  onClick={(e) => { e.stopPropagation(); setVersionDeleteTarget(v); }}
                >
                  ✕
                </button>
              </span>
            );
          })}
        </div>
        {versions.map((v) => {
          if (!v.duplicate_mtimes || v.duplicate_mtimes.length === 0) return null;
          const all = v.duplicate_mtimes;
          const showAll = collapsedDupVersions.has(v.id);
          const overflow = all.length > DUP_MTIME_COLLAPSE;
          const visible = overflow && !showAll ? all.slice(0, DUP_MTIME_COLLAPSE) : all;
          return (
            <div key={`dup-${v.id}`} style={{ marginTop: 6 }}>
              <span className="hint">
                {v.image_type === 'main' ? '主图' : '详情图'} {v.version_label} · 重复文件夹 ({all.length})
              </span>
              <div style={{ marginTop: 2 }}>
                {visible.map((mtime) => (
                  <span key={mtime} className="dup-chip">
                    {mtime.replace('T', ' ').slice(0, 19)}
                    <button
                      className="ff-x"
                      title="删除此重复文件夹"
                      onClick={() =>
                        setDupDeleteTarget({
                          barcode: barcode!,
                          folderCtime: mtime,
                          imageType: v.image_type,
                        })
                      }
                    >
                      ✕
                    </button>
                  </span>
                ))}
                {overflow && (
                  <Button
                    type="link"
                    size="small"
                    style={{ fontSize: 11, padding: 0 }}
                    onClick={() => {
                      const next = new Set(collapsedDupVersions);
                      showAll ? next.delete(v.id) : next.add(v.id);
                      setCollapsedDupVersions(next);
                    }}
                  >
                    {showAll ? '收起' : `展开全部 (${all.length - DUP_MTIME_COLLAPSE})`}
                  </Button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  /* ---------- 主图 / 详情图分区 ---------- */
  const renderZone = (type: 'main' | 'detail') => {
    const isMain = type === 'main';
    const zoneImages = isMain ? mainImages : detailImages;
    const version = isMain ? mainVersion : detailVersion;
    const defaultVersion = isMain ? defaultMainVersion : defaultDetailVersion;
    const options = isMain ? mainVersionOptions : detailVersionOptions;
    const showCount = isMain ? mainShowCount : detailShowCount;
    const hasMoreServer = isMain ? mainHasMoreServer : detailHasMoreServer;
    const loadingMore = isMain ? mainLoadingMore : detailLoadingMore;
    const total = isMain ? mainImageTotal : detailImageTotal;
    const title = isMain ? '主图' : '详情图';

    return (
      <section style={{ marginBottom: 18 }}>
        <div className="zone-head">
          <span className="zone-title">
            <Led color={isMain ? 'blue' : 'green'} />
            {title}
            <span className="count-chip" style={{ background: 'var(--ink-4)' }}>
              {zoneImages.length}
            </span>
          </span>
          <Select
            size="small"
            value={version || undefined}
            onChange={(v) => handleVersionChange(type, v)}
            options={options}
            placeholder={`${title}版本`}
            style={{ width: 150 }}
            allowClear
            onClear={() => handleVersionChange(type, '')}
          />
          <Button size="small" onClick={() => toggleAll(zoneImages, type)}>
            全选{title}
          </Button>
          <Button
            size="small"
            disabled={!version || version === defaultVersion}
            onClick={() => handleSetDefault(type)}
          >
            {version && version === defaultVersion ? '已是默认' : '设为默认'}
          </Button>
          <Button
            size="small"
            danger
            type="text"
            icon={<DeleteOutlined />}
            title={`删除当前${title}版本`}
            disabled={!version}
            onClick={() => {
              const v = versions.find(
                (ver) => ver.folder_ctime === version && ver.image_type === type,
              );
              if (v) setVersionDeleteTarget(v);
            }}
          />
        </div>
        <Image.PreviewGroup>
          <div className="thumb-grid">
            {zoneImages.slice(0, showCount).map(renderImage)}
          </div>
        </Image.PreviewGroup>
        {zoneImages.length === 0 && !loading && (
          <div className="hint" style={{ padding: '10px 0' }}>此版本下暂无{title}</div>
        )}
        {(zoneImages.length > showCount || hasMoreServer) && (
          <Button
            type="link"
            size="small"
            loading={loadingMore}
            onClick={() => {
              if (hasMoreServer) {
                isMain ? loadMoreMainImages() : loadMoreDetailImages();
              } else {
                isMain
                  ? setMainShowCount((prev) => prev + THUMBNAIL_PAGE_SIZE)
                  : setDetailShowCount((prev) => prev + THUMBNAIL_PAGE_SIZE);
              }
            }}
            style={{ padding: 0, marginTop: 8 }}
          >
            {hasMoreServer
              ? `加载更多 (已加载 ${images.filter(i => i.image_type === type).length}/${total})`
              : `加载更多 (${showCount}/${zoneImages.length})`}
          </Button>
        )}
      </section>
    );
  };

  return (
    <div className="panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div className="panel-head">
        <span className="hint">BARCODE</span>
        <span className="mono" style={{ fontSize: 15, fontWeight: 700 }}>{barcode}</span>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
          <span className="count-chip main">主 {mainImages.length}</span>
          <span className="count-chip detail">详 {detailImages.length}</span>
        </span>
      </div>
      <Spin spinning={loading} style={{ flex: 1, minHeight: 0 }}>
        <div style={{ padding: '12px 16px', overflowY: 'auto', maxHeight: 'calc(100vh - 232px)' }}>
          {deletePolling.polling && deletePolling.currentTask && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginBottom: 6 }}>
                <Led color="red" />
                正在删除…
              </div>
              <LightBar
                value={
                  deletePolling.currentTask.total > 0
                    ? deletePolling.currentTask.progress / deletePolling.currentTask.total
                    : undefined
                }
              />
              <div className="activity-meta">
                {deletePolling.currentTask.progress}/{deletePolling.currentTask.total}
              </div>
            </div>
          )}
          {renderFilmstrip()}
          {renderZone('main')}
          {renderZone('detail')}
        </div>
      </Spin>

      <Modal
        title="删除图片"
        open={!!deleteTarget}
        onCancel={() => setDeleteTarget(null)}
        footer={null}
        width={360}
      >
        <p>请选择删除方式：</p>
        <div className="modal-actions">
          <Button onClick={() => handleDelete(false)}>删除索引</Button>
          <Button danger onClick={() => handleDelete(true)}>删除索引和文件</Button>
        </div>
        <div style={{ marginTop: 12, fontSize: 12 }}>
          {deleteTarget && <span className="mono" style={{ color: 'var(--t3)' }}>文件: {deleteTarget.filename}</span>}
        </div>
      </Modal>

      <Modal
        title="删除版本"
        open={!!versionDeleteTarget}
        onCancel={() => setVersionDeleteTarget(null)}
        footer={null}
        width={360}
      >
        <p>
          将删除{versionDeleteTarget?.image_type === 'main' ? '主图' : '详情图'}版本{' '}
          <strong className="mono">{versionDeleteTarget?.version_label}</strong>{' '}
          下的所有图片：
        </p>
        <div className="modal-actions">
          <Button onClick={() => handleVersionDelete(false)}>删除索引</Button>
          <Button danger onClick={() => handleVersionDelete(true)}>删除索引和文件</Button>
        </div>
      </Modal>

      <Modal
        title="删除重复图片"
        open={!!dupDeleteTarget}
        onCancel={() => setDupDeleteTarget(null)}
        footer={null}
        width={360}
      >
        <p>
          将删除文件夹{' '}
          <strong className="mono">{dupDeleteTarget?.folderCtime?.replace('T', ' ').slice(0, 19)}</strong>{' '}
          下的重复图片（{dupDeleteTarget?.imageType === 'main' ? '主图' : '详情图'}）：
        </p>
        <div className="modal-actions">
          <Button onClick={() => handleDuplicateDelete(false)}>删除索引</Button>
          <Button danger onClick={() => handleDuplicateDelete(true)}>删除索引和文件</Button>
        </div>
      </Modal>
    </div>
  );
};

export default React.memo(DetailPanel);
