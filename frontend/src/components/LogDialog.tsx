import React, { useState, useEffect } from 'react';
import { Modal, Switch, Button, Table, Space, Typography, Popconfirm, message, Spin, Tooltip } from 'antd';
import { FolderOpenOutlined, DeleteOutlined, ExclamationCircleOutlined } from '@ant-design/icons';
import { settingsApi, LogFileInfo } from '../services/api';

const { Text } = Typography;

interface Props {
  visible: boolean;
  onClose: () => void;
}

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

const LogDialog: React.FC<Props> = ({ visible, onClose }) => {
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

  useEffect(() => {
    if (visible) {
      fetchData();
    }
  }, [visible]);

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
      // Refresh file list
      const dirRes = await settingsApi.getLogDir();
      setLogFiles(dirRes.files);
    } catch {
      message.error('清理日志失败');
    } finally {
      setClearing(false);
    }
  };

  const columns = [
    {
      title: '文件名',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 100,
      render: (size: number) => formatSize(size),
    },
    {
      title: '最后修改',
      dataIndex: 'modified',
      key: 'modified',
      width: 180,
      render: (ts: number) => formatTime(ts),
    },
  ];

  return (
    <Modal
      title="日志管理"
      open={visible}
      onCancel={onClose}
      width={600}
      footer={null}
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          {/* Debug Mode Section */}
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500, fontSize: 14 }}>
              <Tooltip title="开启后会记录详细诊断日志，可能影响性能，仅在排查问题时使用。">
                <ExclamationCircleOutlined style={{ marginRight: 6, color: '#faad14' }} />
              </Tooltip>
              调试模式
              <Switch
                checked={debugMode}
                onChange={handleToggleDebug}
                loading={switching}
                style={{ marginLeft: 12 }}
                checkedChildren="ON"
                unCheckedChildren="OFF"
              />
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              开启后会记录详细诊断日志，可能影响性能，仅在排查问题时使用。
            </Text>
          </div>

          {/* Log Directory Section */}
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500, fontSize: 14 }}>日志目录</div>
            <Space>
              <Text code copyable style={{ fontSize: 13 }}>{logDir}</Text>
              <Button
                icon={<FolderOpenOutlined />}
                onClick={handleOpenDir}
                size="small"
              >
                打开日志目录
              </Button>
            </Space>
          </div>

          {/* Log Files Section */}
          <div>
            <div style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontWeight: 500, fontSize: 14 }}>日志文件</span>
              <Popconfirm
                title="确定清理所有日志文件吗？"
                description="此操作不可撤销。"
                onConfirm={handleClearLogs}
                okText="确定"
                cancelText="取消"
              >
                <Button
                  icon={<DeleteOutlined />}
                  danger
                  loading={clearing}
                  size="small"
                >
                  清理日志
                </Button>
              </Popconfirm>
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
        </Space>
      )}
    </Modal>
  );
};

export default LogDialog;
