import React from 'react';
import { Input, Button, Space } from 'antd';
import { SearchOutlined, FolderAddOutlined, ExportOutlined, ScanOutlined, WarningOutlined } from '@ant-design/icons';

interface Props {
  onSearch: (barcode: string) => void;
  onAddScanRoot: () => void;
  onExportExcel: () => void;
  onTriggerScan: () => void;
  onOpenPending: () => void;
  pendingCount?: number;
  loading?: boolean;
}

const SearchBar: React.FC<Props> = ({ onSearch, onAddScanRoot, onExportExcel, onTriggerScan, onOpenPending, pendingCount, loading }) => {
  return (
    <Space wrap style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
      <Space>
        <Input.Search
          placeholder="输入条码搜索..."
          allowClear
          onSearch={onSearch}
          style={{ width: 280 }}
          prefix={<SearchOutlined />}
        />
      </Space>
      <Space>
        <Button icon={<FolderAddOutlined />} onClick={onAddScanRoot}>添加扫描目录</Button>
        <Button icon={<ScanOutlined />} onClick={onTriggerScan} loading={loading}>扫描</Button>
        <Button icon={<ExportOutlined />} onClick={onExportExcel}>Excel 导出</Button>
        <Button icon={<WarningOutlined />} onClick={onOpenPending} danger={!!pendingCount}>
          待确认 {pendingCount ? `(${pendingCount})` : ''}
        </Button>
      </Space>
    </Space>
  );
};

export default SearchBar;
