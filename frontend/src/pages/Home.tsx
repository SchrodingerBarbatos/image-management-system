import React, { useState, useEffect, useCallback, useRef, Suspense } from 'react';
import { Layout, message, Space, Button, Typography, Modal, Spin } from 'antd';
import { DeleteOutlined, ExportOutlined } from '@ant-design/icons';
import SearchBar from '../components/SearchBar';
import ImageTable from '../components/ImageTable';
import ImageCardDetail from '../components/ImageCardDetail';
import { useTaskPolling } from '../hooks/useTaskPolling';
import { BarcodeRec, imageApi, barcodeApi, pendingApi, exportApi, taskApi } from '../services/api';

const ScanManager = React.lazy(() => import('../components/ScanManager'));
const PendingList = React.lazy(() => import('../components/PendingList'));
const BatchOperations = React.lazy(() => import('../components/BatchOperations'));
const ExportDialog = React.lazy(() => import('../components/ExportDialog'));

const LazyFallback: React.FC = () => (
  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', padding: 48 }}>
    <Spin />
  </div>
);

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
  const [capturedAllIds, setCapturedAllIds] = useState<number[]>([]);
  const exportTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Delete task polling
  const deletePolling = useTaskPolling({
    onComplete: (task) => {
      setSelectedMainIds(new Set()); setSelectedDetailIds(new Set());
      setSelectedBarcodes(new Set());
      setCapturedAllIds([]);
      fetchBarcodes();
    },
    successMessage: (task) => `删除完成，共删除 ${task.result_count} 张图片`,
  });

  // Modals
  const [scanVisible, setScanVisible] = useState(false);
  const [pendingVisible, setPendingVisible] = useState(false);
  const [batchVisible, setBatchVisible] = useState(false);
  const [exportVisible, setExportVisible] = useState(false);

  const fetchBarcodes = useCallback(() => {
    setLoading(true);
    barcodeApi.list({ barcode: barcode || undefined, page, page_size: pageSize, sort: sortField, order: sortOrder })
      .then(res => { setBarcodes(res.items); setTotal(res.total); })
      .finally(() => setLoading(false));
  }, [barcode, page, pageSize, sortField, sortOrder]);

  const fetchPendingCount = useCallback(() => {
    pendingApi.count().then(setPendingCount);
  }, []);

  useEffect(() => { fetchBarcodes(); fetchPendingCount(); }, [fetchBarcodes, fetchPendingCount]);

  const allSelectedIds = new Set<number>([...selectedMainIds, ...selectedDetailIds]);

  // Resolve barcode selections to image IDs for batch operations
  const resolveBarcodeImageIds = async (): Promise<number[]> => {
    if (selectedBarcodes.size === 0) return [];
    const result = await imageApi.getBarcodeImageIds(Array.from(selectedBarcodes));
    return result.image_ids;
  };

  const executeBatchDelete = async (deleteFile: boolean) => {
    if (capturedAllIds.length === 0) return;
    setBatchLoading(true);
    setBatchDeleteVisible(false);
    try {
      const task = await taskApi.createBatchDeleteImagesTask(capturedAllIds, deleteFile);
      deletePolling.startPolling(task.id);
    } catch {
      message.error('创建删除任务失败');
    } finally { setBatchLoading(false); }
  };

  const handleBatchDelete = async () => {
    setBatchLoading(true);
    try {
      const barcodeIds = await resolveBarcodeImageIds();
      const allIds = [...new Set([...Array.from(allSelectedIds), ...barcodeIds])];
      setCapturedAllIds(allIds);
      setBatchDeleteVisible(true);
    } finally { setBatchLoading(false); }
  };

  const handleBatchExport = async () => {
    const barcodeIds = await resolveBarcodeImageIds();
    const allIds = [...new Set([...Array.from(allSelectedIds), ...barcodeIds])];
    if (allIds.length === 0) return;
    setBatchLoading(true);
    try {
      const res = await imageApi.batchExport(allIds);
      const skipped = res.scanroot_excluded + (res.version_filtered || 0);
      if (res.total === 0) {
        setBatchLoading(false);
        message.warning('没有匹配到可导出的图片');
        return;
      }
      if (skipped > 0) {
        message.warning(`已导出 ${res.total} 张图片，${res.scanroot_excluded} 张因目录已禁用被跳过，${res.version_filtered || 0} 张因版本去重被跳过`);
      }
      // Poll for completion instead of immediate download
      let pollCount = 0;
      const maxPolls = 150; // 5 min
      const poll = async () => {
        try {
          const p = await exportApi.getProgress(res.task_id);
          if (p.status === 'done') {
            setBatchLoading(false);
            window.open(exportApi.downloadUrl(res.task_id), '_blank');
            return;
          }
          if (p.status === 'failed') {
            setBatchLoading(false);
            message.error(p.error_message || '导出失败');
            return;
          }
          pollCount++;
          if (pollCount >= maxPolls) {
            setBatchLoading(false);
            message.error('生成超时，请重试');
            return;
          }
          exportTimerRef.current = setTimeout(poll, 2000);
        } catch {
          pollCount++;
          if (pollCount >= maxPolls) {
            setBatchLoading(false);
            message.error('生成超时，请重试');
            return;
          }
          exportTimerRef.current = setTimeout(poll, 2000);
        }
      };
      exportTimerRef.current = setTimeout(poll, 1000);
    } catch {
      setBatchLoading(false);
    }
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
          onSearch={(value) => { setBarcode(value); setPage(1); }}
          onOpenScanManager={() => setScanVisible(true)}
          onExportExcel={() => setExportVisible(true)}
          onOpenBatch={() => setBatchVisible(true)}
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

      <Suspense fallback={<LazyFallback />}>
        {scanVisible && <ScanManager visible={scanVisible} onClose={() => setScanVisible(false)} onScanComplete={handleScanComplete} />}
      </Suspense>
      <Suspense fallback={<LazyFallback />}>
        {pendingVisible && <PendingList visible={pendingVisible} onClose={() => setPendingVisible(false)} onConfirmed={() => { fetchBarcodes(); fetchPendingCount(); }} />}
      </Suspense>
      <Suspense fallback={<LazyFallback />}>
        {batchVisible && <BatchOperations visible={batchVisible} onClose={() => setBatchVisible(false)} onCompleted={() => { fetchBarcodes(); fetchPendingCount(); }} />}
      </Suspense>
      <Suspense fallback={<LazyFallback />}>
        {exportVisible && <ExportDialog visible={exportVisible} onClose={() => setExportVisible(false)} />}
      </Suspense>

      {deletePolling.polling && deletePolling.currentTask && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 1000,
          background: '#fff', padding: '12px 16px', borderRadius: 8,
          boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
          minWidth: 200,
        }}>
          <div style={{ marginBottom: 8, fontWeight: 500 }}>正在删除...</div>
          <div style={{ fontSize: 12, color: '#666' }}>
            进度: {deletePolling.currentTask.progress}/{deletePolling.currentTask.total}
          </div>
        </div>
      )}

      <Modal title="批量删除" open={batchDeleteVisible} onCancel={() => setBatchDeleteVisible(false)}
        footer={null} width={400}>
        <p>确定删除选中的 {capturedAllIds.length} 条记录？</p>
        <Space style={{ marginTop: 12 }}>
          <Button onClick={() => executeBatchDelete(false)}>删除索引</Button>
          <Button danger onClick={() => executeBatchDelete(true)}>删除索引和文件</Button>
        </Space>
      </Modal>
    </Layout>
  );
};

export default Home;
