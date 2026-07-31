import React, { useState, useEffect } from 'react';
import { Table, Button, App } from 'antd';
import { CheckOutlined } from '@ant-design/icons';
import { ImageRec, pendingApi } from '../services/api';
import { Seg } from '../components/ui';

interface Props {
  onConfirmed: () => void;
}

const PendingView: React.FC<Props> = ({ onConfirmed }) => {
  const { message } = App.useApp();
  const [items, setItems] = useState<ImageRec[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [selectedType, setSelectedType] = useState<'main' | 'detail'>('main');

  const fetchPending = () => {
    setLoading(true);
    pendingApi.list().then(setItems).finally(() => setLoading(false));
  };

  useEffect(() => { fetchPending(); }, []);

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
    { title: '条码', dataIndex: 'barcode', width: 140,
      render: (v: string) => <span className="mono">{v}</span> },
    { title: '序号', dataIndex: 'sequence', width: 60,
      render: (v: number) => <span className="mono">{v}</span> },
    { title: '文件名', dataIndex: 'filename', ellipsis: true },
    { title: '文件夹', dataIndex: 'folder_path', ellipsis: true,
      render: (v: string) => <span className="mono" style={{ fontSize: 11, color: 'var(--t2)' }}>{v}</span> },
    { title: '大小', dataIndex: 'file_size', width: 90,
      render: (s: number) => <span className="mono">{(s / 1024).toFixed(0)} KB</span> },
    { title: '操作', width: 70, render: (_: unknown, r: ImageRec) => (
      <Button size="small" type="link" onClick={() => handleIgnore(r.id)}>忽略</Button>
    )},
  ];

  return (
    <>
      <div className="view-head">
        <div className="view-sub">扫描发现的新图片，确认类型后进入图库</div>
        <div className="view-head-right">
          <span className="hint">确认为</span>
          <Seg
            value={selectedType}
            onChange={setSelectedType}
            options={[
              { value: 'main', label: '主图' },
              { value: 'detail', label: '详情图' },
            ]}
          />
          <Button
            type="primary"
            icon={<CheckOutlined />}
            onClick={handleConfirm}
            disabled={selectedRowKeys.length === 0}
          >
            确认选中 ({selectedRowKeys.length})
          </Button>
        </div>
      </div>

      <div className="panel">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={items}
          loading={loading}
          size="small"
          rowSelection={{ selectedRowKeys, onChange: keys => setSelectedRowKeys(keys) }}
          pagination={{ pageSize: 50, showTotal: (t) => `共 ${t} 条` }}
        />
      </div>
    </>
  );
};

export default PendingView;
