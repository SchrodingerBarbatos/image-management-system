import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Input, Button, Modal, App } from 'antd';
import { DeleteOutlined, ExportOutlined, SearchOutlined } from '@ant-design/icons';
import BarcodeTable from '../components/BarcodeTable';
import DetailPanel from '../components/DetailPanel';
import { ActivityCard } from '../components/ui';
import { useTaskPolling } from '../hooks/useTaskPolling';
import { BarcodeRec, imageApi, barcodeApi, exportApi, taskApi } from '../services/api';

interface Props {
  onDataChanged: () => void;
  refreshRevision: number;
}

const LibraryView: React.FC<Props> = ({ onDataChanged, refreshRevision }) => {
  const { message } = App.useApp();
  // Barcode-level table data
  const [barcodes, setBarcodes] = useState<BarcodeRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

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
  const [exportProgress, setExportProgress] = useState<{ progress: number; total: number } | null>(null);
  const exportTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Delete task polling
  const deletePolling = useTaskPolling({
    onComplete: () => {
      setSelectedMainIds(new Set()); setSelectedDetailIds(new Set());
      setSelectedBarcodes(new Set());
      setCapturedAllIds([]);
      fetchBarcodes();
      onDataChanged();
    },
    successMessage: (task) => `删除完成，共删除 ${task.result_count} 张图片`,
  });

  const fetchBarcodes = useCallback(() => {
    setLoading(true);
    barcodeApi.list({ barcode: barcode || undefined, page, page_size: pageSize, sort: sortField, order: sortOrder })
      .then(res => { setBarcodes(res.items); setTotal(res.total); })
      .finally(() => setLoading(false));
  }, [barcode, page, pageSize, sortField, sortOrder]);

  useEffect(() => {
    fetchBarcodes();
  }, [fetchBarcodes, refreshRevision]);

  // Cleanup export poll timer on unmount
  useEffect(() => {
    return () => {
      if (exportTimerRef.current) clearTimeout(exportTimerRef.current);
    };
  }, []);

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
      setExportProgress({ progress: 0, total: res.total });
      const poll = async () => {
        try {
          const p = await exportApi.getProgress(res.task_id);
          setExportProgress({ progress: p.progress, total: p.total });
          if (p.status === 'done' || p.status === 'partial_failed') {
            setBatchLoading(false);
            setExportProgress(null);
            if (p.status === 'partial_failed') {
              message.warning(
                p.error_message ||
                `部分导出：实际写入 ${p.written_count ?? '?'} / 计划 ${p.planned_count ?? p.total}`,
              );
            }
            window.open(exportApi.downloadUrl(res.task_id), '_blank');
            return;
          }
          if (p.status === 'failed') {
            setBatchLoading(false);
            setExportProgress(null);
            message.error(p.error_message || '导出失败');
            return;
          }
          pollCount++;
          if (pollCount >= maxPolls) {
            setBatchLoading(false);
            setExportProgress(null);
            message.error('生成超时，请重试');
            return;
          }
          exportTimerRef.current = setTimeout(poll, 2000);
        } catch {
          pollCount++;
          if (pollCount >= maxPolls) {
            setBatchLoading(false);
            setExportProgress(null);
            message.error('生成超时，请重试');
            return;
          }
          exportTimerRef.current = setTimeout(poll, 2000);
        }
      };
      exportTimerRef.current = setTimeout(poll, 1000);
    } catch {
      setBatchLoading(false);
      message.error('创建导出任务失败');
    }
  };

  const handleCardDeleted = useCallback(() => {
    fetchBarcodes();
    onDataChanged();
  }, [fetchBarcodes, onDataChanged]);

  const selectionCount = allSelectedIds.size;
  const hasSelection = selectionCount > 0 || selectedBarcodes.size > 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div className="toolbar" style={{ flex: 'none' }}>
        <Input.Search
          placeholder="输入条码搜索…"
          allowClear
          onSearch={(value) => { setBarcode(value); setPage(1); }}
          style={{ width: 300 }}
          prefix={<SearchOutlined />}
        />
        <span className="hint">共 {total} 条码</span>
      </div>

      <div className="library-grid" style={{ flex: 1 }}>
        <div className="library-left">
          <div className="panel" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            <BarcodeTable
              barcodes={barcodes} loading={loading} total={total}
              page={page} pageSize={pageSize}
              selectedBarcode={selectedBarcode}
              selectedBarcodes={selectedBarcodes}
              onSelectionChange={setSelectedBarcodes}
              onRowClick={(bc) => {
                setSelectedBarcode(bc);
                setSelectedMainIds(new Set());
                setSelectedDetailIds(new Set());
              }}
              onPageChange={(p, ps) => { setPage(p); setPageSize(ps); }}
              onSortChange={(field, order) => { setSortField(field); setSortOrder(order); }}
            />
          </div>
        </div>
        <div className="library-right">
          <DetailPanel
            barcode={selectedBarcode}
            selectedMainIds={selectedMainIds}
            selectedDetailIds={selectedDetailIds}
            onMainSelectionChange={setSelectedMainIds}
            onDetailSelectionChange={setSelectedDetailIds}
            onDeleted={handleCardDeleted}
          />
        </div>
      </div>

      {hasSelection && (
        <div className="dock">
          <span className="dock-count">
            已选
            {selectionCount > 0 && <> <strong>{selectionCount}</strong> 张</>}
            {selectionCount > 0 && selectedBarcodes.size > 0 && ' +'}
            {selectedBarcodes.size > 0 && <> <strong>{selectedBarcodes.size}</strong> 条码</>}
          </span>
          <Button icon={<ExportOutlined />} loading={batchLoading} onClick={handleBatchExport}>
            批量导出
          </Button>
          <Button icon={<DeleteOutlined />} danger loading={batchLoading} onClick={handleBatchDelete}>
            批量删除
          </Button>
        </div>
      )}

      {exportProgress && (
        <ActivityCard
          title="正在生成导出包…"
          value={exportProgress.total > 0 ? exportProgress.progress / exportProgress.total : undefined}
          meta={
            <>
              已写入 {exportProgress.progress} / {exportProgress.total}
            </>
          }
        />
      )}

      {deletePolling.polling && deletePolling.currentTask && (
        <ActivityCard
          title="正在删除…"
          ledColor="red"
          value={
            deletePolling.currentTask.total > 0
              ? deletePolling.currentTask.progress / deletePolling.currentTask.total
              : undefined
          }
          meta={
            <>
              进度 {deletePolling.currentTask.progress}/{deletePolling.currentTask.total}
            </>
          }
        />
      )}

      <Modal title="批量删除" open={batchDeleteVisible} onCancel={() => setBatchDeleteVisible(false)}
        footer={null} width={400}>
        <p>确定删除选中的 <strong className="mono">{capturedAllIds.length}</strong> 条记录？</p>
        <div className="modal-actions">
          <Button onClick={() => executeBatchDelete(false)}>删除索引</Button>
          <Button danger onClick={() => executeBatchDelete(true)}>删除索引和文件</Button>
        </div>
      </Modal>
    </div>
  );
};

export default LibraryView;
