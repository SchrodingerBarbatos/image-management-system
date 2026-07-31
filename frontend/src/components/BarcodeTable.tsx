import React from 'react';
import { Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { BarcodeRec } from '../services/api';

interface Props {
  barcodes: BarcodeRec[];
  loading: boolean;
  total: number;
  page: number;
  pageSize: number;
  selectedBarcode: string | null;
  selectedBarcodes: Set<string>;
  onSelectionChange: (barcodes: Set<string>) => void;
  onRowClick: (barcode: string) => void;
  onPageChange: (page: number, pageSize: number) => void;
  onSortChange: (field: string, order: 'asc' | 'desc') => void;
}

const chip = (count: number, cls: string, suffix = '') => (
  <span className={`count-chip ${count > 0 ? cls : 'zero'}`}>
    {count > 0 ? `${count}${suffix}` : '无'}
  </span>
);

// NOTE: dataIndex values must stay in sync with backend _BARCODE_SORT_WHITELIST
// in routes/images.py. Changing a dataIndex will silently break sorting.
const columns: ColumnsType<BarcodeRec> = [
  {
    title: '条码', dataIndex: 'barcode', width: 170, sorter: true,
    render: (v: string) => <span className="mono" style={{ fontWeight: 600 }}>{v}</span>,
  },
  {
    title: '主图', dataIndex: 'main_count', width: 76, sorter: true,
    render: (c: number) => chip(c, 'main'),
  },
  {
    title: '详情图', dataIndex: 'detail_count', width: 76, sorter: true,
    render: (c: number) => chip(c, 'detail'),
  },
  {
    title: '主图版本', dataIndex: 'main_versions', width: 86, sorter: true,
    render: (c: number) => chip(c, 'ver'),
  },
  {
    title: '详情版本', dataIndex: 'detail_versions', width: 86, sorter: true,
    render: (c: number) => chip(c, 'ver'),
  },
];

const BarcodeTable: React.FC<Props> = ({
  barcodes, loading, total, page, pageSize,
  selectedBarcode, selectedBarcodes, onSelectionChange,
  onRowClick, onPageChange, onSortChange,
}) => {
  return (
    <Table<BarcodeRec>
      rowKey="barcode"
      columns={columns}
      dataSource={barcodes}
      loading={loading}
      size="small"
      rowSelection={{
        selectedRowKeys: Array.from(selectedBarcodes),
        preserveSelectedRowKeys: true,
        onChange: (keys) => onSelectionChange(new Set(keys as string[])),
      }}
      onRow={(record) => ({
        onClick: () => onRowClick(record.barcode),
        className: `clickable${record.barcode === selectedBarcode ? ' row-active' : ''}`,
      })}
      pagination={{
        current: page, pageSize, total, showSizeChanger: true,
        onChange: onPageChange, showTotal: (t) => `共 ${t} 条码`,
      }}
      onChange={(_pagination, _filters, sorter) => {
        if (!Array.isArray(sorter) && sorter.column) {
          const field = sorter.field as string;
          const order = sorter.order === 'ascend' ? 'asc' : 'desc';
          onSortChange(field, order);
        }
      }}
      scroll={{ y: 'calc(100vh - 272px)' }}
    />
  );
};

export default BarcodeTable;
