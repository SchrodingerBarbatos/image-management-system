import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Table, Button, Input, Popconfirm, App } from 'antd';
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons';
import { rejectedBarcodeApi, RejectedBarcode, RejectedBarcodeStats, RejectedBarcodeParams } from '../services/api';
import { StatCard } from './ui';

const RejectedPanel: React.FC = () => {
  const { message } = App.useApp();
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
    } catch {
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
    fetchData();
    fetchStats();
  }, [fetchData, fetchStats]);

  const handleDelete = async (id: number) => {
    try {
      const result = await rejectedBarcodeApi.delete(id);
      message.success(result.message);
      fetchData();
      fetchStats();
    } catch {
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
    } catch {
      message.error('批量删除失败');
    }
  };

  const handleDeleteAll = async () => {
    try {
      const result = await rejectedBarcodeApi.deleteAll(filters);
      message.success(result.message);
      fetchData();
      fetchStats();
    } catch {
      message.error('全选删除失败');
    }
  };

  const handleResetFilters = () => {
    setBarcodeInput('');
    setFilters({});
  };

  const columns = [
    {
      title: '条码', dataIndex: 'barcode', width: 150,
      sorter: (a: RejectedBarcode, b: RejectedBarcode) => a.barcode.localeCompare(b.barcode),
      render: (v: string) => <span className="mono">{v}</span>,
    },
    { title: '文件名', dataIndex: 'filename', width: 200, ellipsis: true },
    { title: '拒绝原因', dataIndex: 'reason', width: 250, ellipsis: true },
    {
      title: '扫描目录', dataIndex: 'scan_root_path', width: 200, ellipsis: true,
      render: (v: string) => <span className="mono" style={{ fontSize: 11, color: 'var(--t2)' }}>{v}</span>,
    },
    {
      title: '记录时间', dataIndex: 'created_at', width: 180,
      sorter: (a: RejectedBarcode, b: RejectedBarcode) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      render: (v: string) => <span className="mono">{v?.replace('T', ' ').slice(0, 19)}</span>,
    },
    {
      title: '操作', key: 'action', width: 90,
      render: (_: unknown, record: RejectedBarcode) => (
        <Popconfirm
          title="确定要删除这条记录吗？"
          description="删除后将同时删除对应的文件"
          onConfirm={() => handleDelete(record.id)}
        >
          <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      {stats && (
        <div className="stat-row" style={{ marginBottom: 14 }}>
          <StatCard num={stats.total} label="总记录数" />
          <StatCard num={Object.keys(stats.by_reason).length} label="拒绝原因分布（种）" accent="var(--amber)" />
          <StatCard num={Object.keys(stats.by_scan_root).length} label="涉及扫描目录（个）" accent="var(--acc-2)" />
        </div>
      )}

      <div className="toolbar">
        <Input
          placeholder="按条码筛选"
          value={barcodeInput}
          onChange={e => setBarcodeInput(e.target.value)}
          allowClear
          style={{ width: 220 }}
        />
        <Button icon={<ReloadOutlined />} onClick={handleResetFilters}>重置筛选</Button>
        <span className="spacer" />
        <Button
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
          <Button type="primary" danger icon={<DeleteOutlined />}>全选删除</Button>
        </Popconfirm>
      </div>

      <Table
        rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={{
          current: page,
          pageSize: pageSize,
          total: total,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (t) => `共 ${t} 条记录`,
        }}
        scroll={{ x: 1080 }}
      />
    </div>
  );
};

export default RejectedPanel;
