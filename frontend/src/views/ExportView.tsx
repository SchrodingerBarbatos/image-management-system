import React, { useState, useEffect, useRef } from 'react';
import { Upload, Button, Select, App, Table, Popconfirm } from 'antd';
import { UploadOutlined, DownloadOutlined, CheckOutlined } from '@ant-design/icons';
import { exportApi } from '../services/api';
import { Seg, LightBar } from '../components/ui';

const MAX_POLLING_RETRIES = 150; // 150 * 2s = 5min

const WIZ_STEPS = ['上传 Excel', '选择数据', '生成中', '下载'];

const ExportView: React.FC = () => {
  const { message } = App.useApp();
  const [tab, setTab] = useState<'new' | 'history'>('new');
  const [step, setStep] = useState(0);
  const [columns, setColumns] = useState<string[]>([]);
  const [sheets, setSheets] = useState<string[]>([]);
  const [sheetColumns, setSheetColumns] = useState<Record<string, string[]>>({});
  const [selectedSheet, setSelectedSheet] = useState('');
  const [uploadId, setUploadId] = useState('');
  const [barcodeColumn, setBarcodeColumn] = useState('');
  const [imageType, setImageType] = useState('all');
  const [folderMode, setFolderMode] = useState('folder');
  const [taskId, setTaskId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressTotal, setProgressTotal] = useState(0);
  const [taskList, setTaskList] = useState<{ id: number; status: string; total_images: number; created_at: string; file_available: boolean; error_message?: string; has_detail: boolean }[]>([]);
  const [errorMessage, setErrorMessage] = useState('');
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Generation token: ignore in-flight responses after clear/reset/unmount
  const pollGenerationRef = useRef(0);

  const clearTimer = () => {
    pollGenerationRef.current += 1;
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
  };

  useEffect(() => {
    return clearTimer;
  }, []);

  useEffect(() => {
    if (tab === 'history') {
      exportApi.listTasks().then(setTaskList).catch(() => message.error('加载失败'));
    }
  }, [tab]);

  const startPolling = (tid: number) => {
    clearTimer();
    const generation = pollGenerationRef.current;
    let count = 0;
    const poll = async () => {
      if (generation !== pollGenerationRef.current) return;
      try {
        const p = await exportApi.getProgress(tid);
        if (generation !== pollGenerationRef.current) return;
        setProgress(p.progress);
        setProgressTotal(p.total);
        if (p.status === 'done' || p.status === 'partial_failed') {
          clearTimer();
          setStep(3);
          if (p.status === 'partial_failed') {
            const written = p.written_count ?? '?';
            const planned = p.planned_count ?? p.total;
            message.warning(
              p.error_message || `部分导出：实际写入 ${written} / 计划 ${planned}`,
            );
          }
          return;
        }
        if (p.status === 'failed') {
          clearTimer();
          setErrorMessage(p.error_message || '未知错误');
          message.error('ZIP 生成失败');
          setStep(0);
          return;
        }
        count += 1;
        if (count >= MAX_POLLING_RETRIES) {
          clearTimer();
          message.error('生成超时，请重试');
          setStep(0);
          return;
        }
        timerRef.current = setTimeout(poll, 2000);
      } catch {
        if (generation !== pollGenerationRef.current) return;
        count += 1;
        if (count >= MAX_POLLING_RETRIES) {
          clearTimer();
          message.error('生成超时，请重试');
          setStep(0);
          return;
        }
        timerRef.current = setTimeout(poll, 2000);
      }
    };
    timerRef.current = setTimeout(poll, 1000);
  };

  const handleUpload = async (file: File) => {
    setLoading(true);
    try {
      const res = await exportApi.uploadExcel(file);
      setColumns(res.columns);
      setSheets(res.sheets || []);
      setSheetColumns(res.sheet_columns || {});
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

  const handleDeleteTask = async (tid: number) => {
    try {
      await exportApi.deleteTask(tid);
      setTaskList(prev => prev.filter(t => t.id !== tid));
      message.success('已删除');
    } catch { message.error('删除失败'); }
  };

  const resetWizard = () => {
    clearTimer();
    setStep(0); setColumns([]); setSheets([]); setSheetColumns({}); setSelectedSheet(''); setUploadId(''); setBarcodeColumn(''); setTaskId(null);
    setProgress(0); setProgressTotal(0); setErrorMessage('');
    setFolderMode('folder'); setImageType('all');
  };

  const historyColumns = [
    { title: 'ID', dataIndex: 'id', width: 60,
      render: (v: number) => <span className="mono">#{v}</span> },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string, rec: { error_message?: string }) =>
      s === 'done'
        ? <span style={{ color: 'var(--green)' }}>已完成</span>
        : s === 'processing'
          ? <span style={{ color: 'var(--acc-2)' }}>生成中</span>
          : <span title={rec.error_message} style={{ color: 'var(--red)', cursor: 'help' }}>失败</span> },
    { title: '文件数', dataIndex: 'total_images', width: 80,
      render: (v: number) => <span className="mono">{v}</span> },
    { title: '时间', dataIndex: 'created_at',
      render: (t: string) => <span className="mono">{t ? new Date(t).toLocaleString() : ''}</span> },
    { title: '操作', key: 'action', width: 200,
      render: (_: unknown, rec: { id: number; file_available: boolean; has_detail: boolean }) => (
        <span style={{ display: 'inline-flex', gap: 4 }}>
          {rec.file_available
            ? <Button type="link" size="small" href={exportApi.downloadUrl(rec.id)} target="_blank">下载</Button>
            : <span style={{ color: 'var(--t3)', padding: '0 8px', lineHeight: '22px', fontSize: 12 }}>已过期</span>}
          <Popconfirm title="确定删除？" onConfirm={() => handleDeleteTask(rec.id)}>
            <Button type="link" size="small" danger>删除</Button>
          </Popconfirm>
          {rec.has_detail
            ? <Button type="link" size="small" href={exportApi.detailUrl(rec.id)} target="_blank">详情</Button>
            : <span style={{ color: 'var(--t3)', padding: '0 8px', lineHeight: '22px', fontSize: 12 }}>详情</span>}
        </span>
      ),
    },
  ];

  const rowStyle: React.CSSProperties = { marginBottom: 16 };
  const labelStyle: React.CSSProperties = { display: 'block', fontSize: 12, color: 'var(--t2)', marginBottom: 6 };

  return (
    <>
      <div className="view-head">
        <div className="view-sub">按 Excel 中的条码列表导出图片 ZIP</div>
        <div className="view-head-right">
          <Seg
            value={tab}
            onChange={setTab}
            options={[
              { value: 'new', label: '新建导出' },
              { value: 'history', label: '历史导出' },
            ]}
          />
        </div>
      </div>

      {tab === 'history' && (
        <div className="panel">
          <Table
            columns={historyColumns}
            dataSource={taskList}
            rowKey="id"
            size="small"
            pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
          />
        </div>
      )}

      {tab === 'new' && (
        <div className="export-grid">
          <div className="wizard-steps">
            {WIZ_STEPS.map((label, i) => (
              <div
                key={label}
                className={`wiz-step${i === step ? ' on' : ''}${i < step ? ' done' : ''}`}
              >
                <span className="wiz-dot">
                  {i < step ? <CheckOutlined style={{ fontSize: 10 }} /> : i + 1}
                </span>
                {label}
              </div>
            ))}
          </div>

          <div className="panel panel-pad" style={{ minHeight: 300 }}>
            {step === 0 && (
              <>
                {errorMessage && (
                  <div className="error-banner">
                    <strong>上次失败原因：</strong>
                    <div style={{ marginTop: 4 }}>{errorMessage}</div>
                  </div>
                )}
                <div className="download-hero">
                  <div className="detail-empty-glyph">
                    <UploadOutlined />
                  </div>
                  <div style={{ color: 'var(--t2)', fontSize: 12.5 }}>
                    上传包含商品条码的 .xlsx 文件（上限 50MB）
                  </div>
                  <Upload accept=".xlsx" maxCount={1} beforeUpload={handleUpload} showUploadList={false}>
                    <Button type="primary" icon={<UploadOutlined />} loading={loading}>
                      上传 Excel 文件
                    </Button>
                  </Upload>
                </div>
              </>
            )}

            {step === 1 && (
              <div style={{ maxWidth: 420 }}>
                {sheets.length > 1 && (
                  <div style={rowStyle}>
                    <span style={labelStyle}>工作表</span>
                    <Select value={selectedSheet} onChange={(v) => { setSelectedSheet(v); setColumns(sheetColumns[v] || []); setBarcodeColumn(''); }} style={{ width: '100%' }}
                      options={sheets.map(s => ({ value: s, label: s }))} />
                  </div>
                )}
                <div style={rowStyle}>
                  <span style={labelStyle}>条码所在列</span>
                  <Select value={barcodeColumn} onChange={setBarcodeColumn} style={{ width: '100%' }}
                    placeholder="选择条码所在的列"
                    options={columns.map(c => ({ value: c, label: c }))} />
                </div>
                <div style={rowStyle}>
                  <span style={labelStyle}>导出类型</span>
                  <Seg
                    value={imageType}
                    onChange={setImageType}
                    options={[
                      { value: 'all', label: '全部' },
                      { value: 'main', label: '仅主图' },
                      { value: 'detail', label: '仅详情图' },
                    ]}
                  />
                </div>
                <div style={rowStyle}>
                  <span style={labelStyle}>文件夹结构</span>
                  <Seg
                    value={folderMode}
                    onChange={setFolderMode}
                    options={[
                      { value: 'folder', label: '分文件夹' },
                      { value: 'flat', label: '平铺' },
                    ]}
                  />
                </div>
                <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
                  <Button type="primary" onClick={handleGenerate} loading={loading} disabled={!barcodeColumn}>
                    生成 ZIP
                  </Button>
                  <Button onClick={resetWizard}>重新上传</Button>
                </div>
              </div>
            )}

            {step === 2 && (
              <div style={{ maxWidth: 460, margin: '40px auto 0' }}>
                <div className="scan-phase">正在生成压缩包…</div>
                <LightBar value={progressTotal > 0 ? progress / progressTotal : undefined} />
                <div className="scan-meta" style={{ justifyContent: 'center' }}>
                  <span>已写入 <b className="mono">{progress}</b> / <b className="mono">{progressTotal}</b></span>
                </div>
              </div>
            )}

            {step === 3 && (
              <div className="download-hero">
                <div className="dh-num mono">{progressTotal}</div>
                <div style={{ color: 'var(--t2)', fontSize: 12.5 }}>个文件已打包，ZIP 已生成</div>
                <Button icon={<DownloadOutlined />} type="primary" size="large" onClick={handleDownload}>
                  下载 ZIP
                </Button>
                <Button type="link" onClick={resetWizard}>再导一份</Button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
};

export default ExportView;
