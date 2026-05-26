import React, { useState, useEffect, useRef } from 'react';
import { Modal, Tabs, Button, Checkbox, Table, Space, InputNumber, Tag, Divider, message, Typography } from 'antd';
import { taskApi, BatchTaskInfo, DuplicateScanResultItem, LowVersionScanResultItem, PaginatedResults } from '../services/api';
import { TaskList, TaskProgress } from './TaskList';

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
const DELETE_STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '待删除' },
  deleted: { color: 'success', label: '已删除' },
  skipped: { color: 'warning', label: '已跳过' },
  failed: { color: 'error', label: '失败' },
};

function fmtSize(bytes: number): string {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

const BatchOperations: React.FC<Props> = ({ visible, onClose, onCompleted }) => {
  const [activeTab, setActiveTab] = useState<string>('duplicates');

  // ===== Tab 1: Duplicates =====
  const [dupLoading, setDupLoading] = useState(false);
  const [dupTaskId, setDupTaskId] = useState<number | null>(null);
  const [dupTaskStatus, setDupTaskStatus] = useState<string>('');
  const [dupCurrentTask, setDupCurrentTask] = useState<BatchTaskInfo | null>(null);
  const [dupTasks, setDupTasks] = useState<BatchTaskInfo[]>([]);
  const [dupResults, setDupResults] = useState<PaginatedResults<DuplicateScanResultItem> | null>(null);
  const [dupResultsPage, setDupResultsPage] = useState(1);
  const [dupResultsPageSize] = useState(100);
  const [dupResultSelectedIds, setDupResultSelectedIds] = useState<Set<number>>(new Set());
  const [dupPolling, setDupPolling] = useState(false);

  // ===== Tab 2: Low Versions =====
  const [mainEnabled, setMainEnabled] = useState(false);
  const [mainThreshold, setMainThreshold] = useState(3);
  const [detailEnabled, setDetailEnabled] = useState(false);
  const [detailThreshold, setDetailThreshold] = useState(5);
  const [lowLoading, setLowLoading] = useState(false);
  const [lowTaskId, setLowTaskId] = useState<number | null>(null);
  const [lowTaskStatus, setLowTaskStatus] = useState<string>('');
  const [lowCurrentTask, setLowCurrentTask] = useState<BatchTaskInfo | null>(null);
  const [lowTasks, setLowTasks] = useState<BatchTaskInfo[]>([]);
  const [lowResults, setLowResults] = useState<PaginatedResults<LowVersionScanResultItem> | null>(null);
  const [lowResultsPage, setLowResultsPage] = useState(1);
  const [lowResultsPageSize] = useState(100);
  const [lowResultSelectedIds, setLowResultSelectedIds] = useState<Set<number>>(new Set());
  const [lowPolling, setLowPolling] = useState(false);

  // ===== Confirm modal =====
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [confirmDeleteFiles, setConfirmDeleteFiles] = useState(false);
  const [confirmAction, setConfirmAction] = useState<(() => Promise<void>) | null>(null);
  const [confirmTitle, setConfirmTitle] = useState('');
  const [confirmBody, setConfirmBody] = useState('');

  // ===== Deleting state =====
  const [deleting, setDeleting] = useState(false);

  // Poll timer refs for cleanup on unmount
  const dupPollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lowPollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load task history on mount
  useEffect(() => {
    taskApi.listDuplicateScanTasks().then(setDupTasks).catch(() => {});
    taskApi.listLowVersionScanTasks().then(setLowTasks).catch(() => {});
  }, []);

  // Cleanup poll timers on unmount
  useEffect(() => {
    return () => {
      if (dupPollTimerRef.current) clearTimeout(dupPollTimerRef.current);
      if (lowPollTimerRef.current) clearTimeout(lowPollTimerRef.current);
    };
  }, []);

  // ===== Duplicate scan task helpers =====
  const refreshDupTasks = () => {
    taskApi.listDuplicateScanTasks().then(setDupTasks).catch(() => {});
  };

  const loadDupResults = (taskId: number, page: number) => {
    taskApi.getDuplicateScanResults(taskId, page, dupResultsPageSize).then(data => {
      setDupResults(data);
      setDupResultsPage(page);
    }).catch(() => {});
  };

  const pollDupTask = (taskId: number) => {
    setDupPolling(true);
    taskApi.getTask(taskId).then(task => {
      setDupTaskStatus(task.status);
      setDupCurrentTask(task);
      if (task.status === 'running' || task.status === 'queued') {
        dupPollTimerRef.current = setTimeout(() => pollDupTask(taskId), 2000);
      } else {
        setDupPolling(false);
        if (task.status === 'done') {
          loadDupResults(taskId, 1);
        }
        refreshDupTasks();
      }
    }).catch(() => {
      setDupPolling(false);
    });
  };

  // ===== Low version scan task helpers =====
  const refreshLowTasks = () => {
    taskApi.listLowVersionScanTasks().then(setLowTasks).catch(() => {});
  };

  const loadLowResults = (taskId: number, page: number) => {
    taskApi.getLowVersionScanResults(taskId, page, lowResultsPageSize).then(data => {
      setLowResults(data);
      setLowResultsPage(page);
    }).catch(() => {});
  };

  const pollLowTask = (taskId: number) => {
    setLowPolling(true);
    taskApi.getTask(taskId).then(task => {
      setLowTaskStatus(task.status);
      setLowCurrentTask(task);
      if (task.status === 'running' || task.status === 'queued') {
        lowPollTimerRef.current = setTimeout(() => pollLowTask(taskId), 2000);
      } else {
        setLowPolling(false);
        if (task.status === 'done') {
          loadLowResults(taskId, 1);
        }
        refreshLowTasks();
      }
    }).catch(() => {
      setLowPolling(false);
    });
  };

  // ===== Tab 1: Scan duplicates =====
  const handleScanDuplicates = async () => {
    setDupLoading(true);
    try {
      const task = await taskApi.createDuplicateScan();
      setDupTaskId(task.id);
      setDupTaskStatus(task.status);
      setDupCurrentTask(task);
      setDupResults(null);
      setDupResultSelectedIds(new Set());
      if (task.status === 'running' || task.status === 'queued') {
        pollDupTask(task.id);
      } else if (task.status === 'done') {
        loadDupResults(task.id, 1);
        refreshDupTasks();
      }
    } catch {
      message.error('创建扫描任务失败');
    } finally {
      setDupLoading(false);
    }
  };

  // ===== Tab 2: Match low versions =====
  const handleMatchLow = async () => {
    if (!mainEnabled && !detailEnabled) {
      message.warning('至少启用一项阈值');
      return;
    }
    setLowLoading(true);
    try {
      const task = await taskApi.createLowVersionScan({
        main_enabled: mainEnabled,
        main_threshold: mainThreshold,
        detail_enabled: detailEnabled,
        detail_threshold: detailThreshold,
      });
      setLowTaskId(task.id);
      setLowTaskStatus(task.status);
      setLowCurrentTask(task);
      setLowResults(null);
      setLowResultSelectedIds(new Set());
      if (task.status === 'running' || task.status === 'queued') {
        pollLowTask(task.id);
      } else if (task.status === 'done') {
        loadLowResults(task.id, 1);
        refreshLowTasks();
      }
    } catch {
      message.error('创建匹配任务失败');
    } finally {
      setLowLoading(false);
    }
  };

  // ===== Duplicate selection helpers =====
  const toggleDupAll = () => {
    if (!dupResults || dupResults.items.length === 0) return;
    const pageIds = dupResults.items.map(r => r.id);
    const allPageSelected = pageIds.every(id => dupResultSelectedIds.has(id));
    const next = new Set(dupResultSelectedIds);
    if (allPageSelected) {
      pageIds.forEach(id => next.delete(id));
    } else {
      pageIds.forEach(id => next.add(id));
    }
    setDupResultSelectedIds(next);
  };

  const toggleDupOne = (id: number) => {
    const next = new Set(dupResultSelectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    setDupResultSelectedIds(next);
  };

  // ===== Low version selection helpers =====
  const toggleLowAll = () => {
    if (!lowResults || lowResults.items.length === 0) return;
    const selectable = lowResults.items.filter(r => r.status_tag === 'will_delete');
    if (selectable.length === 0) return;
    const selectableIds = selectable.map(r => r.id);
    const allSelected = selectableIds.every(id => lowResultSelectedIds.has(id));
    const next = new Set(lowResultSelectedIds);
    if (allSelected) {
      selectableIds.forEach(id => next.delete(id));
    } else {
      selectableIds.forEach(id => next.add(id));
    }
    setLowResultSelectedIds(next);
  };

  const toggleLowOne = (id: number) => {
    const next = new Set(lowResultSelectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    setLowResultSelectedIds(next);
  };

  // ===== Task history handlers =====
  const handleSelectDupTask = (taskId: number) => {
    setDupTaskId(taskId);
    setDupResults(null);
    setDupResultSelectedIds(new Set());
    setDupCurrentTask(null);
    loadDupResults(taskId, 1);
  };

  const handleDeleteDupTask = async (taskId: number) => {
    try {
      await taskApi.deleteDuplicateScanTask(taskId);
      if (taskId === dupTaskId) {
        setDupTaskId(null);
        setDupResults(null);
        setDupResultSelectedIds(new Set());
        setDupCurrentTask(null);
      }
      refreshDupTasks();
      message.success('任务已删除');
    } catch {
      message.error('删除任务失败');
    }
  };

  const handleSelectLowTask = (taskId: number) => {
    setLowTaskId(taskId);
    setLowResults(null);
    setLowResultSelectedIds(new Set());
    setLowCurrentTask(null);
    loadLowResults(taskId, 1);
  };

  const handleDeleteLowTask = async (taskId: number) => {
    try {
      await taskApi.deleteLowVersionScanTask(taskId);
      if (taskId === lowTaskId) {
        setLowTaskId(null);
        setLowResults(null);
        setLowResultSelectedIds(new Set());
        setLowCurrentTask(null);
      }
      refreshLowTasks();
      message.success('任务已删除');
    } catch {
      message.error('删除任务失败');
    }
  };

  // ===== Confirm & Delete =====
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

  // ===== Delete action builders =====
  const buildDupDeleteAction = (deleteFiles: boolean) => async () => {
    if (!dupTaskId) return;
    const ids = Array.from(dupResultSelectedIds);
    await taskApi.deleteDuplicateScanResults(dupTaskId, ids, deleteFiles);
  };

  const buildLowDeleteAction = (deleteFiles: boolean) => async () => {
    if (!lowTaskId) return;
    const ids = Array.from(lowResultSelectedIds);
    await taskApi.deleteLowVersionScanResults(lowTaskId, ids, deleteFiles);
  };

  // ===== Table columns =====
  const dupResultColumns = [
    { title: '条码', dataIndex: 'barcode', width: 140 },
    { title: '类型', dataIndex: 'image_type', width: 70, render: (t: string) => TYPE_LABELS[t] || t },
    { title: '版本', dataIndex: 'version_label', width: 70 },
    { title: '文件夹时间', dataIndex: 'folder_ctime', width: 180, render: (v: string) => v.slice(0, 19) },
    { title: '图片数', dataIndex: 'image_count', width: 60 },
    { title: '大小', dataIndex: 'total_file_size', width: 80, render: (v: number) => fmtSize(v) },
    {
      title: '删除状态', dataIndex: 'delete_status', width: 80,
      render: (s: string) => {
        const cfg = DELETE_STATUS_CONFIG[s] || { color: 'default', label: s };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '选择', width: 50,
      render: (_: unknown, r: DuplicateScanResultItem) => (
        <Checkbox checked={dupResultSelectedIds.has(r.id)} onChange={() => toggleDupOne(r.id)} />
      ),
    },
  ];

  const lowResultColumns = [
    { title: '条码', dataIndex: 'barcode', width: 140 },
    { title: '类型', dataIndex: 'image_type', width: 70, render: (t: string) => TYPE_LABELS[t] || t },
    { title: '版本', dataIndex: 'version_label', width: 60 },
    { title: '文件夹时间', dataIndex: 'folder_ctime', width: 180, render: (v: string) => v.slice(0, 19) },
    { title: '图片数', dataIndex: 'image_count', width: 60 },
    { title: '大小', dataIndex: 'total_file_size', width: 80, render: (v: number) => fmtSize(v) },
    {
      title: '状态', width: 150,
      render: (_: unknown, r: LowVersionScanResultItem) => {
        const cfg = STATUS_CONFIG[r.status_tag];
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '选择', width: 50,
      render: (_: unknown, r: LowVersionScanResultItem) => {
        if (r.status_tag !== 'will_delete') return null;
        return <Checkbox checked={lowResultSelectedIds.has(r.id)} onChange={() => toggleLowOne(r.id)} />;
      },
    },
  ];

  // ===== Computed values =====
  const dupPageIds = dupResults ? dupResults.items.map(r => r.id) : [];
  const dupAllChecked = dupPageIds.length > 0 && dupPageIds.every(id => dupResultSelectedIds.has(id));
  const dupIndeterminate = dupPageIds.some(id => dupResultSelectedIds.has(id)) && !dupAllChecked;
  const dupSelectedCount = dupResultSelectedIds.size;

  const lowSelectable = lowResults ? lowResults.items.filter(r => r.status_tag === 'will_delete') : [];
  const lowSelectableCount = lowSelectable.length;
  const lowAllChecked = lowSelectableCount > 0 && lowSelectable.every(r => lowResultSelectedIds.has(r.id));
  const lowIndeterminate = lowSelectable.some(r => lowResultSelectedIds.has(r.id)) && !lowAllChecked;
  const lowSelectedCount = lowResultSelectedIds.size;

  // ===== Tab items =====
  const tabItems = [
    {
      key: 'duplicates',
      label: '删除重复文件夹',
      children: (
        <div>
          <Button type="primary" loading={dupLoading} onClick={handleScanDuplicates} style={{ marginBottom: 16 }}>
            扫描重复
          </Button>

          {dupPolling && dupCurrentTask && (
            <div style={{ marginBottom: 16 }}>
              <TaskProgress task={dupCurrentTask} />
            </div>
          )}

          {dupTaskStatus === 'error' && !dupPolling && (
            <div style={{ color: '#ff4d4f', marginBottom: 16 }}>任务执行失败</div>
          )}

          <Divider>任务历史</Divider>
          <div style={{ marginBottom: 16 }}>
            <TaskList
              tasks={dupTasks}
              onSelectTask={handleSelectDupTask}
              onDeleteTask={handleDeleteDupTask}
              selectedTaskId={dupTaskId}
              typeLabel="重复扫描"
            />
          </div>

          {dupResults && dupResults.items.length === 0 && (
            <div style={{ color: '#52c41a', marginBottom: 16 }}>未发现重复文件夹</div>
          )}

          {dupResults && dupResults.items.length > 0 && (
            <>
              <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center' }}>
                <Checkbox checked={dupAllChecked} indeterminate={dupIndeterminate} onChange={toggleDupAll}>
                  全选
                </Checkbox>
                <Text type="secondary">共 {dupResults.total} 条结果</Text>
              </div>

              <Table
                rowKey="id"
                columns={dupResultColumns}
                dataSource={dupResults.items}
                size="small"
                pagination={{
                  current: dupResults.page,
                  pageSize: dupResults.page_size,
                  total: dupResults.total,
                  showSizeChanger: false,
                  onChange: (page) => loadDupResults(dupTaskId!, page),
                }}
                style={{ marginBottom: 16 }}
              />

              <Divider />
              <Space>
                <Text strong>已选 {dupSelectedCount} 项</Text>
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

          {lowPolling && lowCurrentTask && (
            <div style={{ marginBottom: 16 }}>
              <TaskProgress task={lowCurrentTask} />
            </div>
          )}

          {lowTaskStatus === 'error' && !lowPolling && (
            <div style={{ color: '#ff4d4f', marginBottom: 16 }}>任务执行失败</div>
          )}

          <Divider>任务历史</Divider>
          <div style={{ marginBottom: 16 }}>
            <TaskList
              tasks={lowTasks}
              onSelectTask={handleSelectLowTask}
              onDeleteTask={handleDeleteLowTask}
              selectedTaskId={lowTaskId}
              typeLabel="低版本扫描"
            />
          </div>

          {lowResults && lowResults.items.length === 0 && (
            <div style={{ color: '#52c41a', marginBottom: 16 }}>所有版本均满足阈值要求</div>
          )}

          {lowResults && lowResults.items.length > 0 && (
            <>
              <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center' }}>
                <Checkbox checked={lowAllChecked} indeterminate={lowIndeterminate} onChange={toggleLowAll}>
                  全选（仅将删除项）
                </Checkbox>
                <Text type="secondary">共 {lowResults.total} 条结果</Text>
              </div>

              <Table
                rowKey="id"
                columns={lowResultColumns}
                dataSource={lowResults.items}
                size="small"
                pagination={{
                  current: lowResults.page,
                  pageSize: lowResults.page_size,
                  total: lowResults.total,
                  showSizeChanger: false,
                  onChange: (page) => loadLowResults(lowTaskId!, page),
                }}
                style={{ marginBottom: 16 }}
              />

              <Divider />
              <Space>
                <Text strong>已选 {lowSelectedCount} 项</Text>
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
