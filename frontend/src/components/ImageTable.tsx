import React from 'react';
import { Table, Tag } from 'antd';
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

const columns: ColumnsType<BarcodeRec> = [
  { title: '条码', dataIndex: 'barcode', width: 160, sorter: true },
  {
    title: '主图', dataIndex: 'main_count', width: 80, sorter: true,
    render: (c: number) => c > 0 ? <Tag color="blue">{c} 张</Tag> : <Tag>无</Tag>,
  },
  {
    title: '详情图', dataIndex: 'detail_count', width: 90, sorter: true,
    render: (c: number) => c > 0 ? <Tag color="green">{c} 张</Tag> : <Tag>无</Tag>,
  },
  {
    title: '主图版本', dataIndex: 'main_versions', width: 90, sorter: true,
    render: (c: number) => c > 0 ? <Tag color="purple">{c}</Tag> : <Tag>无</Tag>,
  },
  {
    title: '详情图版本', dataIndex: 'detail_versions', width: 95, sorter: true,
    render: (c: number) => c > 0 ? <Tag color="geekblue">{c}</Tag> : <Tag>无</Tag>,
  },
];

const ImageTable: React.FC<Props> = ({
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
        onChange: (keys) => onSelectionChange(new Set(keys as string[])),
      }}
      onRow={(record) => ({
        onClick: () => onRowClick(record.barcode),
        style: {
          cursor: 'pointer',
          background: record.barcode === selectedBarcode ? '#e6f7ff' : undefined,
        },
      })}
      pagination={{
        current: page, pageSize, total, showSizeChanger: true,
        onChange: onPageChange, showTotal: (t) => `共 ${t} 条`,
      }}
      onChange={(_pagination, _filters, sorter) => {
        if (!Array.isArray(sorter) && sorter.column) {
          const field = sorter.field as string;
          const order = sorter.order === 'ascend' ? 'asc' : 'desc';
          onSortChange(field, order);
        }
      }}
      scroll={{ y: 'calc(100vh - 280px)' }}
    />
  );
};

export default ImageTable;
