import React, { useState, useEffect, useRef } from 'react';
import { Modal, Upload, Button, Select, Radio, Space, message, Steps, Progress, Table, Popconfirm } from 'antd';
import { UploadOutlined, DownloadOutlined, HistoryOutlined } from '@ant-design/icons';
import { exportApi } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
}

const ExportDialog: React.FC<Props> = ({ visible, onClose }) => {
  const [step, setStep] = useState(0);
  const [columns, setColumns] = useState<string[]>([]);
  const [sheets, setSheets] = useState<string[]>([]);
  const [selectedSheet, setSelectedSheet] = useState('');
  const [uploadId, setUploadId] = useState('');
  const [barcodeColumn, setBarcodeColumn] = useState('');
  const [imageType, setImageType] = useState('all');
  const [folderMode, setFolderMode] = useState('folder');
  const [taskId, setTaskId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressTotal, setProgressTotal] = useState(0);
  const [showHistory, setShowHistory] = useState(false);
  const [taskList, setTaskList] = useState<{ id: number; status: string; total_images: number; created_at: string; file_available: boolean }[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [pollingCount, setPollingCount] = useState(0);
  const MAX_POLLING_RETRIES = 150; // 150 * 2s = 5min

  const clearTimer = () => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    setPollingCount(0);
  };

  useEffect(() => {
    return clearTimer;
  }, []);

  const startPolling = (tid: number) => {
    clearTimer();
    const poll = async () => {
      try {
        const p = await exportApi.getProgress(tid);
        setProgress(p.progress);
        setProgressTotal(p.total);
        if (p.status === 'done') {
          clearTimer();
          setStep(3);
          return;
        }
        if (p.status === 'failed') {
          clearTimer();
          message.error('ZIP 生成失败');
          setStep(0);
          return;
        }
        setPollingCount(prev => {
          const next = prev + 1;
          if (next >= MAX_POLLING_RETRIES) {
            clearTimer();
            message.error('生成超时，请重试');
            setStep(0);
            return 0;
          }
          timerRef.current = setTimeout(poll, 2000) as unknown as ReturnType<typeof setInterval>;
          return next;
        });
      } catch {
        setPollingCount(prev => {
          const next = prev + 1;
          if (next >= MAX_POLLING_RETRIES) {
            clearTimer();
            message.error('生成超时，请重试');
            setStep(0);
            return 0;
          }
          timerRef.current = setTimeout(poll, 2000) as unknown as ReturnType<typeof setInterval>;
          return next;
        });
      }
    };
    timerRef.current = setTimeout(poll, 1000) as unknown as ReturnType<typeof setInterval>;
  };

  const handleUpload = async (file: File) => {
    setLoading(true);
    try {
      const res = await exportApi.uploadExcel(file);
      setColumns(res.columns);
      setSheets(res.sheets || []);
      setSelectedSheet(res.sheets?.[0] || '');
      setUploadId(res.upload_id);
      setStep(1);
    } catch {
      message.error('上传失败');
    }
    setLoading(false);
    return false;
  };

  const handleGenerate = async () => {
    if (!barcodeColumn) return;
    setLoading(true);
    try {
      const res = await exportApi.generateZip({
        barcode_column: barcodeColumn,
        image_type: imageType,
        upload_id: uploadId,
        sheet_name: selectedSheet || undefined,
        flat: folderMode === 'flat',
      });
      setTaskId(res.task_id);
      setProgress(0);
      setProgressTotal(res.total_images);
      setStep(2);
      startPolling(res.task_id);
      if (res.excluded_barcodes > 0) {
        message.warning(`${res.excluded_barcodes} 个条码未匹配到图片`);
      }
    } catch {
      message.error('生成失败');
    }
    setLoading(false);
  };

  const handleDownload = () => {
    if (taskId) window.open(exportApi.downloadUrl(taskId), '_blank');
  };

  const loadHistory = async () => {
    try {
      const list = await exportApi.listTasks();
      setTaskList(list);
      setShowHistory(true);
    } catch { message.error('加载失败'); }
  };

  const handleDeleteTask = async (tid: number) => {
    try {
      await exportApi.deleteTask(tid);
      setTaskList(prev => prev.filter(t => t.id !== tid));
      message.success('已删除');
    } catch { message.error('删除失败'); }
  };

  const reset = () => {
    clearTimer();
    setStep(0); setColumns([]); setSheets([]); setSelectedSheet(''); setUploadId(''); setBarcodeColumn(''); setTaskId(null);
    setProgress(0); setProgressTotal(0); setShowHistory(false); setTaskList([]);
    setFolderMode('folder'); setImageType('all');
  };

  const historyColumns = [
    { title: 'ID', dataIndex: 'id', width: 50 },
    { title: '状态', dataIndex: 'status', width: 70, render: (s: string) => s === 'done' ? '已完成' : s === 'processing' ? '生成中' : s },
    { title: '文件数', dataIndex: 'total_images', width: 60 },
    { title: '时间', dataIndex: 'created_at', render: (t: string) => t ? new Date(t).toLocaleString() : '' },
    { title: '操作', key: 'action', width: 130,
      render: (_: unknown, rec: { id: number; file_available: boolean }) => (
        <Space size="small">
          {rec.file_available
            ? <a href={exportApi.downloadUrl(rec.id)} target="_blank">下载</a>
            : <span style={{ color: '#999' }}>已过期</span>}
          <Popconfirm title="确定删除？" onConfirm={() => handleDeleteTask(rec.id)}>
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Modal title="Excel 批量导出" open={visible} onCancel={() => { reset(); onClose(); }} width={600} footer={null}>
      <Steps current={step} size="small" style={{ marginBottom: 24 }}
        items={[
          { title: '上传 Excel' },
          { title: '选择数据' },
          { title: '生成中' },
          { title: '下载' },
        ]} />

      {step === 0 && !showHistory && (
        <Space>
          <Upload accept=".xlsx" maxCount={1} beforeUpload={handleUpload} showUploadList={false}>
            <Button icon={<UploadOutlined />} loading={loading}>上传 Excel 文件</Button>
          </Upload>
          <Button icon={<HistoryOutlined />} onClick={loadHistory}>历史导出</Button>
        </Space>
      )}

      {step === 0 && showHistory && (
        <>
          <Button size="small" onClick={() => setShowHistory(false)} style={{ marginBottom: 12 }}>{'< 返回'}</Button>
          <Table columns={historyColumns} dataSource={taskList} rowKey="id" size="small" pagination={false} />
        </>
      )}

      {step === 1 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          {sheets.length > 1 && (
            <div>
              <span>工作表：</span>
              <Select value={selectedSheet} onChange={setSelectedSheet} style={{ width: '100%' }}
                options={sheets.map(s => ({ value: s, label: s }))} />
            </div>
          )}
          <div>
            <span>条码所在列：</span>
            <Select value={barcodeColumn} onChange={setBarcodeColumn} style={{ width: '100%' }}
              placeholder="选择条码所在的列"
              options={columns.map(c => ({ value: c, label: c }))} />
          </div>
          <div>
            <span>导出类型：</span>
            <Radio.Group value={imageType} onChange={e => setImageType(e.target.value)}>
              <Radio.Button value="all">全部</Radio.Button>
              <Radio.Button value="main">仅主图</Radio.Button>
              <Radio.Button value="detail">仅详情图</Radio.Button>
            </Radio.Group>
          </div>
          <div>
            <span>文件夹：</span>
            <Radio.Group value={folderMode} onChange={e => setFolderMode(e.target.value)}>
              <Radio.Button value="folder">分文件夹</Radio.Button>
              <Radio.Button value="flat">平铺</Radio.Button>
            </Radio.Group>
          </div>
          <Button type="primary" onClick={handleGenerate} loading={loading} disabled={!barcodeColumn}>
            生成 ZIP
          </Button>
        </Space>
      )}

      {step === 2 && (
        <Space direction="vertical" style={{ width: '100%', alignItems: 'center' }}>
          <Progress percent={progressTotal > 0 ? Math.round((progress / progressTotal) * 100) : 0}
            format={() => `${progress} / ${progressTotal}`} />
          <p>正在生成压缩包...</p>
        </Space>
      )}

      {step === 3 && (
        <Space direction="vertical" style={{ width: '100%', alignItems: 'center' }}>
          <p>ZIP 文件已生成（{progressTotal} 个文件）</p>
          <Button icon={<DownloadOutlined />} type="primary" size="large" onClick={handleDownload}>
            下载 ZIP
          </Button>
        </Space>
      )}
    </Modal>
  );
};

export default ExportDialog;
