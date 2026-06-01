import React, { useState, useEffect, useRef } from 'react';
import { Modal, Table, Button, Input, Switch, Select, Space, Popconfirm, message, Tag, Radio, Progress } from 'antd';
import { PlusOutlined, DeleteOutlined, ScanOutlined, FileTextOutlined, LoadingOutlined, ExclamationCircleOutlined, CloseCircleOutlined, HistoryOutlined } from '@ant-design/icons';
import { ScanRoot, ScanLog, ScanJobStatus, ScanHistoryRecord, scanRootApi, scanApi, scanLogApi } from '../services/api';
import { fmtEta } from '../utils/format';
import RejectedBarcodes from './RejectedBarcodes';

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

  // Rejected barcodes viewer
  const [rejectedVisible, setRejectedVisible] = useState(false);

  // Scan history
  const [historyVisible, setHistoryVisible] = useState(false);
  const [history, setHistory] = useState<ScanHistoryRecord[]>([]);

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

  // Cancel scan handler
  const handleCancelScan = async () => {
    if (!scanJobId) return;
    try {
      await scanApi.cancel(scanJobId);
      message.info('取消请求已发送');
    } catch (err: any) {
      message.error(err?.response?.data?.error || '取消失败');
    }
  };

  // Fetch scan history
  const fetchHistory = () => {
    scanApi.getHistory().then(setHistory).catch(() => {});
    setHistoryVisible(true);
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
          const elapsed = status.elapsed_seconds ? `，耗时 ${fmtEta(status.elapsed_seconds)}` : '';
          message.success(`扫描完成: 新增 ${status.added}, 跳过 ${status.skipped}${elapsed}`);
          onScanComplete();
        } else if (status.status === 'cancelled') {
          clearPolling();
          setScanning(false);
          setScanJobId(null);
          message.info('扫描已取消');
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
          {scanning && scanJobId && (
            <Button icon={<CloseCircleOutlined />} danger onClick={handleCancelScan}>取消扫描</Button>
          )}
          <Button icon={<FileTextOutlined />} onClick={fetchLogs}>日志</Button>
          <Button icon={<HistoryOutlined />} onClick={fetchHistory}>扫描记录</Button>
          <Button icon={<ExclamationCircleOutlined />} onClick={() => setRejectedVisible(true)}>非标品记录</Button>
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

        {scanProgress && (() => {
          const sp = scanProgress;
          const phaseText = sp.cancel_requested && sp.status === 'running'
            ? '正在取消...'
            : sp.phase === 'counting'
            ? '阶段1/3 统计文件'
            : sp.phase === 'thumbnails'
            ? '阶段2/3 生成缩略图'
            : sp.phase === 'versioning'
            ? '阶段3/3 更新版本'
            : sp.status === 'done'
            ? '扫描完成'
            : sp.status === 'cancelled'
            ? '扫描已取消'
            : sp.status === 'error'
            ? '扫描出错'
            : `阶段2/3 扫描文件 (目录 ${sp.current_root_index || 0}/${sp.total_roots || 0})`;

          // 进度百分比：仅 scanning/thumbnails/versioning 使用真实百分比
          let percent = sp.percent || 0;
          if (sp.phase === 'counting') {
            percent = 0; // counting 阶段不显示假百分比
          } else if (sp.phase === 'thumbnails' && sp.thumbnail_total > 0) {
            percent = Math.round((sp.thumbnail_current / sp.thumbnail_total) * 100);
          } else if (sp.phase === 'versioning' && (sp.versioning_total || 0) > 0) {
            percent = Math.round(((sp.versioning_current || 0) / (sp.versioning_total || 1)) * 100);
          } else if (sp.status === 'done') {
            percent = 100;
          }

          return (
            <div style={{ marginBottom: 12, padding: '12px', background: '#fafafa', borderRadius: 6 }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Space>
                  {sp.status === 'running' && <LoadingOutlined spin />}
                  <span>{phaseText}</span>
                </Space>

                {/* 进度条 */}
                <Progress
                  percent={percent}
                  size="small"
                  status={sp.status === 'error' ? 'exception' : sp.status === 'done' ? 'success' : sp.status === 'cancelled' ? 'exception' : 'active'}
                  showInfo={sp.phase === 'counting' ? false : undefined}
                  format={() => sp.phase === 'counting'
                    ? ''
                    : sp.total_files > 0 && sp.status === 'running'
                    ? `${sp.processed_files || 0} / ${sp.total_files}`
                    : `${percent}%`}
                />

                {/* counting 阶段实时反馈 */}
                {sp.phase === 'counting' && (
                  <>
                    <div style={{ fontSize: 12, color: '#888' }}>
                      已发现图片: {sp.counted_files || 0}
                    </div>
                    {(sp.counting_current_dir || sp.counting_root_index > 0) && (
                      <>
                        {sp.counting_current_dir && (
                          <div style={{ fontSize: 12, color: '#888' }}>当前目录: {sp.counting_current_dir}</div>
                        )}
                        {sp.counting_total_roots > 1 && (
                          <div style={{ fontSize: 12, color: '#888' }}>
                            目录: {sp.counting_root_index || 0} / {sp.counting_total_roots}
                          </div>
                        )}
                      </>
                    )}
                  </>
                )}

                {/* 当前目录（scanning/thumbnails 阶段） */}
                {(sp.phase === 'scanning' || sp.phase === 'thumbnails') && (sp.current_dir || sp.current_root_path) && (
                  <div style={{ fontSize: 12, color: '#888' }}>目录: {sp.current_dir || sp.current_root_path}</div>
                )}

                {/* 当前文件 */}
                {sp.phase === 'scanning' && sp.current_file && (
                  <div style={{ fontSize: 12, color: '#888' }}>文件: {sp.current_file}</div>
                )}

                {/* 统计信息 */}
                {sp.phase !== 'counting' && (
                  <div style={{ fontSize: 12 }}>
                    新增 {sp.added} | 跳过 {sp.skipped} | 清理 {sp.broken_cleaned} | 拒绝 {sp.rejected}
                  </div>
                )}

                {/* 速度和 ETA（仅扫描阶段） */}
                {sp.phase === 'scanning' && sp.speed > 0 && (
                  <div style={{ fontSize: 12, color: '#888' }}>
                    速度: {sp.speed} 文件/秒
                    {sp.eta_seconds > 0 && ` | 预计剩余: ${fmtEta(sp.eta_seconds)}`}
                  </div>
                )}

                {/* 缩略图进度详情 */}
                {sp.phase === 'thumbnails' && sp.thumbnail_total > 0 && (
                  <div style={{ fontSize: 12, color: '#888' }}>
                    缩略图: {sp.thumbnail_current}/{sp.thumbnail_total}
                  </div>
                )}

                {/* 版本更新进度详情 */}
                {sp.phase === 'versioning' && (sp.versioning_total || 0) > 0 && (
                  <div style={{ fontSize: 12, color: '#888' }}>
                    版本更新: {sp.versioning_current || 0}/{sp.versioning_total || 0}
                  </div>
                )}

                {/* 耗时 */}
                {(sp.status === 'done' || sp.status === 'cancelled') && sp.elapsed_seconds > 0 && (
                  <div style={{ fontSize: 12, color: '#888' }}>
                    耗时: {fmtEta(sp.elapsed_seconds)}
                  </div>
                )}

                {sp.status === 'error' && (
                  <div style={{ color: 'red', fontSize: 12 }}>错误: {sp.error}</div>
                )}
              </Space>
            </div>
          );
        })()}
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

      <RejectedBarcodes
        visible={rejectedVisible}
        onClose={() => setRejectedVisible(false)}
      />

      <Modal title="最近扫描记录" open={historyVisible} onCancel={() => setHistoryVisible(false)} width={700} footer={null}>
        {history.length === 0 ? (
          <div style={{ color: '#999', textAlign: 'center', padding: 20 }}>暂无扫描记录</div>
        ) : (
          <Table
            rowKey={(r, i) => `${r.started_at}-${i}`}
            dataSource={history}
            size="small"
            pagination={false}
            columns={[
              {
                title: '时间', dataIndex: 'started_at', width: 160,
                render: (t: string) => t?.replace('T', ' ').slice(0, 19),
              },
              {
                title: '模式', dataIndex: 'scan_mode', width: 70,
                render: (m: string) => m === 'full' ? '全量' : '增量',
              },
              { title: '新增', dataIndex: 'added', width: 70 },
              { title: '跳过', dataIndex: 'skipped', width: 70 },
              { title: '拒绝', dataIndex: 'rejected', width: 60 },
              { title: '清理', dataIndex: 'broken_cleaned', width: 60 },
              {
                title: '耗时', dataIndex: 'elapsed_seconds', width: 80,
                render: (s: number) => s > 0 ? fmtEta(s) : '-',
              },
            ]}
          />
        )}
      </Modal>
    </>
  );
};

export default ScanManager;
