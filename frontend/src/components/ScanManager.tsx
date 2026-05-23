import React, { useState, useEffect, useRef } from 'react';
import { Modal, Table, Button, Input, Switch, Select, Space, Popconfirm, message, Tag, Radio, Progress } from 'antd';
import { PlusOutlined, DeleteOutlined, ScanOutlined, FileTextOutlined, LoadingOutlined } from '@ant-design/icons';
import { ScanRoot, ScanLog, ScanJobStatus, scanRootApi, scanApi, scanLogApi } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
  onScanComplete: () => void;
}

const STATUS_COLOR: Record<string, string> = { success: 'green', error: 'red', info: 'blue' };
const ACTION_LABEL: Record<string, string> = { scan: '扫描', add_root: '添加目录', delete_root: '删除目录' };

const ScanManager: React.FC<Props> = ({ visible, onClose, onScanComplete }) => {
  const [roots, setRoots] = useState<ScanRoot[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanJobId, setScanJobId] = useState<string | null>(null);
  const [scanProgress, setScanProgress] = useState<ScanJobStatus | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [path, setPath] = useState('');
  const [recursive, setRecursive] = useState(true);
  const [addAllowFuzzy, setAddAllowFuzzy] = useState(false);
  const [addFuzzyType, setAddFuzzyType] = useState('main');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [scanMode, setScanMode] = useState<'full' | 'incremental'>('full');

  // Log viewer
  const [logVisible, setLogVisible] = useState(false);
  const [logs, setLogs] = useState<ScanLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const fetchRoots = () => {
    setLoading(true);
    scanRootApi.list().then(setRoots).finally(() => setLoading(false));
  };

  useEffect(() => {
    if (visible) {
      fetchRoots();
      setSelectedRowKeys([]);
      setScanMode('full');
      // 页面刷新后恢复扫描进度
      setScanning(false);
      setScanJobId(null);
      setScanProgress(null);
      scanApi.getActive().then(job => {
        if (job && job.status === 'running') {
          setScanning(true);
          setScanJobId(job.job_id);
          setScanProgress(job);
          startPolling(job.job_id);
        }
      }).catch(err => {
        console.warn('无法恢复扫描进度:', err);
      });
    }
  }, [visible]);

  const handleAdd = async () => {
    if (!path.trim()) return;
    await scanRootApi.create({ path: path.trim(), recursive, allow_fuzzy: addAllowFuzzy, fuzzy_image_type: addFuzzyType });
    setPath(''); setShowAdd(false);
    setAddAllowFuzzy(false); setAddFuzzyType('main');
    fetchRoots();
    message.success('扫描目录已添加');
  };

  const handleDelete = async (id: number) => {
    await scanRootApi.delete(id);
    setSelectedRowKeys(prev => prev.filter(k => k !== id));
    fetchRoots();
    message.success('已删除');
  };

  const handleToggle = async (id: number, field: 'recursive' | 'enabled' | 'allow_fuzzy' | 'fuzzy_image_type', value: boolean | string) => {
    await scanRootApi.update(id, { [field]: value });
    fetchRoots();
    if (field === 'enabled') onScanComplete();
  };

  const clearPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  // Start polling for job status
  const startPolling = (jobId: string) => {
    clearPolling();
    pollingRef.current = setInterval(async () => {
      try {
        const status = await scanApi.getStatus(jobId);
        setScanProgress(status);
        if (status.status === 'done') {
          clearPolling();
          setScanning(false);
          setScanJobId(null);
          message.success(`扫描完成: 新增 ${status.added}, 跳过 ${status.skipped}`);
          onScanComplete();
        } else if (status.status === 'error') {
          clearPolling();
          setScanning(false);
          setScanJobId(null);
          message.error(`扫描失败: ${status.error || '未知错误'}`);
        }
      } catch {
        // API error, try again next poll
      }
    }, 1500);
  };

  // Cleanup polling on unmount
  useEffect(() => {
    return () => clearPolling();
  }, []);

  // Cleanup polling when modal closes
  useEffect(() => {
    if (!visible) {
      clearPolling();
    }
  }, [visible]);

  const handleScan = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先勾选要扫描的目录');
      return;
    }
    const rootIds = selectedRowKeys as number[];
    let effectiveMode = scanMode;

    if (scanMode === 'incremental') {
      try {
        const { new_root_ids } = await scanApi.checkNew(rootIds);
        if (new_root_ids.length > 0) {
          const newPaths = roots
            .filter(r => new_root_ids.includes(r.id))
            .map(r => r.path)
            .join('\n');
          message.warning({
            content: `以下目录尚未扫描过，将执行全量扫描：\n${newPaths}`,
            duration: 5,
          });
          effectiveMode = 'full';
        }
      } catch {
        // 检查失败不影响扫描，继续执行
      }
    }

    setScanning(true);
    setScanProgress(null);
    try {
      const result = await scanApi.trigger({
        root_ids: rootIds,
        scan_mode: effectiveMode,
      });
      if (result.job_id) {
        setScanJobId(result.job_id);
        startPolling(result.job_id);
      } else {
        // Legacy: scan completed synchronously (shouldn't happen)
        message.success('扫描完成');
        onScanComplete();
        setScanning(false);
      }
    } catch (err: any) {
      const errMsg = err?.response?.data?.error || err?.message || '扫描失败';
      message.error(errMsg);
      setScanning(false);
    }
  };

  const fetchLogs = () => {
    setLogsLoading(true);
    scanLogApi.list().then(setLogs).finally(() => setLogsLoading(false));
    setLogVisible(true);
  };

  const columns = [
    { title: '路径', dataIndex: 'path', ellipsis: true },
    {
      title: '递归', dataIndex: 'recursive', width: 60,
      render: (v: boolean, r: ScanRoot) => (
        <Switch size="small" checked={v} onChange={(val) => handleToggle(r.id, 'recursive', val)} />
      ),
    },
    {
      title: '启用', dataIndex: 'enabled', width: 60,
      render: (v: boolean, r: ScanRoot) => (
        <Switch size="small" checked={v} onChange={(val) => handleToggle(r.id, 'enabled', val)} />
      ),
    },
    {
      title: '指定类型', dataIndex: 'allow_fuzzy', width: 80,
      render: (v: boolean, r: ScanRoot) => (
        <Switch size="small" checked={v} onChange={(val) => handleToggle(r.id, 'allow_fuzzy', val)} />
      ),
    },
    {
      title: '类型', dataIndex: 'fuzzy_image_type', width: 90,
      render: (v: string, r: ScanRoot) => (
        <Select size="small" value={v || 'main'} style={{ width: 80 }}
          disabled={!r.allow_fuzzy}
          onChange={(val) => handleToggle(r.id, 'fuzzy_image_type', val)}
          options={[
            { value: 'main', label: '主图' },
            { value: 'detail', label: '详情图' },
          ]}
        />
      ),
    },
    {
      title: '操作', width: 80,
      render: (_: unknown, r: ScanRoot) => (
        <Popconfirm title="确定删除此目录及其索引?" onConfirm={() => handleDelete(r.id)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  const logColumns = [
    {
      title: '时间', dataIndex: 'created_at', width: 160,
      render: (t: string) => t?.replace('T', ' ').slice(0, 19),
    },
    {
      title: '操作', dataIndex: 'action', width: 80,
      render: (a: string) => ACTION_LABEL[a] || a,
    },
    {
      title: '状态', dataIndex: 'status', width: 70,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag>,
    },
    { title: '消息', dataIndex: 'message', ellipsis: true },
  ];

  return (
    <>
      <Modal title="扫描目录管理" open={visible} onCancel={onClose} width={900} footer={null}>
        <Space style={{ marginBottom: 12 }}>
          <Button icon={<PlusOutlined />} onClick={() => { setShowAdd(!showAdd); if (!showAdd) { setPath(''); setRecursive(true); setAddAllowFuzzy(false); setAddFuzzyType('main'); } }}>添加</Button>
          <Radio.Group value={scanMode} onChange={e => setScanMode(e.target.value)}>
            <Radio.Button value="full">全量</Radio.Button>
            <Radio.Button value="incremental">增量</Radio.Button>
          </Radio.Group>
          <Button icon={<ScanOutlined />} loading={scanning} onClick={handleScan}>执行扫描</Button>
          <Button icon={<FileTextOutlined />} onClick={fetchLogs}>日志</Button>
        </Space>
        {showAdd && (
          <Space style={{ marginBottom: 12 }}>
            <Input placeholder="文件夹绝对路径" value={path} onChange={e => setPath(e.target.value)} style={{ width: 320 }} />
            <span>递归: <Switch checked={recursive} onChange={setRecursive} /></span>
            <span>指定类型: <Switch checked={addAllowFuzzy} onChange={setAddAllowFuzzy} /></span>
            <Select size="small" value={addFuzzyType} style={{ width: 90 }}
              disabled={!addAllowFuzzy}
              onChange={setAddFuzzyType}
              options={[
                { value: 'main', label: '主图' },
                { value: 'detail', label: '详情图' },
              ]}
            />
            <Button type="primary" onClick={handleAdd}>确认</Button>
          </Space>
        )}

        {scanProgress && (
          <div style={{ marginBottom: 12, padding: '12px', background: '#fafafa', borderRadius: 6 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space>
                <LoadingOutlined spin />
                <span>
                  {scanProgress.phase === 'thumbnails'
                    ? '正在生成缩略图...'
                    : scanProgress.phase === 'versioning'
                    ? '正在更新版本信息...'
                    : scanProgress.status === 'done'
                    ? '扫描完成'
                    : scanProgress.status === 'error'
                    ? '扫描出错'
                    : `正在扫描 (${scanProgress.current_root_index || 0}/${scanProgress.total_roots || 0})`}
                </span>
              </Space>
              {scanProgress.current_root_path && (
                <div style={{ fontSize: 12, color: '#888' }}>目录: {scanProgress.current_root_path}</div>
              )}
              {scanProgress.phase === 'scanning' && scanProgress.current_file && (
                <div style={{ fontSize: 12, color: '#888' }}>当前文件: {scanProgress.current_file}</div>
              )}
              <div style={{ fontSize: 12 }}>
                新增 {scanProgress.added} | 跳过 {scanProgress.skipped} | 清理 {scanProgress.broken_cleaned}
              </div>
              {scanProgress.phase === 'thumbnails' && scanProgress.thumbnail_total > 0 && (
                <Progress
                  percent={Math.round((scanProgress.thumbnail_current / scanProgress.thumbnail_total) * 100)}
                  size="small"
                  format={() => `${scanProgress.thumbnail_current}/${scanProgress.thumbnail_total}`}
                />
              )}
              {scanProgress.phase !== 'thumbnails' && scanProgress.status === 'running' && (
                <Progress percent={99} size="small" status="active" showInfo={false} />
              )}
              {scanProgress.status === 'done' && (
                <Progress percent={100} size="small" status="success" showInfo={false} />
              )}
              {scanProgress.status === 'error' && (
                <div style={{ color: 'red', fontSize: 12 }}>错误: {scanProgress.error}</div>
              )}
            </Space>
          </div>
        )}
        <Table rowKey="id" columns={columns} dataSource={roots} loading={loading} size="small"
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys),
          }}
          pagination={false} scroll={{ y: 300 }} />
      </Modal>

      <Modal title="扫描日志" open={logVisible} onCancel={() => setLogVisible(false)} width={700} footer={null}>
        <Table rowKey="id" columns={logColumns} dataSource={logs} loading={logsLoading} size="small"
          pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
          expandable={{
            expandedRowRender: (r: ScanLog) => r.details ? <pre style={{ margin: 0, fontSize: 12 }}>{(() => { try { return JSON.stringify(JSON.parse(r.details), null, 2); } catch { return r.details; } })()}</pre> : null,
            rowExpandable: (r: ScanLog) => !!r.details,
          }}
          scroll={{ y: 400 }} />
      </Modal>
    </>
  );
};

export default ScanManager;
