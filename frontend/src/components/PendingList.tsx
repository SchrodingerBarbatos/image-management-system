import React, { useState, useEffect } from 'react';
import { Modal, Table, Button, Select, Space, message } from 'antd';
import { ImageRec, pendingApi } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
  onConfirmed: () => void;
}

const PendingList: React.FC<Props> = ({ visible, onClose, onConfirmed }) => {
  const [items, setItems] = useState<ImageRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [selectedType, setSelectedType] = useState<string>('main');

  const fetchPending = () => {
    setLoading(true);
    pendingApi.list().then(setItems).finally(() => setLoading(false));
  };

  useEffect(() => { if (visible) fetchPending(); }, [visible]);

  const handleConfirm = async () => {
    if (selectedRowKeys.length === 0) return;
    const toConfirm = selectedRowKeys.map(Number).map(id => ({ id, image_type: selectedType }));
    await pendingApi.confirm(toConfirm);
    message.success(`已确认 ${selectedRowKeys.length} 条`);
    setSelectedRowKeys([]);
    fetchPending();
    onConfirmed();
  };

  const handleIgnore = async (id: number) => {
    await pendingApi.ignore(id);
    fetchPending();
  };

  const columns = [
    { title: '条码', dataIndex: 'barcode', width: 130 },
    { title: '序号', dataIndex: 'sequence', width: 60 },
    { title: '文件名', dataIndex: 'filename', ellipsis: true },
    { title: '文件夹', dataIndex: 'folder_path', ellipsis: true },
    { title: '大小', dataIndex: 'file_size', width: 80, render: (s: number) => `${(s / 1024).toFixed(0)} KB` },
    { title: '操作', width: 60, render: (_: unknown, r: ImageRec) => (
      <Button size="small" onClick={() => handleIgnore(r.id)}>忽略</Button>
    )},
  ];

  return (
    <Modal title="待确认图片" open={visible} onCancel={onClose} width={800} footer={null}>
      <Space style={{ marginBottom: 12 }}>
        <Select value={selectedType} onChange={setSelectedType} style={{ width: 100 }}
          options={[{ value: 'main', label: '主图' }, { value: 'detail', label: '详情图' }]} />
        <Button type="primary" onClick={handleConfirm} disabled={selectedRowKeys.length === 0}>
          确认选中 ({selectedRowKeys.length})
        </Button>
      </Space>
      <Table rowKey="id" columns={columns} dataSource={items} loading={loading} size="small"
        rowSelection={{ selectedRowKeys, onChange: keys => setSelectedRowKeys(keys) }}
        pagination={false} scroll={{ y: 400 }} />
    </Modal>
  );
};

export default PendingList;
