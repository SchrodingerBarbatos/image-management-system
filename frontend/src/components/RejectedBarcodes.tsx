import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Modal, Table, Button, Input, Space, Popconfirm, message, Card, Row, Col, Statistic } from 'antd';
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import { rejectedBarcodeApi, RejectedBarcode, RejectedBarcodeStats, RejectedBarcodeParams } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
}

const RejectedBarcodes: React.FC<Props> = ({ visible, onClose }) => {
  const [data, setData] = useState<RejectedBarcode[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filters, setFilters] = useState<RejectedBarcodeParams>({});
  const [barcodeInput, setBarcodeInput] = useState('');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [stats, setStats] = useState<RejectedBarcodeStats | null>(null);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce barcode input → filters
  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      setFilters(prev => ({ ...prev, barcode: barcodeInput || undefined }));
    }, 300);
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [barcodeInput]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const result = await rejectedBarcodeApi.list({
        page,
        page_size: pageSize,
        ...filters,
      });
      setData(result.items);
      setTotal(result.total);
    } catch (error) {
      message.error('获取数据失败');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, filters]);

  const fetchStats = useCallback(async () => {
    try {
      const result = await rejectedBarcodeApi.getStats();
      setStats(result);
    } catch (error) {
      console.error('获取统计信息失败', error);
    }
  }, []);

  useEffect(() => {
    if (visible) {
      fetchData();
      fetchStats();
    }
  }, [visible, fetchData, fetchStats]);

  const handleDelete = async (id: number) => {
    try {
      const result = await rejectedBarcodeApi.delete(id);
      message.success(result.message);
      fetchData();
      fetchStats();
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleDeleteBatch = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择要删除的记录');
      return;
    }
    try {
      const result = await rejectedBarcodeApi.deleteBatch(selectedRowKeys as number[]);
      message.success(result.message);
      setSelectedRowKeys([]);
      fetchData();
      fetchStats();
    } catch (error) {
      message.error('批量删除失败');
    }
  };

  const handleDeleteAll = async () => {
    try {
      const result = await rejectedBarcodeApi.deleteAll(filters);
      message.success(result.message);
      fetchData();
      fetchStats();
    } catch (error) {
      message.error('全选删除失败');
    }
  };

  const handleResetFilters = () => {
    setBarcodeInput('');
    setFilters({});
  };

  const columns = [
    {
      title: '条码',
      dataIndex: 'barcode',
      key: 'barcode',
      width: 150,
      sorter: (a: RejectedBarcode, b: RejectedBarcode) => a.barcode.localeCompare(b.barcode),
    },
    {
      title: '文件名',
      dataIndex: 'filename',
      key: 'filename',
      width: 200,
    },
    {
      title: '拒绝原因',
      dataIndex: 'reason',
      key: 'reason',
      width: 250,
    },
    {
      title: '扫描目录',
      dataIndex: 'scan_root_path',
      key: 'scan_root_path',
      width: 200,
    },
    {
      title: '记录时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      sorter: (a: RejectedBarcode, b: RejectedBarcode) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: RejectedBarcode) => (
        <Popconfirm
          title="确定要删除这条记录吗？"
          description="删除后将同时删除对应的文件"
          onConfirm={() => handleDelete(record.id)}
        >
          <Button type="link" danger icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Modal
      title="非标品条码记录"
      open={visible}
      onCancel={onClose}
      width={1200}
      footer={[
        <Button key="close" onClick={onClose}>
          关闭
        </Button>,
      ]}
    >
      {/* 统计信息 */}
      {stats && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={8}>
            <Card>
              <Statistic title="总记录数" value={stats.total} />
            </Card>
          </Col>
          <Col span={8}>
            <Card>
              <Statistic
                title="拒绝原因分布"
                value={Object.keys(stats.by_reason).length}
                suffix="种"
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card>
              <Statistic
                title="涉及扫描目录"
                value={Object.keys(stats.by_scan_root).length}
                suffix="个"
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 筛选条件 */}
      <Space style={{ marginBottom: 16 }}>
        <Input
          placeholder="按条码筛选"
          value={barcodeInput}
          onChange={e => setBarcodeInput(e.target.value)}
          allowClear
        />
        <Button icon={<ReloadOutlined />} onClick={handleResetFilters}>
          重置筛选
        </Button>
        <Button
          type="primary"
          danger
          icon={<DeleteOutlined />}
          onClick={handleDeleteBatch}
          disabled={selectedRowKeys.length === 0}
        >
          批量删除 ({selectedRowKeys.length})
        </Button>
        <Popconfirm
          title="确定要删除所有匹配的记录吗？"
          description="删除后将同时删除对应的文件"
          onConfirm={handleDeleteAll}
        >
          <Button type="primary" danger icon={<DeleteOutlined />}>
            全选删除
          </Button>
        </Popconfirm>
      </Space>

      {/* 数据表格 */}
      <Table
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
        }}
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
        pagination={{
          current: page,
          pageSize: pageSize,
          total: total,
          onChange: (page, pageSize) => {
            setPage(page);
            setPageSize(pageSize);
          },
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条记录`,
        }}
        scroll={{ x: 1200 }}
      />
    </Modal>
  );
};

export default RejectedBarcodes;
