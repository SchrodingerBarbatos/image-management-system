import React, { useState, useEffect, useRef } from 'react';
import { Table, Button, Input, Switch, Select, Popconfirm, App, Tag } from 'antd';
import {
  PlusOutlined, DeleteOutlined, ScanOutlined, CloseCircleOutlined,
  ReloadOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import { ScanRoot, ScanLog, ScanJobStatus, ScanHistoryRecord, scanRootApi, scanApi, scanLogApi } from '../services/api';
import { fmtEta } from '../utils/format';
import { Seg, Led, LightBar, EmptyBlock } from '../components/ui';
import RejectedPanel from '../components/RejectedPanel';

interface Props {
  onScanComplete: () => void;
}

const STATUS_COLOR: Record<string, string> = { success: 'green', error: 'red', info: 'blue' };
const ACTION_LABEL: Record<string, string> = { scan: '扫描', add_root: '添加目录', delete_root: '删除目录' };

type TabKey = 'roots' | 'logs' | 'history' | 'rejected';

const ScanView: React.FC<Props> = ({ onScanComplete }) => {
  const { message } = App.useApp();
  const [tab, setTab] = useState<TabKey>('roots');
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

  const [logs, setLogs] = useState<ScanLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsLoaded, setLogsLoaded] = useState(false);
  const [history, setHistory] = useState<ScanHistoryRecord[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);

  const fetchRoots = () => {
    setLoading(true);
    scanRootApi.list().then(setRoots).finally(() => setLoading(false));
  };

  const clearPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

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

  // 挂载时加载目录并恢复进行中的扫描（页面刷新 / 切换视图后回来）
  useEffect(() => {
    fetchRoots();
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
    return () => clearPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 切到日志 / 记录页签时按需加载
  useEffect(() => {
    if (tab === 'logs' && !logsLoaded) {
      setLogsLoading(true);
      scanLogApi.list().then(setLogs).finally(() => { setLogsLoading(false); setLogsLoaded(true); });
    }
    if (tab === 'history' && !historyLoaded) {
      scanApi.getHistory().then(setHistory).catch(() => {}).finally(() => setHistoryLoaded(true));
    }
  }, [tab, logsLoaded, historyLoaded]);

  const refreshLogs = () => {
    setLogsLoading(true);
    scanLogApi.list().then(setLogs).finally(() => setLogsLoading(false));
  };

  const refreshHistory = () => {
    scanApi.getHistory().then(setHistory).catch(() => {});
  };

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

  const handleCancelScan = async () => {
    if (!scanJobId) return;
    try {
      await scanApi.cancel(scanJobId);
      message.info('取消请求已发送');
    } catch (err: any) {
      message.error(err?.response?.data?.error || '取消失败');
    }
  };

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

  const columns = [
    { title: '路径', dataIndex: 'path', ellipsis: true,
      render: (v: string) => <span className="mono" style={{ fontSize: 11.5 }}>{v}</span> },
    {
      title: '递归', dataIndex: 'recursive', width: 64,
      render: (v: boolean, r: ScanRoot) => (
        <Switch size="small" checked={v} onChange={(val) => handleToggle(r.id, 'recursive', val)} />
      ),
    },
    {
      title: '启用', dataIndex: 'enabled', width: 64,
      render: (v: boolean, r: ScanRoot) => (
        <Switch size="small" checked={v} onChange={(val) => handleToggle(r.id, 'enabled', val)} />
      ),
    },
    {
      title: '指定类型', dataIndex: 'allow_fuzzy', width: 90,
      render: (v: boolean, r: ScanRoot) => (
        <Switch size="small" checked={v} onChange={(val) => handleToggle(r.id, 'allow_fuzzy', val)} />
      ),
    },
    {
      title: '类型', dataIndex: 'fuzzy_image_type', width: 100,
      render: (v: string, r: ScanRoot) => (
        <Select size="small" value={v || 'main'} style={{ width: 84 }}
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
      title: '操作', width: 70,
      render: (_: unknown, r: ScanRoot) => (
        <Popconfirm title="确定删除此目录及其索引?" onConfirm={() => handleDelete(r.id)}>
          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  const logColumns = [
    {
      title: '时间', dataIndex: 'created_at', width: 170,
      render: (t: string) => <span className="mono">{t?.replace('T', ' ').slice(0, 19)}</span>,
    },
    { title: '操作', dataIndex: 'action', width: 90, render: (a: string) => ACTION_LABEL[a] || a },
    {
      title: '状态', dataIndex: 'status', width: 80,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag>,
    },
    { title: '消息', dataIndex: 'message', ellipsis: true },
  ];

  const historyColumns = [
    {
      title: '时间', dataIndex: 'started_at', width: 170,
      render: (t: string) => <span className="mono">{t?.replace('T', ' ').slice(0, 19)}</span>,
    },
    {
      title: '模式', dataIndex: 'scan_mode', width: 80,
      render: (m: string) => m === 'full' ? '全量' : '增量',
    },
    { title: '新增', dataIndex: 'added', width: 80, render: (v: number) => <span className="mono">{v}</span> },
    { title: '跳过', dataIndex: 'skipped', width: 80, render: (v: number) => <span className="mono">{v}</span> },
    { title: '拒绝', dataIndex: 'rejected', width: 70, render: (v: number) => <span className="mono">{v}</span> },
    { title: '清理', dataIndex: 'broken_cleaned', width: 70, render: (v: number) => <span className="mono">{v}</span> },
    {
      title: '耗时', dataIndex: 'elapsed_seconds', width: 100,
      render: (s: number) => <span className="mono">{s > 0 ? fmtEta(s) : '-'}</span>,
    },
  ];

  /* ---------- 扫描进度卡片 ---------- */
  const renderProgress = () => {
    const sp = scanProgress;
    if (!sp) return null;
    const phaseText = sp.cancel_requested && sp.status === 'running'
      ? '正在取消…'
      : sp.phase === 'counting'
        ? '阶段 1/3 · 统计文件'
        : sp.phase === 'thumbnails'
          ? '阶段 2/3 · 生成缩略图'
          : sp.phase === 'versioning'
            ? '阶段 3/3 · 更新版本'
            : sp.status === 'done'
              ? '扫描完成'
              : sp.status === 'cancelled'
                ? '扫描已取消'
                : sp.status === 'error'
                  ? '扫描出错'
                  : `阶段 2/3 · 扫描文件（目录 ${sp.current_root_index || 0}/${sp.total_roots || 0}）`;

    let percent = sp.percent || 0;
    if (sp.phase === 'counting') {
      percent = 0;
    } else if (sp.phase === 'thumbnails' && sp.thumbnail_total > 0) {
      percent = Math.round((sp.thumbnail_current / sp.thumbnail_total) * 100);
    } else if (sp.phase === 'versioning' && (sp.versioning_total || 0) > 0) {
      percent = Math.round(((sp.versioning_current || 0) / (sp.versioning_total || 1)) * 100);
    } else if (sp.status === 'done') {
      percent = 100;
    }

    const ledColor = sp.status === 'error' ? 'red' : sp.status === 'done' ? 'green' : 'blue';
    const barState = sp.status === 'error' ? 'error' : sp.status === 'done' ? 'done' : 'active';

    return (
      <div className="scan-progress-card">
        <div className="scan-phase">
          <Led color={ledColor} />
          {phaseText}
        </div>
        <LightBar
          value={sp.phase === 'counting' ? undefined : percent / 100}
          state={barState}
        />
        <div className="scan-meta">
          {sp.phase === 'counting' && (
            <>
              <span>已发现图片 <b>{sp.counted_files || 0}</b></span>
              {sp.counting_current_dir && <span>当前目录 <b>{sp.counting_current_dir}</b></span>}
              {sp.counting_total_roots > 1 && (
                <span>目录 <b>{sp.counting_root_index || 0} / {sp.counting_total_roots}</b></span>
              )}
            </>
          )}
          {(sp.phase === 'scanning' || sp.phase === 'thumbnails') && (sp.current_dir || sp.current_root_path) && (
            <span>目录 <b>{sp.current_dir || sp.current_root_path}</b></span>
          )}
          {sp.phase === 'scanning' && sp.current_file && <span>文件 <b>{sp.current_file}</b></span>}
          {sp.phase === 'scanning' && sp.total_files > 0 && (
            <span>进度 <b>{sp.processed_files || 0} / {sp.total_files}</b></span>
          )}
          {sp.phase !== 'counting' && (
            <span>新增 <b>{sp.added}</b> · 跳过 <b>{sp.skipped}</b> · 清理 <b>{sp.broken_cleaned}</b> · 拒绝 <b>{sp.rejected}</b></span>
          )}
          {sp.phase === 'scanning' && sp.speed > 0 && (
            <span>速度 <b>{sp.speed}/s</b>{sp.eta_seconds > 0 && <> · 剩余 <b>{fmtEta(sp.eta_seconds)}</b></>}</span>
          )}
          {sp.phase === 'thumbnails' && sp.thumbnail_total > 0 && (
            <span>缩略图 <b>{sp.thumbnail_current}/{sp.thumbnail_total}</b></span>
          )}
          {sp.phase === 'versioning' && (sp.versioning_total || 0) > 0 && (
            <span>版本更新 <b>{sp.versioning_current || 0}/{sp.versioning_total || 0}</b></span>
          )}
          {(sp.status === 'done' || sp.status === 'cancelled') && sp.elapsed_seconds > 0 && (
            <span>耗时 <b>{fmtEta(sp.elapsed_seconds)}</b></span>
          )}
          {sp.status === 'error' && <span style={{ color: 'var(--red)' }}>错误 {sp.error}</span>}
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="view-head">
        <div className="view-sub">管理图片源文件夹，执行全量 / 增量扫描入库</div>
        <div className="view-head-right">
          <Seg
            value={tab}
            onChange={setTab}
            options={[
              { value: 'roots', label: '目录' },
              { value: 'logs', label: '日志' },
              { value: 'history', label: '扫描记录' },
              { value: 'rejected', label: '非标品' },
            ]}
          />
        </div>
      </div>

      {renderProgress()}

      {tab === 'roots' && (
        <>
          <div className="toolbar">
            <Button icon={<PlusOutlined />} onClick={() => { setShowAdd(!showAdd); if (!showAdd) { setPath(''); setRecursive(true); setAddAllowFuzzy(false); setAddFuzzyType('main'); } }}>
              添加目录
            </Button>
            <Seg
              value={scanMode}
              onChange={setScanMode}
              options={[
                { value: 'full', label: '全量' },
                { value: 'incremental', label: '增量' },
              ]}
            />
            <Button type="primary" icon={<ScanOutlined />} loading={scanning} onClick={handleScan}>
              执行扫描
            </Button>
            {scanning && scanJobId && (
              <Button icon={<CloseCircleOutlined />} danger onClick={handleCancelScan}>取消扫描</Button>
            )}
          </div>

          {showAdd && (
            <div className="panel panel-pad" style={{ marginBottom: 14, animation: 'card-pop .3s var(--e1)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                <Input
                  placeholder="文件夹绝对路径"
                  value={path}
                  onChange={e => setPath(e.target.value)}
                  style={{ width: 340 }}
                  className="mono"
                />
                <span style={{ fontSize: 12, color: 'var(--t2)' }}>
                  递归 <Switch size="small" checked={recursive} onChange={setRecursive} style={{ marginLeft: 6 }} />
                </span>
                <span style={{ fontSize: 12, color: 'var(--t2)' }}>
                  指定类型 <Switch size="small" checked={addAllowFuzzy} onChange={setAddAllowFuzzy} style={{ marginLeft: 6 }} />
                </span>
                <Select size="small" value={addFuzzyType} style={{ width: 90 }}
                  disabled={!addAllowFuzzy}
                  onChange={setAddFuzzyType}
                  options={[
                    { value: 'main', label: '主图' },
                    { value: 'detail', label: '详情图' },
                  ]}
                />
                <Button type="primary" onClick={handleAdd}>确认添加</Button>
              </div>
            </div>
          )}

          <div className="panel">
            <Table
              rowKey="id"
              columns={columns}
              dataSource={roots}
              loading={loading}
              size="small"
              rowSelection={{ selectedRowKeys, onChange: (keys) => setSelectedRowKeys(keys) }}
              pagination={false}
            />
          </div>
        </>
      )}

      {tab === 'logs' && (
        <>
          <div className="toolbar">
            <Button icon={<ReloadOutlined />} onClick={refreshLogs} loading={logsLoading}>刷新</Button>
          </div>
          <div className="panel">
            <Table
              rowKey="id"
              columns={logColumns}
              dataSource={logs}
              loading={logsLoading}
              size="small"
              pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
              expandable={{
                expandedRowRender: (r: ScanLog) => r.details ? (
                  <pre className="mono" style={{ margin: 0, fontSize: 11 }}>
                    {(() => { try { return JSON.stringify(JSON.parse(r.details), null, 2); } catch { return r.details; } })()}
                  </pre>
                ) : null,
                rowExpandable: (r: ScanLog) => !!r.details,
              }}
            />
          </div>
        </>
      )}

      {tab === 'history' && (
        <>
          <div className="toolbar">
            <Button icon={<ReloadOutlined />} onClick={refreshHistory}>刷新</Button>
          </div>
          <div className="panel">
            {historyLoaded && history.length === 0 ? (
              <EmptyBlock icon={<CheckCircleOutlined />} text="暂无扫描记录" />
            ) : (
              <Table
                rowKey={(r, i) => `${r.started_at}-${i}`}
                dataSource={history}
                size="small"
                pagination={false}
                columns={historyColumns}
                loading={!historyLoaded}
              />
            )}
          </div>
        </>
      )}

      {tab === 'rejected' && (
        <div className="panel panel-pad">
          <RejectedPanel />
        </div>
      )}
    </>
  );
};

export default ScanView;
