import React, { useState, useEffect, useCallback } from 'react';
import { Layout, message, Space, Button, Typography, Modal } from 'antd';
import { DeleteOutlined, ExportOutlined } from '@ant-design/icons';
import SearchBar from '../components/SearchBar';
import ImageTable from '../components/ImageTable';
import ImageCardDetail from '../components/ImageCardDetail';
import ScanManager from '../components/ScanManager';
import PendingList from '../components/PendingList';
import ExportDialog from '../components/ExportDialog';
import { BarcodeRec, imageApi, barcodeApi, pendingApi, exportApi } from '../services/api';

const { Header, Content, Footer } = Layout;
const { Text } = Typography;

const Home: React.FC = () => {
  // Barcode-level table data
  const [barcodes, setBarcodes] = useState<BarcodeRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [pendingCount, setPendingCount] = useState(0);

  // Search & sort
  const [barcode, setBarcode] = useState('');
  const [sortField, setSortField] = useState('barcode');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  // Selection state
  const [selectedBarcode, setSelectedBarcode] = useState<string | null>(null);
  const [selectedBarcodes, setSelectedBarcodes] = useState<Set<string>>(new Set());
  const [selectedMainIds, setSelectedMainIds] = useState<Set<number>>(new Set());
  const [selectedDetailIds, setSelectedDetailIds] = useState<Set<number>>(new Set());
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchDeleteVisible, setBatchDeleteVisible] = useState(false);

  // Modals
  const [scanVisible, setScanVisible] = useState(false);
  const [pendingVisible, setPendingVisible] = useState(false);
  const [exportVisible, setExportVisible] = useState(false);

  const fetchBarcodes = useCallback(() => {
    setLoading(true);
    barcodeApi.list({ barcode: barcode || undefined, page, page_size: pageSize, sort: sortField, order: sortOrder })
      .then(res => { setBarcodes(res.items); setTotal(res.total); })
      .finally(() => setLoading(false));
  }, [barcode, page, pageSize, sortField, sortOrder]);

  const fetchPendingCount = useCallback(() => {
    pendingApi.list().then(list => setPendingCount(list.length));
  }, []);

  useEffect(() => { fetchBarcodes(); fetchPendingCount(); }, [fetchBarcodes, fetchPendingCount]);

  const allSelectedIds = new Set<number>([...selectedMainIds, ...selectedDetailIds]);

  // Resolve barcode selections to image IDs for batch operations
  const resolveBarcodeImageIds = async (): Promise<number[]> => {
    if (selectedBarcodes.size === 0) return [];
    const results = await Promise.all(
      Array.from(selectedBarcodes).map(bc =>
        imageApi.list({ barcode: bc, page_size: 500 })
      )
    );
    return results.flatMap(res => res.items.map(i => i.id));
  };

  const executeBatchDelete = async (deleteFile: boolean) => {
    const barcodeIds = await resolveBarcodeImageIds();
    const allIds = [...new Set([...Array.from(allSelectedIds), ...barcodeIds])];
    if (allIds.length === 0) return;
    setBatchLoading(true);
    setBatchDeleteVisible(false);
    try {
      await imageApi.batchDelete(allIds, deleteFile);
      message.success(deleteFile ? `已删除 ${allIds.length} 张图片索引和文件` : `已删除 ${allIds.length} 张图片索引`);
      setSelectedMainIds(new Set()); setSelectedDetailIds(new Set());
      setSelectedBarcodes(new Set());
      fetchBarcodes();
    } finally { setBatchLoading(false); }
  };

  const handleBatchDelete = () => setBatchDeleteVisible(true);

  const handleBatchExport = async () => {
    const barcodeIds = await resolveBarcodeImageIds();
    const allIds = [...new Set([...Array.from(allSelectedIds), ...barcodeIds])];
    if (allIds.length === 0) return;
    setBatchLoading(true);
    try {
      const res = await imageApi.batchExport(allIds);
      if (res.excluded > 0) {
        message.warning(`已导出 ${res.total} 张图片，${res.excluded} 张因目录已禁用被跳过`);
      }
      window.open(exportApi.downloadUrl(res.task_id), '_blank');
    } finally { setBatchLoading(false); }
  };

  const handleCardDeleted = useCallback(() => {
    fetchBarcodes();
    fetchPendingCount();
  }, [fetchBarcodes, fetchPendingCount]);

  const handleScanComplete = useCallback(() => {
    fetchBarcodes();
    fetchPendingCount();
    setSelectedBarcode(null);
  }, [fetchBarcodes, fetchPendingCount]);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ background: '#fff', padding: '0 24px', borderBottom: '1px solid #f0f0f0' }}>
        <Text strong style={{ fontSize: 18 }}>商品图片管理系统</Text>
      </Header>
      <Content style={{ padding: 16 }}>
        <SearchBar
          onSearch={setBarcode}
          onOpenScanManager={() => setScanVisible(true)}
          onExportExcel={() => setExportVisible(true)}
          onOpenPending={() => setPendingVisible(true)}
          pendingCount={pendingCount}
        />

        {(allSelectedIds.size > 0 || selectedBarcodes.size > 0) && (
          <div style={{ background: '#e6f7ff', padding: '8px 16px', marginBottom: 12, borderRadius: 6,
            display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>
              已选{allSelectedIds.size > 0 && <Text strong> {allSelectedIds.size} 张</Text>}
              {allSelectedIds.size > 0 && selectedBarcodes.size > 0 && ' +'}
              {selectedBarcodes.size > 0 && <Text strong> {selectedBarcodes.size} 个条码</Text>}
            </span>
            <Space>
              <Button icon={<ExportOutlined />} loading={batchLoading} onClick={handleBatchExport}>批量导出</Button>
              <Button icon={<DeleteOutlined />} danger loading={batchLoading} onClick={handleBatchDelete}>批量删除</Button>
            </Space>
          </div>
        )}

        <div style={{ display: 'flex', gap: 16 }}>
          <div style={{ flex: '0 0 60%', minWidth: 0 }}>
            <ImageTable
              barcodes={barcodes} loading={loading} total={total}
              page={page} pageSize={pageSize}
              selectedBarcode={selectedBarcode}
              selectedBarcodes={selectedBarcodes}
              onSelectionChange={setSelectedBarcodes}
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
              onDeleted={handleCardDeleted}
            />
          </div>
        </div>
      </Content>
      <Footer style={{ textAlign: 'center', padding: '8px 0' }}>
        <Text type="secondary">图片库系统 v1.0</Text>
      </Footer>

      <ScanManager visible={scanVisible} onClose={() => setScanVisible(false)} onScanComplete={handleScanComplete} />
      <PendingList visible={pendingVisible} onClose={() => setPendingVisible(false)} onConfirmed={() => { fetchBarcodes(); fetchPendingCount(); }} />
      <ExportDialog visible={exportVisible} onClose={() => setExportVisible(false)} />

      <Modal title="批量删除" open={batchDeleteVisible} onCancel={() => setBatchDeleteVisible(false)}
        footer={null} width={400}>
        <p>确定删除选中的 {allSelectedIds.size + (selectedBarcodes.size > 0 ? selectedBarcodes.size : 0)} 条记录？</p>
        <Space style={{ marginTop: 12 }}>
          <Button onClick={() => executeBatchDelete(false)}>删除索引</Button>
          <Button danger onClick={() => executeBatchDelete(true)}>删除索引和文件</Button>
        </Space>
      </Modal>
    </Layout>
  );
};

export default Home;
