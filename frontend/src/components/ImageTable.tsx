import React from 'react';
import { Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ImageRec } from '../services/api';

interface Props {
  images: ImageRec[];
  loading: boolean;
  total: number;
  page: number;
  pageSize: number;
  selectedRowKeys: React.Key[];
  onSelectionChange: (keys: React.Key[], rows: ImageRec[]) => void;
  onRowClick: (barcode: string) => void;
  onPageChange: (page: number, pageSize: number) => void;
  onSortChange: (field: string, order: 'asc' | 'desc') => void;
}

const columns: ColumnsType<ImageRec> = [
  { title: '条码', dataIndex: 'barcode', width: 150, sorter: true },
  { title: '类型', dataIndex: 'image_type', width: 80, render: (t: string) => (
    <Tag color={t === 'main' ? 'blue' : 'green'}>{t === 'main' ? '主图' : '详情图'}</Tag>
  )},
  { title: '序号', dataIndex: 'sequence', width: 60 },
  { title: '文件名', dataIndex: 'filename', ellipsis: true },
  { title: '文件夹', dataIndex: 'folder_path', ellipsis: true, width: 200 },
  { title: '大小', dataIndex: 'file_size', width: 80, render: (s: number) => `${(s / 1024).toFixed(0)} KB` },
  { title: '状态', dataIndex: 'confirmed', width: 70, render: (c: boolean) => c ? null : <Tag color="orange">待确认</Tag> },
];

const ImageTable: React.FC<Props> = ({
  images, loading, total, page, pageSize,
  selectedRowKeys, onSelectionChange, onRowClick, onPageChange, onSortChange,
}) => {
  return (
    <Table<ImageRec>
      rowKey="id"
      columns={columns}
      dataSource={images}
      loading={loading}
      size="small"
      rowSelection={{
        selectedRowKeys,
        onChange: onSelectionChange,
      }}
      onRow={(record) => ({
        onClick: () => onRowClick(record.barcode),
        style: { cursor: 'pointer' },
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
