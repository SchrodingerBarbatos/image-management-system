import React, { useState, useEffect, useCallback, useMemo } from "react";
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
  const [images, setImages] = useState<ImageRec[]>([]);
  const [versions, setVersions] = useState<ImageVersion[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<ImageRec | null>(null);
  const [versionDeleteTarget, setVersionDeleteTarget] =
    useState<ImageVersion | null>(null);

  // Per-type version selection (stored as folder_mtime)
  const [mainVersion, setMainVersion] = useState<string>("");
  const [detailVersion, setDetailVersion] = useState<string>("");

  useEffect(() => {
    if (!barcode) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      imageApi.list({ barcode, page_size: 500 }),
      barcodeSettingApi.get(barcode),
    ])
      .then(([imgRes, settings]) => {
        if (cancelled) return;
        setImages(imgRes.items);
        if (imgRes.items.length > 0) {
          imageApi.get(imgRes.items[0].id).then((detail) => {
            if (cancelled) return;
            setVersions(detail.versions);
            const latest = detail.versions.find((v) => v.is_latest);
            const latestMtime = latest?.folder_mtime || "";
            // Use saved setting or fallback to latest
            setMainVersion(settings.default_main_mtime || latestMtime);
            setDetailVersion(settings.default_detail_mtime || latestMtime);
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
          (!mainVersion || i.folder_mtime === mainVersion),
      ),
    [images, mainVersion],
  );
  const detailImages = useMemo(
    () =>
      images.filter(
        (i) =>
          i.image_type === "detail" &&
          (!detailVersion || i.folder_mtime === detailVersion),
      ),
    [images, detailVersion],
  );

  // Build version options for dropdowns (filtered by image_type)
  const mainVersionOptions = useMemo(
    () =>
      versions
        .filter((v) => v.image_type === "main")
        .map((v) => ({
          value: v.folder_mtime,
          label: `${v.version_label}${v.is_latest ? " (最新)" : ""}`,
        })),
    [versions],
  );
  const detailVersionOptions = useMemo(
    () =>
      versions
        .filter((v) => v.image_type === "detail")
        .map((v) => ({
          value: v.folder_mtime,
          label: `${v.version_label}${v.is_latest ? " (最新)" : ""}`,
        })),
    [versions],
  );

  const handleVersionChange = useCallback(
    (type: "main" | "detail", mtime: string) => {
      if (type === "main") {
        setMainVersion(mtime);
        barcodeSettingApi.update(barcode!, { default_main_mtime: mtime });
      } else {
        setDetailVersion(mtime);
        barcodeSettingApi.update(barcode!, { default_detail_mtime: mtime });
      }
    },
    [barcode],
  );

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
    if (!deleteTarget) return;
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
  };

  const handleVersionDelete = async (deleteFile: boolean) => {
    if (!versionDeleteTarget) return;
    const deletedMtime = versionDeleteTarget.folder_mtime;
    await versionApi.delete(versionDeleteTarget.id, deleteFile);
    message.success(deleteFile ? "已删除版本索引和文件" : "已删除版本索引");
    setVersionDeleteTarget(null);
    if (barcode) {
      setLoading(true);
      const [imgRes, settings] = await Promise.all([
        imageApi.list({ barcode, page_size: 500 }),
        barcodeSettingApi.get(barcode),
      ]);
      setImages(imgRes.items);
      if (imgRes.items.length > 0) {
        const detail = await imageApi.get(imgRes.items[0].id);
        setVersions(detail.versions);
        const latest = detail.versions.find((v) => v.is_latest);
        const latestMtime = latest?.folder_mtime || "";
        // Reset to latest if deleted version was the selected one
        setMainVersion((prev) =>
          prev === deletedMtime
            ? settings.default_main_mtime || latestMtime
            : prev,
        );
        setDetailVersion((prev) =>
          prev === deletedMtime
            ? settings.default_detail_mtime || latestMtime
            : prev,
        );
      } else {
        setVersions([]);
        setMainVersion("");
        setDetailVersion("");
      }
      setLoading(false);
    }
    onDeleted();
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
              <Text>{img.folder_mtime?.replace("T", " ").slice(0, 19)}</Text>
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
                  <Tag
                    key={v.id}
                    color={v.is_latest ? "blue" : "default"}
                    style={{ cursor: "pointer", marginBottom: 4 }}
                    onClick={() => {
                      if (v.image_type === "main") {
                        setMainVersion(v.folder_mtime);
                        barcodeSettingApi.update(barcode!, {
                          default_main_mtime: v.folder_mtime,
                        });
                      } else {
                        setDetailVersion(v.folder_mtime);
                        barcodeSettingApi.update(barcode!, {
                          default_detail_mtime: v.folder_mtime,
                        });
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
                  </Tag>
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
          </Space>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {mainImages.map(renderImage)}
          </div>
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
          </Space>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {detailImages.map(renderImage)}
          </div>
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
          将删除版本 <Text strong>{versionDeleteTarget?.version_label}</Text>{" "}
          下的所有图片：
        </p>
        <Space style={{ marginTop: 12 }}>
          <Button onClick={() => handleVersionDelete(false)}>删除索引</Button>
          <Button danger onClick={() => handleVersionDelete(true)}>
            删除索引和文件
          </Button>
        </Space>
      </Modal>
    </Spin>
  );
};

export default React.memo(ImageCardDetail);
