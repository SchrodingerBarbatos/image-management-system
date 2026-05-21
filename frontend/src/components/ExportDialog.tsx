import React, { useState } from 'react';
import { Modal, Upload, Button, Select, Radio, Space, message, Steps } from 'antd';
import { UploadOutlined, DownloadOutlined } from '@ant-design/icons';
import { exportApi } from '../services/api';

interface Props {
  visible: boolean;
  onClose: () => void;
}

const ExportDialog: React.FC<Props> = ({ visible, onClose }) => {
  const [step, setStep] = useState(0);
  const [columns, setColumns] = useState<string[]>([]);
  const [uploadId, setUploadId] = useState('');
  const [barcodeColumn, setBarcodeColumn] = useState('');
  const [imageType, setImageType] = useState('all');
  const [taskId, setTaskId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const handleUpload = async (file: File) => {
    setLoading(true);
    try {
      const res = await exportApi.uploadExcel(file);
      setColumns(res.columns);
      setUploadId(res.upload_id);
      setStep(1);
    } catch {
      message.error('上传失败');
    }
    setLoading(false);
    return false;
  };

  const handleGenerate = async () => {
    if (!barcodeColumn) return;
    setLoading(true);
    try {
      const res = await exportApi.generateZip({ barcode_column: barcodeColumn, image_type: imageType, upload_id: uploadId });
      setTaskId(res.task_id);
      setStep(2);
    } catch {
      message.error('生成失败');
    }
    setLoading(false);
  };

  const handleDownload = () => {
    if (taskId) window.open(exportApi.downloadUrl(taskId), '_blank');
  };

  const reset = () => { setStep(0); setColumns([]); setUploadId(''); setBarcodeColumn(''); setTaskId(null); };

  return (
    <Modal title="Excel 批量导出" open={visible} onCancel={() => { reset(); onClose(); }} width={550} footer={null}>
      <Steps current={step} size="small" style={{ marginBottom: 24 }}
        items={[{ title: '上传 Excel' }, { title: '选择列' }, { title: '下载' }]} />

      {step === 0 && (
        <Upload accept=".xlsx" maxCount={1} beforeUpload={handleUpload} showUploadList={false}>
          <Button icon={<UploadOutlined />} loading={loading}>上传 Excel 文件</Button>
        </Upload>
      )}

      {step === 1 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <span>条码所在列：</span>
            <Select value={barcodeColumn} onChange={setBarcodeColumn} style={{ width: '100%' }}
              placeholder="选择条码所在的列"
              options={columns.map(c => ({ value: c, label: c }))} />
          </div>
          <div>
            <span>导出类型：</span>
            <Radio.Group value={imageType} onChange={e => setImageType(e.target.value)}>
              <Radio.Button value="all">全部</Radio.Button>
              <Radio.Button value="main">仅主图</Radio.Button>
              <Radio.Button value="detail">仅详情图</Radio.Button>
            </Radio.Group>
          </div>
          <Button type="primary" onClick={handleGenerate} loading={loading} disabled={!barcodeColumn}>
            生成 ZIP
          </Button>
        </Space>
      )}

      {step === 2 && (
        <Space direction="vertical" style={{ width: '100%', alignItems: 'center' }}>
          <p>ZIP 文件已生成</p>
          <Button icon={<DownloadOutlined />} type="primary" size="large" onClick={handleDownload}>
            下载 ZIP
          </Button>
        </Space>
      )}
    </Modal>
  );
};

export default ExportDialog;
