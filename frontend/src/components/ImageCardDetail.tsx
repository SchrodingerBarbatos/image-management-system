import React, { useState, useEffect } from 'react';
import { Card, Checkbox, Collapse, Tag, Image, Spin, Empty, Typography, Space, Button } from 'antd';
import { ImageRec, ImageVersion, imageApi } from '../services/api';

const { Text } = Typography;

interface Props {
  barcode: string | null;
  selectedMainIds: Set<number>;
  selectedDetailIds: Set<number>;
  onMainSelectionChange: (ids: Set<number>) => void;
  onDetailSelectionChange: (ids: Set<number>) => void;
}

const ImageCardDetail: React.FC<Props> = ({
  barcode, selectedMainIds, selectedDetailIds,
  onMainSelectionChange, onDetailSelectionChange,
}) => {
  const [loading, setLoading] = useState(false);
  const [images, setImages] = useState<ImageRec[]>([]);
  const [versions, setVersions] = useState<ImageVersion[]>([]);
  const [activeVersion, setActiveVersion] = useState<string | null>(null);

  useEffect(() => {
    if (!barcode) return;
    setLoading(true);
    imageApi.list({ barcode, page_size: 500 }).then(res => {
      setImages(res.items);
      if (res.items.length > 0) {
        imageApi.get(res.items[0].id).then(detail => {
          setVersions(detail.versions);
          setActiveVersion(detail.versions.find(v => v.is_latest)?.version_label || null);
        });
      }
    }).finally(() => setLoading(false));
  }, [barcode]);

  const filteredImages = activeVersion
    ? images.filter(img => img.folder_mtime === versions.find(v => v.version_label === activeVersion)?.folder_mtime)
    : images;

  const mainImages = filteredImages.filter(i => i.image_type === 'main');
  const detailImages = filteredImages.filter(i => i.image_type === 'detail');

  const toggleCheck = (id: number, type: 'main' | 'detail') => {
    const selected = type === 'main' ? new Set(selectedMainIds) : new Set(selectedDetailIds);
    if (selected.has(id)) selected.delete(id); else selected.add(id);
    type === 'main' ? onMainSelectionChange(selected) : onDetailSelectionChange(selected);
  };

  const toggleAll = (imgs: ImageRec[], type: 'main' | 'detail') => {
    const currentSet = type === 'main' ? selectedMainIds : selectedDetailIds;
    const allIds = new Set(imgs.map(i => i.id));
    const allSelected = imgs.every(i => currentSet.has(i.id));
    type === 'main' ? onMainSelectionChange(allSelected ? new Set() : allIds) : onDetailSelectionChange(allSelected ? new Set() : allIds);
  };

  if (!barcode) return <Empty description="点击表格行查看图片详情" />;

  return (
    <Spin spinning={loading}>
      <Card size="small" title={<Text strong>条码: {barcode}</Text>}>
        {versions.length > 0 && (
          <Collapse size="small" style={{ marginBottom: 12 }}
            items={[{
              key: 'versions', label: `版本历史 (${versions.length})`,
              children: versions.map(v => (
                <Tag key={v.id} color={v.is_latest ? 'blue' : 'default'}
                  style={{ cursor: 'pointer', marginBottom: 4 }}
                  onClick={() => setActiveVersion(v.version_label)}>
                  {v.version_label} {v.is_latest ? '(最新)' : ''}
                </Tag>
              )),
            }]}
          />
        )}

        <div style={{ marginBottom: 12 }}>
          <Space style={{ marginBottom: 8 }}>
            <Text strong>主图 ({mainImages.length})</Text>
            <Button size="small" onClick={() => toggleAll(mainImages, 'main')}>全选主图</Button>
          </Space>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {mainImages.map(img => (
              <div key={img.id} style={{ position: 'relative' }}>
                <Image src={imageApi.thumbnailUrl(img.id)} width={100} height={100}
                  style={{ objectFit: 'cover', borderRadius: 4 }} preview={{ src: imageApi.fileUrl(img.id) }} />
                <Checkbox checked={selectedMainIds.has(img.id)}
                  onChange={() => toggleCheck(img.id, 'main')}
                  style={{ position: 'absolute', top: 2, left: 2 }} />
              </div>
            ))}
          </div>
        </div>

        <div>
          <Space style={{ marginBottom: 8 }}>
            <Text strong>详情图 ({detailImages.length})</Text>
            <Button size="small" onClick={() => toggleAll(detailImages, 'detail')}>全选详情图</Button>
          </Space>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {detailImages.map(img => (
              <div key={img.id} style={{ position: 'relative' }}>
                <Image src={imageApi.thumbnailUrl(img.id)} width={100} height={100}
                  style={{ objectFit: 'cover', borderRadius: 4 }} preview={{ src: imageApi.fileUrl(img.id) }} />
                <Checkbox checked={selectedDetailIds.has(img.id)}
                  onChange={() => toggleCheck(img.id, 'detail')}
                  style={{ position: 'absolute', top: 2, left: 2 }} />
              </div>
            ))}
          </div>
        </div>
      </Card>
    </Spin>
  );
};

export default ImageCardDetail;
