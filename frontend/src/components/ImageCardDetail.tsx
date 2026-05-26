import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Card,
  Checkbox,
  Collapse,
  Tag,
  Image,
  Spin,
  Empty,
  Typography,
  Space,
  Button,
  Modal,
  Select,
  message,
  Popover,
} from "antd";
import { DeleteOutlined, EyeOutlined, InfoCircleOutlined } from "@ant-design/icons";
import {
  ImageRec,
  ImageVersion,
  imageApi,
  versionApi,
  barcodeApi,
  barcodeSettingApi,
} from "../services/api";

const { Text } = Typography;

const PLACEHOLDER_SVG =
  "data:image/svg+xml," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">' +
      '<rect fill="#f5f5f5" width="100" height="100" rx="4"/>' +
      '<text x="50" y="55" text-anchor="middle" fill="#bfbfbf" font-size="36" font-family="sans-serif">?</text>' +
      "</svg>",
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

const ImageCardDetail: React.FC<Props> = ({
  barcode,
  selectedMainIds,
  selectedDetailIds,
  onMainSelectionChange,
  onDetailSelectionChange,
  onDeleted,
}) => {
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [images, setImages] = useState<ImageRec[]>([]);
  const [versions, setVersions] = useState<ImageVersion[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<ImageRec | null>(null);
  const [versionDeleteTarget, setVersionDeleteTarget] =
    useState<ImageVersion | null>(null);
  const [dupDeleteTarget, setDupDeleteTarget] = useState<{
    barcode: string; folderCtime: string; imageType: string;
  } | null>(null);
  const [mainShowCount, setMainShowCount] = useState(THUMBNAIL_PAGE_SIZE);
  const [detailShowCount, setDetailShowCount] = useState(THUMBNAIL_PAGE_SIZE);
  const [collapsedDupVersions, setCollapsedDupVersions] = useState<Set<number>>(new Set());

  // Server-side pagination
  const [imagePage, setImagePage] = useState(1);
  const [imageTotal, setImageTotal] = useState(0);

  // Per-type version selection (stored as folder_ctime)
  const [mainVersion, setMainVersion] = useState<string>("");
  const [detailVersion, setDetailVersion] = useState<string>("");
  const [defaultMainVersion, setDefaultMainVersion] = useState<string>("");
  const [defaultDetailVersion, setDefaultDetailVersion] = useState<string>("");
  const barcodeRef = useRef(barcode);
  barcodeRef.current = barcode;
  const deletingRef = useRef(false);

  useEffect(() => {
    if (!barcode) return;
    let cancelled = false;
    setLoading(true);
    setMainShowCount(THUMBNAIL_PAGE_SIZE);
    setDetailShowCount(THUMBNAIL_PAGE_SIZE);
    setCollapsedDupVersions(new Set());
    setImagePage(1);
    setImageTotal(0);
    Promise.all([
      imageApi.list({ barcode, page: 1, page_size: IMAGE_PAGE_SIZE }),
      barcodeSettingApi.get(barcode),
    ])
      .then(([imgRes, settings]) => {
        if (cancelled) return;
        setImages(imgRes.items);
        setImageTotal(imgRes.total);
        setDefaultMainVersion(settings.default_main_ctime || "");
        setDefaultDetailVersion(settings.default_detail_ctime || "");
        if (imgRes.items.length > 0) {
          return imageApi.get(imgRes.items[0].id).then((detail) => {
            if (cancelled) return;
            setVersions(detail.versions);
            const latestMain = detail.versions.find((v) => v.is_latest && v.image_type === "main");
            const latestDetail = detail.versions.find((v) => v.is_latest && v.image_type === "detail");
            const newMainVersion = settings.default_main_ctime || latestMain?.folder_ctime || "";
            const newDetailVersion = settings.default_detail_ctime || latestDetail?.folder_ctime || "";
            setMainVersion(newMainVersion);
            setDetailVersion(newDetailVersion);
          });
        } else {
          setVersions([]);
          setMainVersion("");
          setDetailVersion("");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [barcode]);

  const mainImages = useMemo(
    () =>
      images.filter(
        (i) =>
          i.image_type === "main" &&
          (!mainVersion || i.folder_ctime === mainVersion),
      ),
    [images, mainVersion],
  );
  const detailImages = useMemo(
    () =>
      images.filter(
        (i) =>
          i.image_type === "detail" &&
          (!detailVersion || i.folder_ctime === detailVersion),
      ),
    [images, detailVersion],
  );

  // Build version options for dropdowns (filtered by image_type)
  const mainVersionOptions = useMemo(
    () =>
      versions
        .filter((v) => v.image_type === "main")
        .map((v) => ({
          value: v.folder_ctime,
          label: `${v.version_label}${v.is_latest ? " (最新)" : ""}${defaultMainVersion && v.folder_ctime === defaultMainVersion ? " (默认)" : ""}`,
        })),
    [versions, defaultMainVersion],
  );
  const detailVersionOptions = useMemo(
    () =>
      versions
        .filter((v) => v.image_type === "detail")
        .map((v) => ({
          value: v.folder_ctime,
          label: `${v.version_label}${v.is_latest ? " (最新)" : ""}${defaultDetailVersion && v.folder_ctime === defaultDetailVersion ? " (默认)" : ""}`,
        })),
    [versions, defaultDetailVersion],
  );

  const handleVersionChange = useCallback(
    (type: "main" | "detail", ctime: string) => {
      if (type === "main") {
        setMainVersion(ctime);
      } else {
        setDetailVersion(ctime);
      }
    },
    [],
  );

  const handleSetDefault = useCallback(
    async (type: "main" | "detail") => {
      if (!barcode) return;
      const ctime = type === "main" ? mainVersion : detailVersion;
      if (!ctime) return;
      try {
        const data = type === "main"
          ? { default_main_ctime: ctime }
          : { default_detail_ctime: ctime };
        await barcodeSettingApi.update(barcode, data);
        if (type === "main") {
          setDefaultMainVersion(ctime);
        } else {
          setDefaultDetailVersion(ctime);
        }
        message.success(type === "main" ? "已设为默认主图版本" : "已设为默认详情图版本");
      } catch {
        message.error("设置默认版本失败");
      }
    },
    [barcode, mainVersion, detailVersion],
  );

  const hasMoreServer = imagePage * IMAGE_PAGE_SIZE < imageTotal;

  const loadMoreImages = useCallback(async () => {
    if (!barcode || loadingMore || !hasMoreServer) return;
    setLoadingMore(true);
    try {
      const nextPage = imagePage + 1;
      const res = await imageApi.list({ barcode, page: nextPage, page_size: IMAGE_PAGE_SIZE });
      if (barcodeRef.current !== barcode) return;
      setImages((prev) => [...prev, ...res.items]);
      setImagePage(nextPage);
      setImageTotal(res.total);
    } catch {
      message.error("加载更多图片失败");
    } finally {
      setLoadingMore(false);
    }
  }, [barcode, imagePage, hasMoreServer, loadingMore]);

  const toggleCheck = useCallback(
    (id: number, type: "main" | "detail") => {
      const selected =
        type === "main" ? new Set(selectedMainIds) : new Set(selectedDetailIds);
      if (selected.has(id)) selected.delete(id);
      else selected.add(id);
      type === "main"
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
    (imgs: ImageRec[], type: "main" | "detail") => {
      const currentSet = type === "main" ? selectedMainIds : selectedDetailIds;
      const allIds = new Set(imgs.map((i) => i.id));
      const allSelected = imgs.every((i) => currentSet.has(i.id));
      type === "main"
        ? onMainSelectionChange(allSelected ? new Set() : allIds)
        : onDetailSelectionChange(allSelected ? new Set() : allIds);
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
      message.success(deleteFile ? "已删除索引和文件" : "已删除索引");
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
      message.error("删除失败，请重试");
    } finally {
      deletingRef.current = false;
    }
  };

  const reloadImages = useCallback(async () => {
    if (!barcode) return;
    setLoading(true);
    try {
      const [imgRes, settings] = await Promise.all([
        imageApi.list({ barcode, page: 1, page_size: IMAGE_PAGE_SIZE }),
        barcodeSettingApi.get(barcode),
      ]);
      if (barcodeRef.current !== barcode) return;
      setImages(imgRes.items);
      setImageTotal(imgRes.total);
      setImagePage(1);
      setDefaultMainVersion(settings.default_main_ctime || "");
      setDefaultDetailVersion(settings.default_detail_ctime || "");
      if (imgRes.items.length > 0) {
        const detail = await imageApi.get(imgRes.items[0].id);
        if (barcodeRef.current !== barcode) return;
        setVersions(detail.versions);
        const latestMain = detail.versions.find((v) => v.is_latest && v.image_type === "main");
        const latestDetail = detail.versions.find((v) => v.is_latest && v.image_type === "detail");
        setMainVersion((prev) =>
          prev === "" ? (settings.default_main_ctime || latestMain?.folder_ctime || "") : prev,
        );
        setDetailVersion((prev) =>
          prev === "" ? (settings.default_detail_ctime || latestDetail?.folder_ctime || "") : prev,
        );
      } else {
        setVersions([]);
        setMainVersion("");
        setDetailVersion("");
      }
    } catch {
      message.error("刷新数据失败");
    } finally {
      setLoading(false);
    }
  }, [barcode]);

  const handleVersionDelete = async (deleteFile: boolean) => {
    if (!versionDeleteTarget || deletingRef.current) return;
    deletingRef.current = true;
    const deletedCtime = versionDeleteTarget.folder_ctime;
    const deletedImageType = versionDeleteTarget.image_type;
    try {
      await versionApi.delete(versionDeleteTarget.id, deleteFile);
      message.success(deleteFile ? "已删除版本索引和文件" : "已删除版本索引");
      setVersionDeleteTarget(null);
      // Reset selection if we deleted the currently selected version
      if (deletedImageType === "main" && mainVersion === deletedCtime) {
        setMainVersion("");
      }
      if (deletedImageType === "detail" && detailVersion === deletedCtime) {
        setDetailVersion("");
      }
      if (deletedImageType === "main" && defaultMainVersion === deletedCtime) {
        setDefaultMainVersion("");
      }
      if (deletedImageType === "detail" && defaultDetailVersion === deletedCtime) {
        setDefaultDetailVersion("");
      }
      await reloadImages();
      onDeleted();
    } catch {
      message.error("删除版本失败，请重试");
    } finally {
      deletingRef.current = false;
    }
  };

  const handleDuplicateDelete = async (deleteFile: boolean) => {
    if (!dupDeleteTarget || deletingRef.current) return;
    deletingRef.current = true;
    try {
      await barcodeApi.deleteDuplicateImages(
        dupDeleteTarget.barcode,
        dupDeleteTarget.folderCtime,
        dupDeleteTarget.imageType,
        deleteFile,
      );
      message.success(deleteFile ? "已删除重复图片和文件" : "已删除重复图片索引");
      setDupDeleteTarget(null);
      await reloadImages();
      onDeleted();
    } catch {
      message.error("删除重复图片失败，请重试");
    } finally {
      deletingRef.current = false;
    }
  };

  const renderImage = (img: ImageRec) => (
    <div key={img.id} style={{ position: "relative", display: "inline-block" }}>
      <Image
        src={imageApi.thumbnailUrl(img.id)}
        width={100}
        height={100}
        style={{ objectFit: "cover", borderRadius: 4 }}
        fallback={PLACEHOLDER_SVG}
        preview={{
          src: imageApi.fileUrl(img.id),
          mask: (
            <Space>
              <EyeOutlined />
              <Text style={{ color: "#fff" }}>预览</Text>
            </Space>
          ),
        }}
      />
      <Checkbox
        checked={selectedMainIds.has(img.id) || selectedDetailIds.has(img.id)}
        onChange={() =>
          toggleCheck(img.id, img.image_type as "main" | "detail")
        }
        style={{ position: "absolute", top: 2, left: 2 }}
      />
      <Button
        size="small"
        danger
        icon={<DeleteOutlined />}
        style={{ position: "absolute", top: 2, right: 2, opacity: 0.85 }}
        onClick={(e) => {
          e.stopPropagation();
          setDeleteTarget(img);
        }}
      />
      <Popover
        title={
          <Text ellipsis style={{ maxWidth: 260 }}>
            {img.filename}
          </Text>
        }
        content={
          <div style={{ maxWidth: 320, fontSize: 12 }}>
            <div style={{ marginBottom: 4 }}>
              <Text type="secondary">路径：</Text>
              <Text copyable style={{ wordBreak: "break-all" }}>{img.file_path}</Text>
            </div>
            <div style={{ marginBottom: 4 }}>
              <Text type="secondary">文件夹：</Text>
              <Text style={{ wordBreak: "break-all" }}>{img.folder_path}</Text>
            </div>
            <div>
              <Text type="secondary">版本时间：</Text>
              <Text>{img.folder_ctime?.replace("T", " ").slice(0, 19)}</Text>
            </div>
          </div>
        }
        trigger="click"
        placement="left"
      >
        <Button
          size="small"
          icon={<InfoCircleOutlined />}
          style={{ position: "absolute", bottom: 2, right: 2, opacity: 0.85 }}
          onClick={(e) => e.stopPropagation()}
        />
      </Popover>
    </div>
  );

  if (!barcode) return <Empty description="点击表格行查看图片详情" />;

  return (
    <Spin spinning={loading}>
      <Card size="small" title={<Text strong>条码: {barcode}</Text>}>
        {versions.length > 0 && (
          <Collapse
            size="small"
            style={{ marginBottom: 12 }}
            items={[
              {
                key: "versions",
                label: `版本历史 (${versions.length})`,
                children: versions.map((v) => (
                  <div key={v.id} style={{ marginBottom: 6 }}>
                    <Tag
                      color={v.is_latest ? "blue" : "default"}
                      style={{ cursor: "pointer" }}
                      onClick={() => {
                        if (v.image_type === "main") {
                          setMainVersion(v.folder_ctime);
                        } else {
                          setDetailVersion(v.folder_ctime);
                        }
                      }}
                      closable
                      onClose={(e) => {
                        e.preventDefault();
                        setVersionDeleteTarget(v);
                      }}
                    >
                      {v.image_type === "main" ? "主" : "详"} {v.version_label}{" "}
                      {v.is_latest ? "(最新)" : ""}
                      {(v.image_type === "main" && defaultMainVersion && v.folder_ctime === defaultMainVersion)
                        || (v.image_type === "detail" && defaultDetailVersion && v.folder_ctime === defaultDetailVersion)
                        ? " (默认)" : ""}
                    </Tag>
                    {v.duplicate_mtimes && v.duplicate_mtimes.length > 0 && (() => {
                      const all = v.duplicate_mtimes!;
                      const showAll = collapsedDupVersions.has(v.id);
                      const overflow = all.length > 20;
                      const visible = overflow && !showAll ? all.slice(0, 20) : all;
                      return (
                        <div style={{ marginLeft: 8, marginTop: 2 }}>
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            重复文件夹 ({all.length}):
                          </Text>
                          {visible.map((mtime) => (
                            <Tag
                              key={mtime}
                              color="orange"
                              style={{ fontSize: 11, marginLeft: 4, cursor: "pointer" }}
                              closable
                              onClose={(e) => {
                                e.preventDefault();
                                setDupDeleteTarget({
                                  barcode: barcode!,
                                  folderCtime: mtime,
                                  imageType: v.image_type,
                                });
                              }}
                            >
                              {mtime.replace("T", " ").slice(0, 19)}
                            </Tag>
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
                              {showAll ? "收起" : `展开全部 (${all.length - 20})`}
                            </Button>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                )),
              },
            ]}
          />
        )}

        <div style={{ marginBottom: 12 }}>
          <Space style={{ marginBottom: 8 }}>
            <Text strong>主图 ({mainImages.length})</Text>
            <Select
              size="small"
              value={mainVersion || undefined}
              onChange={(v) => handleVersionChange("main", v)}
              options={mainVersionOptions}
              placeholder="主图版本"
              style={{ width: 140 }}
              allowClear
              onClear={() => handleVersionChange("main", "")}
            />
            <Button size="small" onClick={() => toggleAll(mainImages, "main")}>
              全选主图
            </Button>
            <Button
              size="small"
              disabled={!mainVersion || mainVersion === defaultMainVersion}
              onClick={() => handleSetDefault("main")}
            >
              {mainVersion && mainVersion === defaultMainVersion ? "已是默认" : "设为默认"}
            </Button>
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              disabled={!mainVersion}
              onClick={() => {
                const v = versions.find(
                  (ver) => ver.folder_ctime === mainVersion && ver.image_type === "main",
                );
                if (v) setVersionDeleteTarget(v);
              }}
            />
          </Space>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {mainImages.slice(0, mainShowCount).map(renderImage)}
          </div>
          {(mainImages.length > mainShowCount || hasMoreServer) && (
            <Button
              type="link"
              size="small"
              loading={loadingMore}
              onClick={() => {
                if (hasMoreServer) {
                  loadMoreImages();
                } else {
                  setMainShowCount((prev) => prev + THUMBNAIL_PAGE_SIZE);
                }
              }}
              style={{ padding: 0, marginTop: 4 }}
            >
              {hasMoreServer
                ? `加载更多 (已加载 ${images.length}/${imageTotal})`
                : `加载更多 (${mainShowCount}/${mainImages.length})`}
            </Button>
          )}
        </div>

        <div>
          <Space style={{ marginBottom: 8 }}>
            <Text strong>详情图 ({detailImages.length})</Text>
            <Select
              size="small"
              value={detailVersion || undefined}
              onChange={(v) => handleVersionChange("detail", v)}
              options={detailVersionOptions}
              placeholder="详情图版本"
              style={{ width: 140 }}
              allowClear
              onClear={() => handleVersionChange("detail", "")}
            />
            <Button
              size="small"
              onClick={() => toggleAll(detailImages, "detail")}
            >
              全选详情图
            </Button>
            <Button
              size="small"
              disabled={!detailVersion || detailVersion === defaultDetailVersion}
              onClick={() => handleSetDefault("detail")}
            >
              {detailVersion && detailVersion === defaultDetailVersion ? "已是默认" : "设为默认"}
            </Button>
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              disabled={!detailVersion}
              onClick={() => {
                const v = versions.find(
                  (ver) => ver.folder_ctime === detailVersion && ver.image_type === "detail",
                );
                if (v) setVersionDeleteTarget(v);
              }}
            />
          </Space>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {detailImages.slice(0, detailShowCount).map(renderImage)}
          </div>
          {(detailImages.length > detailShowCount || hasMoreServer) && (
            <Button
              type="link"
              size="small"
              loading={loadingMore}
              onClick={() => {
                if (hasMoreServer) {
                  loadMoreImages();
                } else {
                  setDetailShowCount((prev) => prev + THUMBNAIL_PAGE_SIZE);
                }
              }}
              style={{ padding: 0, marginTop: 4 }}
            >
              {hasMoreServer
                ? `加载更多 (已加载 ${images.length}/${imageTotal})`
                : `加载更多 (${detailShowCount}/${detailImages.length})`}
            </Button>
          )}
        </div>
      </Card>

      <Modal
        title="删除图片"
        open={!!deleteTarget}
        onCancel={() => setDeleteTarget(null)}
        footer={null}
        width={360}
      >
        <p>请选择删除方式：</p>
        <Space style={{ marginTop: 12 }}>
          <Button onClick={() => handleDelete(false)}>删除索引</Button>
          <Button danger onClick={() => handleDelete(true)}>
            删除索引和文件
          </Button>
        </Space>
        <div style={{ marginTop: 12, color: "#999", fontSize: 12 }}>
          {deleteTarget && (
            <Text type="secondary">文件: {deleteTarget.filename}</Text>
          )}
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
          将删除{versionDeleteTarget?.image_type === "main" ? "主图" : "详情图"}版本{" "}
          <Text strong>{versionDeleteTarget?.version_label}</Text>{" "}
          下的所有图片：
        </p>
        <Space style={{ marginTop: 12 }}>
          <Button onClick={() => handleVersionDelete(false)}>删除索引</Button>
          <Button danger onClick={() => handleVersionDelete(true)}>
            删除索引和文件
          </Button>
        </Space>
      </Modal>

      <Modal
        title="删除重复图片"
        open={!!dupDeleteTarget}
        onCancel={() => setDupDeleteTarget(null)}
        footer={null}
        width={360}
      >
        <p>
          将删除文件夹{" "}
          <Text strong>
            {dupDeleteTarget?.folderCtime?.replace("T", " ").slice(0, 19)}
          </Text>{" "}
          下的重复图片（{dupDeleteTarget?.imageType === "main" ? "主图" : "详情图"}）：
        </p>
        <Space style={{ marginTop: 12 }}>
          <Button onClick={() => handleDuplicateDelete(false)}>删除索引</Button>
          <Button danger onClick={() => handleDuplicateDelete(true)}>
            删除索引和文件
          </Button>
        </Space>
      </Modal>
    </Spin>
  );
};

export default React.memo(ImageCardDetail);
