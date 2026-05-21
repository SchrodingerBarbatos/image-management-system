import React, { useState, useEffect, useCallback } from 'react';
import { Layout, message, Space, Button, Typography } from 'antd';
import { DeleteOutlined, ExportOutlined } from '@ant-design/icons';
import SearchBar from '../components/SearchBar';
import ImageTable from '../components/ImageTable';
import ImageCardDetail from '../components/ImageCardDetail';
import ScanManager from '../components/ScanManager';
import PendingList from '../components/PendingList';
import ExportDialog from '../components/ExportDialog';
import { ImageRec, imageApi, pendingApi, exportApi } from '../services/api';

const { Header, Content, Footer } = Layout;
const { Text } = Typography;

const Home: React.FC = () => {
  // Data state
  const [images, setImages] = useState<ImageRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [pendingCount, setPendingCount] = useState(0);

  // Search & sort
  const [barcode, setBarcode] = useState('');
  const [sortField, setSortField] = useState('created_at');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Selection state — union of table + card selections
  const [tableSelectedKeys, setTableSelectedKeys] = useState<React.Key[]>([]);
  const [selectedBarcode, setSelectedBarcode] = useState<string | null>(null);
  const [selectedMainIds, setSelectedMainIds] = useState<Set<number>>(new Set());
  const [selectedDetailIds, setSelectedDetailIds] = useState<Set<number>>(new Set());

  // Modals
  const [scanVisible, setScanVisible] = useState(false);
  const [pendingVisible, setPendingVisible] = useState(false);
  const [exportVisible, setExportVisible] = useState(false);

  const fetchImages = useCallback(() => {
    setLoading(true);
    imageApi.list({ barcode: barcode || undefined, page, page_size: pageSize, sort: sortField, order: sortOrder })
      .then(res => { setImages(res.items); setTotal(res.total); })
      .finally(() => setLoading(false));
  }, [barcode, page, pageSize, sortField, sortOrder]);

  const fetchPendingCount = useCallback(() => {
    pendingApi.list().then(list => setPendingCount(list.length));
  }, []);

  useEffect(() => { fetchImages(); fetchPendingCount(); }, [fetchImages, fetchPendingCount]);

  // All selected image IDs (union)
  const allSelectedIds = new Set<number>([
    ...tableSelectedKeys.map(Number),
    ...selectedMainIds,
    ...selectedDetailIds,
  ]);

  const handleBatchDelete = async () => {
    if (allSelectedIds.size === 0) return;
    await imageApi.batchDelete(Array.from(allSelectedIds));
    message.success(`已删除 ${allSelectedIds.size} 张图片`);
    setTableSelectedKeys([]);
    setSelectedMainIds(new Set()); setSelectedDetailIds(new Set());
    fetchImages();
  };

  const handleBatchExport = async () => {
    if (allSelectedIds.size === 0) return;
    const res = await imageApi.batchExport(Array.from(allSelectedIds));
    window.open(exportApi.downloadUrl(res.task_id), '_blank');
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
        <Text strong style={{ fontSize: 18 }}>商品图片管理系统</Text>
      </Header>
      <Content style={{ padding: 16 }}>
        <SearchBar
          onSearch={setBarcode}
          onAddScanRoot={() => setScanVisible(true)}
          onExportExcel={() => setExportVisible(true)}
          onTriggerScan={() => { setScanVisible(true); }}
          onOpenPending={() => setPendingVisible(true)}
          pendingCount={pendingCount}
          loading={loading}
        />

        {/* Batch operation bar */}
        {allSelectedIds.size > 0 && (
          <div style={{ background: '#e6f7ff', padding: '8px 16px', marginBottom: 12, borderRadius: 6,
            display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>已选 <Text strong>{allSelectedIds.size}</Text> 张图片</span>
            <Space>
              <Button icon={<ExportOutlined />} onClick={handleBatchExport}>批量导出</Button>
              <Button icon={<DeleteOutlined />} danger onClick={handleBatchDelete}>批量删除</Button>
            </Space>
          </div>
        )}

        <div style={{ display: 'flex', gap: 16 }}>
          <div style={{ flex: '0 0 60%', minWidth: 0 }}>
            <ImageTable
              images={images} loading={loading} total={total}
              page={page} pageSize={pageSize}
              selectedRowKeys={tableSelectedKeys}
              onSelectionChange={(keys) => { setTableSelectedKeys(keys); }}
              onRowClick={setSelectedBarcode}
              onPageChange={(p, ps) => { setPage(p); setPageSize(ps); }}
              onSortChange={(field, order) => { setSortField(field); setSortOrder(order); }}
            />
          </div>
          <div style={{ flex: '0 0 40%', minWidth: 300 }}>
            <ImageCardDetail
              barcode={selectedBarcode}
              selectedMainIds={selectedMainIds}
              selectedDetailIds={selectedDetailIds}
              onMainSelectionChange={setSelectedMainIds}
              onDetailSelectionChange={setSelectedDetailIds}
            />
          </div>
        </div>
      </Content>
      <Footer style={{ textAlign: 'center', padding: '8px 0' }}>
        <Text type="secondary">图片库系统 v1.0</Text>
      </Footer>

      <ScanManager visible={scanVisible} onClose={() => setScanVisible(false)} onScanComplete={fetchImages} />
      <PendingList visible={pendingVisible} onClose={() => setPendingVisible(false)} onConfirmed={() => { fetchImages(); fetchPendingCount(); }} />
      <ExportDialog visible={exportVisible} onClose={() => setExportVisible(false)} />
    </Layout>
  );
};

export default Home;
