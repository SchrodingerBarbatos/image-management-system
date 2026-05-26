import React, { useState, useMemo } from 'react';
import { Modal, Tabs, Button, Checkbox, Collapse, Table, Space, InputNumber, Tag, Divider, message, Typography } from 'antd';
import { batchApi, DuplicateGroup, LowVersionGroup } from '../services/api';

const { Text } = Typography;

interface Props {
  visible: boolean;
  onClose: () => void;
  onCompleted: () => void;
}

const TYPE_LABELS: Record<string, string> = { main: '主图', detail: '详情图' };
const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  will_delete: { color: 'red', label: '将删除' },
  keep_threshold: { color: 'green', label: '保留（满足阈值）' },
  keep_only: { color: 'blue', label: '保留（唯一版本）' },
  keep_disabled: { color: 'default', label: '保留（未启用）' },
};
const MAX_INITIAL_EXPAND_GROUPS = 20;

function initialExpandedKeys(barcodes: string[]): string[] {
  return barcodes.length <= MAX_INITIAL_EXPAND_GROUPS ? barcodes : [];
}

function fmtSize(bytes: number): string {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

const BatchOperations: React.FC<Props> = ({ visible, onClose, onCompleted }) => {
  const [activeTab, setActiveTab] = useState<string>('duplicates');

  // ===== Tab 1: Duplicates =====
  const [dupGroups, setDupGroups] = useState<DuplicateGroup[]>([]);
  const [dupLoading, setDupLoading] = useState(false);
  const [dupScanned, setDupScanned] = useState(false);
  const [dupSelected, setDupSelected] = useState<Set<string>>(new Set());
  const [dupExpanded, setDupExpanded] = useState<string[]>([]);

  // ===== Tab 2: Low Versions =====
  const [mainEnabled, setMainEnabled] = useState(false);
  const [mainThreshold, setMainThreshold] = useState(3);
  const [detailEnabled, setDetailEnabled] = useState(false);
  const [detailThreshold, setDetailThreshold] = useState(5);
  const [lowGroups, setLowGroups] = useState<LowVersionGroup[]>([]);
  const [lowLoading, setLowLoading] = useState(false);
  const [lowMatched, setLowMatched] = useState(false);
  const [lowSelected, setLowSelected] = useState<Set<string>>(new Set());
  const [lowExpanded, setLowExpanded] = useState<string[]>([]);
  const [lowSummary, setLowSummary] = useState<Record<string, number>>({});

  // ===== Confirm modal =====
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [confirmDeleteFiles, setConfirmDeleteFiles] = useState(false);
  const [confirmAction, setConfirmAction] = useState<(() => Promise<void>) | null>(null);
  const [confirmTitle, setConfirmTitle] = useState('');
  const [confirmBody, setConfirmBody] = useState('');

  // ===== Deleting state =====
  const [deleting, setDeleting] = useState(false);

  // ---- Helpers ----
  const dupKey = (g: DuplicateGroup) => `${g.barcode}|${g.image_type}|${g.folder_ctime}`;
  const lowKey = (g: LowVersionGroup) => `${g.barcode}|${g.image_type}|${g.folder_ctime}`;

  // Group duplicates by barcode for collapse panels
  const dupByBarcode = useMemo(() => {
    const map: Record<string, DuplicateGroup[]> = {};
    for (const g of dupGroups) {
      (map[g.barcode] ??= []).push(g);
    }
    return map;
  }, [dupGroups]);

  // Group low versions by barcode for collapse panels
  const lowByBarcode = useMemo(() => {
    const map: Record<string, LowVersionGroup[]> = {};
    for (const g of lowGroups) {
      (map[g.barcode] ??= []).push(g);
    }
    return map;
  }, [lowGroups]);

  // ---- Tab 1: Scan ----
  const handleScanDuplicates = async () => {
    setDupLoading(true);
    try {
      const res = await batchApi.listDuplicates();
      setDupGroups(res.groups);
      setDupScanned(true);
      const allKeys = res.groups.map(dupKey);
      setDupSelected(new Set(allKeys));
      const barcodes = [...new Set(res.groups.map(g => g.barcode))];
      setDupExpanded(initialExpandedKeys(barcodes));
      if (barcodes.length > MAX_INITIAL_EXPAND_GROUPS) {
        message.info(`共 ${barcodes.length} 个条码，已默认收起以避免页面卡顿，请按需展开`);
      }
    } catch {
      message.error('扫描重复文件夹失败');
    } finally {
      setDupLoading(false);
    }
  };

  const toggleDupAll = () => {
    if (dupSelected.size === dupGroups.length) {
      setDupSelected(new Set());
    } else {
      setDupSelected(new Set(dupGroups.map(dupKey)));
    }
  };

  const toggleDupOne = (key: string) => {
    const next = new Set(dupSelected);
    if (next.has(key)) next.delete(key); else next.add(key);
    setDupSelected(next);
  };

  const toggleDupExpandAll = () => {
    const barcodes = Object.keys(dupByBarcode);
    if (dupExpanded.length === barcodes.length) {
      setDupExpanded([]);
    } else if (barcodes.length > MAX_INITIAL_EXPAND_GROUPS) {
      Modal.confirm({
        title: '展开全部条码',
        content: `共 ${barcodes.length} 个条码，展开全部可能导致页面卡顿，是否继续？`,
        okText: '继续展开',
        cancelText: '取消',
        onOk: () => setDupExpanded(barcodes),
      });
    } else {
      setDupExpanded(barcodes);
    }
  };

  // ---- Tab 2: Match ----
  const handleMatchLow = async () => {
    if (!mainEnabled && !detailEnabled) {
      message.warning('至少启用一项阈值');
      return;
    }
    setLowLoading(true);
    try {
      const res = await batchApi.listLowVersions(
        mainEnabled ? mainThreshold : 0,
        detailEnabled ? detailThreshold : 0,
      );
      setLowGroups(res.groups);
      setLowSummary(res.summary);
      setLowMatched(true);
      const deleteKeys = res.groups
        .filter(g => g.status_tag === 'will_delete')
        .map(lowKey);
      setLowSelected(new Set(deleteKeys));
      const barcodes = [...new Set(res.groups.map(g => g.barcode))];
      setLowExpanded(initialExpandedKeys(barcodes));
      if (barcodes.length > MAX_INITIAL_EXPAND_GROUPS) {
        message.info(`共 ${barcodes.length} 个条码，已默认收起以避免页面卡顿，请按需展开`);
      }
    } catch {
      message.error('匹配失败');
    } finally {
      setLowLoading(false);
    }
  };

  const toggleLowOne = (key: string) => {
    const next = new Set(lowSelected);
    if (next.has(key)) next.delete(key); else next.add(key);
    setLowSelected(next);
  };

  const toggleLowExpandAll = () => {
    const barcodes = Object.keys(lowByBarcode);
    if (lowExpanded.length === barcodes.length) {
      setLowExpanded([]);
    } else if (barcodes.length > MAX_INITIAL_EXPAND_GROUPS) {
      Modal.confirm({
        title: '展开全部条码',
        content: `共 ${barcodes.length} 个条码，展开全部可能导致页面卡顿，是否继续？`,
        okText: '继续展开',
        cancelText: '取消',
        onOk: () => setLowExpanded(barcodes),
      });
    } else {
      setLowExpanded(barcodes);
    }
  };

  // ---- Confirm & Delete ----
  const openConfirm = (action: () => Promise<void>, deleteFiles: boolean, title: string, body: string) => {
    setConfirmAction(() => action);
    setConfirmDeleteFiles(deleteFiles);
    setConfirmTitle(title);
    setConfirmBody(body);
    setConfirmVisible(true);
  };

  const handleConfirmDelete = async () => {
    setConfirmVisible(false);
    if (!confirmAction) return;
    setDeleting(true);
    try {
      await confirmAction();
      message.success('删除完成');
      onCompleted();
      onClose();
    } catch (err: any) {
      message.error(err?.response?.data?.error || '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  // ---- Delete actions ----
  const buildDupDeleteAction = (deleteFiles: boolean) => async () => {
    const items = dupGroups
      .filter(g => dupSelected.has(dupKey(g)))
      .map(g => ({ barcode: g.barcode, image_type: g.image_type, folder_ctime: g.folder_ctime }));
    await batchApi.deleteDuplicates(items, deleteFiles);
  };

  const buildLowDeleteAction = (deleteFiles: boolean) => async () => {
    const items = lowGroups
      .filter(g => lowSelected.has(lowKey(g)))
      .map(g => ({ barcode: g.barcode, image_type: g.image_type, folder_ctime: g.folder_ctime }));
    await batchApi.deleteLowVersions(
      items,
      deleteFiles,
      mainEnabled ? mainThreshold : 0,
      detailEnabled ? detailThreshold : 0,
    );
  };

  // ---- Collapse panel builders ----
  const dupPanelItems = useMemo(() => {
    return Object.entries(dupByBarcode).map(([barcode, items]) => {
      const mainItems = items.filter(i => i.image_type === 'main');
      const detailItems = items.filter(i => i.image_type === 'detail');
      const parts: string[] = [];
      if (mainItems.length) parts.push(`主图${mainItems.length}组重复(${mainItems.reduce((s, i) => s + i.image_count, 0)}张)`);
      if (detailItems.length) parts.push(`详情图${detailItems.length}组重复(${detailItems.reduce((s, i) => s + i.image_count, 0)}张)`);

      const columns = [
        { title: '类型', dataIndex: 'image_type', width: 70, render: (t: string) => TYPE_LABELS[t] || t },
        { title: '保留版本', dataIndex: 'version_label', width: 70 },
        { title: '重复文件夹时间', dataIndex: 'folder_ctime', width: 180, render: (v: string) => v.slice(0, 19) },
        { title: '图片数', dataIndex: 'image_count', width: 60 },
        { title: '大小', dataIndex: 'total_file_size', width: 80, render: (v: number) => fmtSize(v) },
        {
          title: '选择', width: 50,
          render: (_: unknown, r: DuplicateGroup) => (
            <Checkbox checked={dupSelected.has(dupKey(r))} onChange={() => toggleDupOne(dupKey(r))} />
          ),
        },
      ];

      return {
        key: barcode,
        label: <Text strong>{barcode}</Text>,
        extra: <Text type="secondary" style={{ fontSize: 12 }}>{parts.join('  ')}</Text>,
        children: <Table rowKey={r => dupKey(r)} columns={columns} dataSource={items} size="small" pagination={false} />,
      };
    });
  }, [dupByBarcode, dupSelected]);

  const lowPanelItems = useMemo(() => {
    return Object.entries(lowByBarcode).map(([barcode, items]) => {
      const willDel = items.filter(i => i.status_tag === 'will_delete').length;
      const keepTh = items.filter(i => i.status_tag === 'keep_threshold').length;
      const keepOnly = items.filter(i => i.status_tag === 'keep_only').length;
      const parts: string[] = [];
      if (willDel) parts.push(`删${willDel}`);
      if (keepTh) parts.push(`保${keepTh}(满足阈值)`);
      if (keepOnly) parts.push(`保${keepOnly}(唯一)`);

      // Color the header based on whether there are items to delete
      const headerColor = willDel > 0 ? '#ff4d4f' : '#52c41a';

      const columns = [
        { title: '类型', dataIndex: 'image_type', width: 70, render: (t: string) => TYPE_LABELS[t] || t },
        { title: '版本', dataIndex: 'version_label', width: 60 },
        { title: '文件夹时间', dataIndex: 'folder_ctime', width: 180, render: (v: string) => v.slice(0, 19) },
        { title: '图片数', dataIndex: 'image_count', width: 60 },
        { title: '大小', dataIndex: 'total_file_size', width: 80, render: (v: number) => fmtSize(v) },
        {
          title: '状态', width: 150,
          render: (_: unknown, r: LowVersionGroup) => {
            const cfg = STATUS_CONFIG[r.status_tag];
            return <Tag color={cfg.color}>{cfg.label}</Tag>;
          },
        },
        {
          title: '选择', width: 50,
          render: (_: unknown, r: LowVersionGroup) => {
            if (r.status_tag !== 'will_delete') return null;
            const key = lowKey(r);
            return <Checkbox checked={lowSelected.has(key)} onChange={() => toggleLowOne(key)} />;
          },
        },
      ];

      return {
        key: barcode,
        label: <Text strong style={{ color: headerColor }}>{barcode}</Text>,
        extra: <Text type="secondary" style={{ fontSize: 12 }}>{parts.join('  ')}</Text>,
        children: <Table rowKey={r => lowKey(r)} columns={columns} dataSource={items} size="small" pagination={false} />,
      };
    });
  }, [lowByBarcode, lowSelected]);

  // ---- Render ----
  const dupAllChecked = dupGroups.length > 0 && dupSelected.size === dupGroups.length;
  const dupIndeterminate = dupSelected.size > 0 && dupSelected.size < dupGroups.length;
  const dupSelectedCount = dupSelected.size;

  const lowSelectedCount = lowSelected.size;

  const tabItems = [
    {
      key: 'duplicates',
      label: '删除重复文件夹',
      children: (
        <div>
          <Button type="primary" loading={dupLoading} onClick={handleScanDuplicates} style={{ marginBottom: 16 }}>
            扫描重复
          </Button>

          {dupScanned && dupGroups.length === 0 && !dupLoading && (
            <div style={{ color: '#52c41a', marginBottom: 16 }}>未发现重复文件夹</div>
          )}

          {dupGroups.length > 0 && (
            <>
              <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center' }}>
                <Checkbox checked={dupAllChecked} indeterminate={dupIndeterminate} onChange={toggleDupAll}>
                  全选
                </Checkbox>
                <Button size="small" onClick={toggleDupExpandAll}>
                  {dupExpanded.length === Object.keys(dupByBarcode).length ? '全部收起' : '全部展开'}
                </Button>
                <Text type="secondary">共 {dupGroups.length} 组重复，涉及 {Object.keys(dupByBarcode).length} 个条码</Text>
              </div>

              <Collapse
                activeKey={dupExpanded}
                onChange={keys => setDupExpanded(keys as string[])}
                items={dupPanelItems}
                style={{ marginBottom: 16 }}
              />

              <Divider />
              <Space>
                <Text strong>已选 {dupSelectedCount} 组重复</Text>
                <Button
                  danger
                  disabled={dupSelectedCount === 0}
                  loading={deleting}
                  onClick={() => openConfirm(
                    buildDupDeleteAction(false),
                    false,
                    '确认删除重复文件夹',
                    `将删除 ${dupSelectedCount} 组重复文件夹（仅删除索引，文件保留）。此操作不可撤销。`,
                  )}
                >
                  仅删索引
                </Button>
                <Button
                  danger
                  type="primary"
                  disabled={dupSelectedCount === 0}
                  loading={deleting}
                  onClick={() => openConfirm(
                    buildDupDeleteAction(true),
                    true,
                    '确认删除重复文件夹及文件',
                    `将删除 ${dupSelectedCount} 组重复文件夹的索引和磁盘文件。此操作不可撤销！`,
                  )}
                >
                  删索引和文件
                </Button>
              </Space>
            </>
          )}
        </div>
      ),
    },
    {
      key: 'lowVersions',
      label: '删除低版本',
      children: (
        <div>
          <Space direction="vertical" style={{ marginBottom: 16 }}>
            <Space>
              <Checkbox checked={mainEnabled} onChange={e => setMainEnabled(e.target.checked)}>
                启用主图 &gt;=
              </Checkbox>
              <InputNumber min={1} value={mainThreshold} onChange={v => setMainThreshold(v || 3)} disabled={!mainEnabled} style={{ width: 70 }} />
            </Space>
            <Space>
              <Checkbox checked={detailEnabled} onChange={e => setDetailEnabled(e.target.checked)}>
                启用详情图 &gt;=
              </Checkbox>
              <InputNumber min={1} value={detailThreshold} onChange={v => setDetailThreshold(v || 5)} disabled={!detailEnabled} style={{ width: 70 }} />
            </Space>
          </Space>

          <Button
            type="primary"
            loading={lowLoading}
            onClick={handleMatchLow}
            disabled={!mainEnabled && !detailEnabled}
            style={{ marginBottom: 16 }}
          >
            执行匹配
          </Button>

          {lowMatched && lowGroups.length === 0 && !lowLoading && (
            <div style={{ color: '#52c41a', marginBottom: 16 }}>所有版本均满足阈值要求</div>
          )}

          {lowGroups.length > 0 && (
            <>
              <div style={{ marginBottom: 12 }}>
                {lowSummary.will_delete > 0 && <Tag color="red">将删除 {lowSummary.will_delete}</Tag>}
                {lowSummary.keep_threshold > 0 && <Tag color="green">保留满足阈值 {lowSummary.keep_threshold}</Tag>}
                {lowSummary.keep_only > 0 && <Tag color="blue">保留唯一版本 {lowSummary.keep_only}</Tag>}
                {lowSummary.keep_disabled > 0 && <Tag>未启用 {lowSummary.keep_disabled}</Tag>}
              </div>

              <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center' }}>
                <Button size="small" onClick={toggleLowExpandAll}>
                  {lowExpanded.length === Object.keys(lowByBarcode).length ? '全部收起' : '全部展开'}
                </Button>
                <Text type="secondary">共 {Object.keys(lowByBarcode).length} 个条码</Text>
              </div>

              <Collapse
                activeKey={lowExpanded}
                onChange={keys => setLowExpanded(keys as string[])}
                items={lowPanelItems}
                style={{ marginBottom: 16 }}
              />

              <Divider />
              <Space>
                <Text strong>已选 {lowSelectedCount} 个版本</Text>
                <Button
                  danger
                  disabled={lowSelectedCount === 0}
                  loading={deleting}
                  onClick={() => openConfirm(
                    buildLowDeleteAction(false),
                    false,
                    '确认删除低版本',
                    `将删除 ${lowSelectedCount} 个版本（仅删除索引，文件保留）。此操作不可撤销。`,
                  )}
                >
                  仅删索引
                </Button>
                <Button
                  danger
                  type="primary"
                  disabled={lowSelectedCount === 0}
                  loading={deleting}
                  onClick={() => openConfirm(
                    buildLowDeleteAction(true),
                    true,
                    '确认删除低版本及文件',
                    `将删除 ${lowSelectedCount} 个版本的索引和磁盘文件。此操作不可撤销！`,
                  )}
                >
                  删索引和文件
                </Button>
              </Space>
            </>
          )}
        </div>
      ),
    },
  ];

  return (
    <>
      <Modal
        title="批量操作"
        open={visible}
        onCancel={onClose}
        width={960}
        footer={null}
        destroyOnClose
      >
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
      </Modal>

      <Modal
        title={confirmTitle}
        open={confirmVisible}
        onOk={handleConfirmDelete}
        onCancel={() => setConfirmVisible(false)}
        okText="确认删除"
        okButtonProps={{ danger: true }}
        cancelText="取消"
      >
        <p>{confirmBody}</p>
        {confirmDeleteFiles && (
          <p style={{ color: '#ff4d4f', fontWeight: 'bold' }}>⚠ 将同时删除磁盘上的文件！</p>
        )}
      </Modal>
    </>
  );
};

export default BatchOperations;
