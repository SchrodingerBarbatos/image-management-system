import React, { useState, useEffect } from 'react';
import { Modal, Table, Button, Form, Input, Switch, Space, Popconfirm, message } from 'antd';
import { PlusOutlined, DeleteOutlined, ScanOutlined } from '@ant-design/icons';
import { ScanRoot, scanRootApi, scanApi } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
  onScanComplete: () => void;
}

const ScanManager: React.FC<Props> = ({ visible, onClose, onScanComplete }) => {
  const [roots, setRoots] = useState<ScanRoot[]>([]);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [path, setPath] = useState('');
  const [recursive, setRecursive] = useState(true);

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

  const handleScan = async () => {
    setScanning(true);
    await scanApi.trigger({ allow_fuzzy: true });
    setScanning(false);
    message.success('扫描完成');
    onScanComplete();
  };

  const columns = [
    { title: '路径', dataIndex: 'path', ellipsis: true },
    { title: '递归', dataIndex: 'recursive', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '启用', dataIndex: 'enabled', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '操作', width: 80, render: (_: unknown, r: ScanRoot) => (
      <Popconfirm title="确定删除此目录及其索引?" onConfirm={() => handleDelete(r.id)}>
        <Button size="small" danger icon={<DeleteOutlined />} />
      </Popconfirm>
    )},
  ];

  return (
    <Modal title="扫描目录管理" open={visible} onCancel={onClose} width={700} footer={null}>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<PlusOutlined />} onClick={() => setShowAdd(!showAdd)}>添加</Button>
        <Button icon={<ScanOutlined />} loading={scanning} onClick={handleScan}>执行扫描</Button>
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
  );
};

export default ScanManager;
