import React, { useState, useEffect } from 'react';
import { Switch, Button, Table, Popconfirm, App, Spin, Tooltip } from 'antd';
import { FolderOpenOutlined, DeleteOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { settingsApi, LogFileInfo } from '../services/api';
import { Led } from '../components/ui';

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(timestamp: number): string {
  if (!timestamp) return '-';
  const d = new Date(timestamp * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

const LogsView: React.FC = () => {
  const { message } = App.useApp();
  const [debugMode, setDebugMode] = useState(false);
  const [logDir, setLogDir] = useState('');
  const [logFiles, setLogFiles] = useState<LogFileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [clearing, setClearing] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [debugRes, dirRes] = await Promise.all([
        settingsApi.getDebugMode(),
        settingsApi.getLogDir(),
      ]);
      setDebugMode(debugRes.debug_mode);
      setLogDir(dirRes.log_dir);
      setLogFiles(dirRes.files);
    } catch {
      message.error('加载日志信息失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, []);

  const handleToggleDebug = async (checked: boolean) => {
    setSwitching(true);
    try {
      const res = await settingsApi.setDebugMode(checked);
      setDebugMode(res.debug_mode);
      message.success(res.message);
    } catch {
      message.error('设置调试模式失败');
    } finally {
      setSwitching(false);
    }
  };

  const handleOpenDir = async () => {
    try {
      await settingsApi.openLogDir();
    } catch {
      message.error('打开日志目录失败');
    }
  };

  const handleClearLogs = async () => {
    setClearing(true);
    try {
      const res = await settingsApi.clearLogs();
      if (res.failed.length > 0) {
        message.warning(`已清理 ${res.cleared_count} 个文件，${res.failed.length} 个文件清理失败`);
      } else {
        message.success(`已清理 ${res.cleared_count} 个日志文件`);
      }
      const dirRes = await settingsApi.getLogDir();
      setLogFiles(dirRes.files);
    } catch {
      message.error('清理日志失败');
    } finally {
      setClearing(false);
    }
  };

  const columns = [
    { title: '文件名', dataIndex: 'name',
      render: (v: string) => <span className="mono" style={{ fontSize: 11.5 }}>{v}</span> },
    { title: '大小', dataIndex: 'size', width: 100,
      render: (size: number) => <span className="mono">{formatSize(size)}</span> },
    { title: '最后修改', dataIndex: 'modified', width: 190,
      render: (ts: number) => <span className="mono">{formatTime(ts)}</span> },
  ];

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 80 }}>
        <Spin />
      </div>
    );
  }

  return (
    <div className="logs-grid">
      <div className="panel panel-pad">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <Led color={debugMode ? 'amber' : undefined} />
          <span className="panel-title">调试模式</span>
          <Tooltip title="开启后会记录详细诊断日志，可能影响性能，仅在排查问题时使用。">
            <ExclamationCircleOutlined style={{ color: 'var(--amber)', fontSize: 12 }} />
          </Tooltip>
          <Switch
            checked={debugMode}
            onChange={handleToggleDebug}
            loading={switching}
            checkedChildren="ON"
            unCheckedChildren="OFF"
            style={{ marginLeft: 'auto' }}
          />
        </div>
        <div style={{ fontSize: 12, color: 'var(--t3)' }}>
          开启后会记录详细诊断日志，可能影响性能，仅在排查问题时使用。
        </div>
      </div>

      <div className="panel panel-pad">
        <div className="panel-title" style={{ marginBottom: 10 }}>日志目录</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <code
            className="mono"
            style={{
              fontSize: 11.5,
              padding: '5px 10px',
              background: 'var(--ink-1)',
              border: '1px solid var(--line)',
              borderRadius: 6,
              color: 'var(--t2)',
              wordBreak: 'break-all',
            }}
          >
            {logDir}
          </code>
          <Button icon={<FolderOpenOutlined />} onClick={handleOpenDir} size="small">
            打开日志目录
          </Button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">日志文件</span>
          <span className="hint mono">{logFiles.length} 个文件</span>
          <span style={{ marginLeft: 'auto' }}>
            <Popconfirm
              title="确定清理所有日志文件吗？"
              description="此操作不可撤销。"
              onConfirm={handleClearLogs}
              okText="确定"
              cancelText="取消"
            >
              <Button icon={<DeleteOutlined />} danger loading={clearing} size="small">
                清理日志
              </Button>
            </Popconfirm>
          </span>
        </div>
        <Table
          rowKey="name"
          columns={columns}
          dataSource={logFiles}
          size="small"
          pagination={false}
          locale={{ emptyText: '暂无日志文件' }}
        />
      </div>
    </div>
  );
};

export default LogsView;
