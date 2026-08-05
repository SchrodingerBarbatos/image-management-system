import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Switch, Button, Table, Popconfirm, App, Spin, Tooltip, Input, Tag, Alert } from 'antd';
import {
  FolderOpenOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  GlobalOutlined,
  KeyOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import {
  settingsApi,
  LogFileInfo,
  readStoredApiToken,
  writeStoredApiToken,
} from '../services/api';
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
  const [lanMode, setLanMode] = useState(false);
  const [savedLanMode, setSavedLanMode] = useState(false);
  const [tokenConfigured, setTokenConfigured] = useState(false);
  const [browserTokenConfigured, setBrowserTokenConfigured] = useState(
    () => Boolean(readStoredApiToken()),
  );
  const [tokenInput, setTokenInput] = useState('');
  const [restartRequired, setRestartRequired] = useState(false);
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [networkSaving, setNetworkSaving] = useState(false);
  const [clearing, setClearing] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [networkRes, debugRes, dirRes] = await Promise.all([
        settingsApi.getNetworkSettings(),
        settingsApi.getDebugMode(),
        settingsApi.getLogDir(),
      ]);
      setLanMode(networkRes.lan_mode);
      setSavedLanMode(networkRes.lan_mode);
      setTokenConfigured(networkRes.api_token_configured);
      setRestartRequired(networkRes.restart_required);
      setDebugMode(debugRes.debug_mode);
      setLogDir(dirRes.log_dir);
      setLogFiles(dirRes.files);
    } catch {
      message.error('加载系统设置信息失败');
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

  const generateSecureToken = () => {
    if (!window.crypto?.getRandomValues) {
      message.error('当前浏览器不支持安全 Token 生成，请手动输入至少 16 位随机字符');
      return;
    }
    const bytes = new Uint8Array(24);
    window.crypto.getRandomValues(bytes);
    setTokenInput(
      Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join(''),
    );
    message.success('已生成安全 Token，保存前请妥善记录');
  };

  const handleSaveNetwork = async () => {
    const token = tokenInput.trim();
    if (token && token.length < 16) {
      message.error('API Token 至少需要 16 个字符');
      return;
    }
    const storedToken = readStoredApiToken();
    let browserTokenSaved = true;
    setNetworkSaving(true);
    try {
      const res = await settingsApi.setNetworkSettings(
        {
          lan_mode: lanMode,
          ...(token ? { api_token: token } : {}),
        },
        storedToken || token || undefined,
      );
      if (token) {
        browserTokenSaved = writeStoredApiToken(token);
        setBrowserTokenConfigured(browserTokenSaved);
        if (browserTokenSaved) setTokenInput('');
      }
      setLanMode(res.lan_mode);
      setSavedLanMode(res.lan_mode);
      setTokenConfigured(res.api_token_configured);
      setRestartRequired(res.restart_required);
      if (token && !browserTokenSaved) {
        message.warning('服务器设置已保存，但当前浏览器无法保存 Token；请先复制输入值并检查浏览器存储权限');
      } else {
        message.success(res.message || '网络设置已保存');
      }
    } catch (error) {
      const detail = axios.isAxiosError<{ error?: string }>(error)
        ? error.response?.data?.error
        : undefined;
      message.error(detail || '保存网络设置失败');
    } finally {
      setNetworkSaving(false);
    }
  };

  const handleForgetBrowserToken = () => {
    if (!writeStoredApiToken('')) {
      message.error('无法清除当前浏览器 Token，请检查浏览器存储权限');
      return;
    }
    setBrowserTokenConfigured(false);
    setTokenInput('');
    message.success('已清除当前浏览器保存的 Token，服务器配置未改变');
  };

  const handleClearServerToken = async () => {
    const storedToken = readStoredApiToken();
    const typedToken = tokenInput.trim();
    setNetworkSaving(true);
    try {
      const res = await settingsApi.setNetworkSettings(
        { lan_mode: lanMode, api_token: '' },
        storedToken || typedToken || undefined,
      );
      writeStoredApiToken('');
      setBrowserTokenConfigured(false);
      setTokenInput('');
      setLanMode(res.lan_mode);
      setSavedLanMode(res.lan_mode);
      setTokenConfigured(false);
      setRestartRequired(res.restart_required);
      message.success('服务器 API Token 已清除，修改操作不再鉴权');
    } catch (error) {
      const detail = axios.isAxiosError<{ error?: string }>(error)
        ? error.response?.data?.error
        : undefined;
      message.error(detail || '清除服务器 API Token 失败');
    } finally {
      setNetworkSaving(false);
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

  const networkDirty = lanMode !== savedLanMode;

  return (
    <div className="logs-grid">
      <div className="panel">
        <div className="panel-head">
          <GlobalOutlined style={{ color: lanMode ? 'var(--green)' : 'var(--t3)' }} />
          <span className="panel-title">局域网访问</span>
          <Tag color={networkDirty ? 'processing' : lanMode ? 'success' : 'default'}>
            {networkDirty
              ? lanMode ? '待保存开启' : '待保存关闭'
              : lanMode ? '已配置开启' : '仅本机访问'}
          </Tag>
          <Switch
            checked={lanMode}
            onChange={setLanMode}
            checkedChildren="ON"
            unCheckedChildren="OFF"
            style={{ marginLeft: 'auto' }}
          />
        </div>
        <div className="panel-pad network-settings">
          <Alert
            showIcon
            type={lanMode && !tokenConfigured ? 'error' : lanMode ? 'warning' : 'info'}
            message={lanMode
              ? tokenConfigured
                ? '局域网访问已配置；修改操作必须携带 API Token。'
                : '局域网访问将不启用鉴权，局域网内设备可直接执行增删改操作。'
              : '当前仅允许本机通过 127.0.0.1 访问。'}
            description="监听地址和 Windows 防火墙规则会在应用重启时更新。"
          />

          <div className="network-token-row">
            <div className="network-token-copy">
              <div className="setting-label">
                <KeyOutlined />
                API Token（可选）
                <Tag color={tokenConfigured ? 'success' : 'warning'}>
                  {tokenConfigured ? '已启用鉴权' : '未启用鉴权'}
                </Tag>
                <Tag color={browserTokenConfigured ? 'blue' : 'default'}>
                  {browserTokenConfigured ? '本浏览器已保存' : '本浏览器未保存'}
                </Tag>
              </div>
              <div className="setting-description">
                可留空开放访问；已配置时留空会保留服务器 Token。输入新值可设置或替换，并仅在当前浏览器保存一份用于请求鉴权。
              </div>
            </div>
            <Input.Password
              value={tokenInput}
              onChange={(event) => setTokenInput(event.target.value)}
              placeholder={tokenConfigured ? '留空以保留现有 Token' : '可留空；如需鉴权请输入至少 16 位'}
              autoComplete="new-password"
              maxLength={256}
              prefix={<SafetyCertificateOutlined />}
            />
          </div>

          <div className="network-actions">
            <Button onClick={generateSecureToken}>生成安全 Token</Button>
            {browserTokenConfigured && (
              <Popconfirm
                title="清除当前浏览器保存的 Token？"
                description="服务器上的 Token 不会改变；清除后需要重新输入才能执行修改操作。"
                onConfirm={handleForgetBrowserToken}
                okText="清除"
                cancelText="取消"
              >
                <Button>清除本浏览器 Token</Button>
              </Popconfirm>
            )}
            {tokenConfigured && (
              <Popconfirm
                title="清除服务器 API Token？"
                description="清除后所有修改操作都不再鉴权，局域网设备可直接操作。"
                onConfirm={handleClearServerToken}
                okText="清除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
              >
                <Button danger loading={networkSaving}>清除服务器 Token</Button>
              </Popconfirm>
            )}
            <Button type="primary" loading={networkSaving} onClick={handleSaveNetwork}>
              保存网络设置
            </Button>
          </div>

          {restartRequired && (
            <Alert
              showIcon
              type="warning"
              message="网络访问范围已改变，请重启应用后生效"
            />
          )}
        </div>
      </div>

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
