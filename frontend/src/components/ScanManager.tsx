import React, { useState, useEffect } from 'react';
import { Modal, Table, Button, Input, Switch, Space, Popconfirm, message, Tag } from 'antd';
import { PlusOutlined, DeleteOutlined, ScanOutlined, FileTextOutlined } from '@ant-design/icons';
import { ScanRoot, ScanLog, scanRootApi, scanApi, scanLogApi } from '../services/api';

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
  const [showAdd, setShowAdd] = useState(false);
  const [path, setPath] = useState('');
  const [recursive, setRecursive] = useState(true);

  // Log viewer
  const [logVisible, setLogVisible] = useState(false);
  const [logs, setLogs] = useState<ScanLog[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const fetchRoots = () => {
    setLoading(true);
    scanRootApi.list().then(setRoots).finally(() => setLoading(false));
  };

  useEffect(() => { if (visible) fetchRoots(); }, [visible]);

  const handleAdd = async () => {
    if (!path.trim()) return;
    await scanRootApi.create({ path: path.trim(), recursive });
    setPath(''); setShowAdd(false);
    fetchRoots();
    message.success('扫描目录已添加');
  };

  const handleDelete = async (id: number) => {
    await scanRootApi.delete(id);
    fetchRoots();
    message.success('已删除');
  };

  const handleToggle = async (id: number, field: 'recursive' | 'enabled', value: boolean) => {
    await scanRootApi.update(id, { [field]: value });
    fetchRoots();
  };

  const handleScan = async () => {
    setScanning(true);
    try {
      await scanApi.trigger({ allow_fuzzy: true });
      message.success('扫描完成');
      onScanComplete();
    } catch {
      message.error('扫描失败，请查看日志');
    }
    setScanning(false);
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
      <Modal title="扫描目录管理" open={visible} onCancel={onClose} width={700} footer={null}>
        <Space style={{ marginBottom: 12 }}>
          <Button icon={<PlusOutlined />} onClick={() => setShowAdd(!showAdd)}>添加</Button>
          <Button icon={<ScanOutlined />} loading={scanning} onClick={handleScan}>执行扫描</Button>
          <Button icon={<FileTextOutlined />} onClick={fetchLogs}>日志</Button>
        </Space>
        {showAdd && (
          <Space style={{ marginBottom: 12 }}>
            <Input placeholder="文件夹绝对路径" value={path} onChange={e => setPath(e.target.value)} style={{ width: 320 }} />
            <span>递归: <Switch checked={recursive} onChange={setRecursive} /></span>
            <Button type="primary" onClick={handleAdd}>确认</Button>
          </Space>
        )}
        <Table rowKey="id" columns={columns} dataSource={roots} loading={loading} size="small"
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
