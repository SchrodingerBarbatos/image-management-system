import React from 'react';
import { Input, Button, Space } from 'antd';
import { SearchOutlined, ScanOutlined, ExportOutlined, WarningOutlined, ToolOutlined } from '@ant-design/icons';

interface Props {
  onSearch: (barcode: string) => void;
  onOpenScanManager: () => void;
  onExportExcel: () => void;
  onOpenBatch: () => void;
  onOpenPending: () => void;
  pendingCount?: number;
}

const SearchBar: React.FC<Props> = ({ onSearch, onOpenScanManager, onExportExcel, onOpenBatch, onOpenPending, pendingCount }) => {
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
        <Button icon={<ScanOutlined />} onClick={onOpenScanManager}>扫描目录</Button>
        <Button icon={<ExportOutlined />} onClick={onExportExcel}>Excel 导出</Button>
        <Button icon={<ToolOutlined />} onClick={onOpenBatch}>批量操作</Button>
        <Button icon={<WarningOutlined />} onClick={onOpenPending} danger={!!pendingCount}>
          待确认 {pendingCount ? `(${pendingCount})` : ''}
        </Button>
      </Space>
    </Space>
  );
};

export default SearchBar;
