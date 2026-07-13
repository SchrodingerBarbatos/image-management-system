import React, { useState, useEffect, useRef } from 'react';
import { Modal, Tabs, Button, Checkbox, Table, Space, InputNumber, Tag, Divider, message, Typography } from 'antd';
import { taskApi, BatchTaskInfo, DuplicateScanResultItem, LowVersionScanResultItem, PaginatedResults, DuplicateVersionScanResults, DuplicateVersionGroup } from '../services/api';
import { TaskList, TaskProgress } from './TaskList';
import { useTaskPolling } from '../hooks/useTaskPolling';

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

type DeleteTarget = {
  barcode: string;
  image_type: string;
  folder_ctime: string;
};

const toDeleteTarget = (r: DuplicateScanResultItem | LowVersionScanResultItem): DeleteTarget => ({
  barcode: r.barcode,
  image_type: r.image_type,
  folder_ctime: r.folder_ctime,
});

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

  // ===== Tab 3: Duplicate Versions =====
  const [dvLoading, setDvLoading] = useState(false);
  const [dvTaskId, setDvTaskId] = useState<number | null>(null);
  const [dvTaskStatus, setDvTaskStatus] = useState<string>('');
  const [dvCurrentTask, setDvCurrentTask] = useState<BatchTaskInfo | null>(null);
  const [dvTasks, setDvTasks] = useState<BatchTaskInfo[]>([]);
  const [dvResults, setDvResults] = useState<DuplicateVersionScanResults | null>(null);
  const [dvPolling, setDvPolling] = useState(false);
  const [dvResultsLoading, setDvResultsLoading] = useState(false);
  const [dvExpandedGroups, setDvExpandedGroups] = useState<Set<number>>(new Set());
  const dvPollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ===== Confirm modal =====
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [confirmDeleteFiles, setConfirmDeleteFiles] = useState(false);
  const [confirmAction, setConfirmAction] = useState<(() => Promise<void>) | null>(null);
  const [confirmTitle, setConfirmTitle] = useState('');
  const [confirmBody, setConfirmBody] = useState('');
  const [confirmOkText, setConfirmOkText] = useState('确认删除');

  // ===== Deleting state =====
  const [deleting, setDeleting] = useState(false);

  // Delete task polling
  const deletePolling = useTaskPolling({
    onComplete: () => {
      onCompleted();
      onClose();
    },
  });

  // Poll timer refs for cleanup on unmount
  const dupPollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lowPollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load task history on mount
  useEffect(() => {
    taskApi.listDuplicateScanTasks().then(setDupTasks).catch(() => {});
    taskApi.listLowVersionScanTasks().then(setLowTasks).catch(() => {});
    taskApi.listDuplicateVersionScanTasks().then(setDvTasks).catch(() => {});
  }, []);

  // Cleanup poll timers on unmount
  useEffect(() => {
    return () => {
      if (dupPollTimerRef.current) clearTimeout(dupPollTimerRef.current);
      if (lowPollTimerRef.current) clearTimeout(lowPollTimerRef.current);
      if (dvPollTimerRef.current) clearTimeout(dvPollTimerRef.current);
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

  const cancelDupPolling = () => {
    if (dupPollTimerRef.current) {
      clearTimeout(dupPollTimerRef.current);
      dupPollTimerRef.current = null;
    }
    setDupPolling(false);
  };

  const pollDupTask = (taskId: number) => {
    cancelDupPolling();
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
      // 翻页时保留 summary（summary 只在第 1 页无筛选时返回）
      if (!data.summary && lowResults?.summary) {
        data.summary = lowResults.summary;
      }
      setLowResults(data);
      setLowResultsPage(page);
    }).catch(() => {});
  };

  const cancelLowPolling = () => {
    if (lowPollTimerRef.current) {
      clearTimeout(lowPollTimerRef.current);
      lowPollTimerRef.current = null;
    }
    setLowPolling(false);
  };

  const pollLowTask = (taskId: number) => {
    cancelLowPolling();
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

  // ===== Duplicate version scan task helpers =====
  const refreshDvTasks = () => {
    taskApi.listDuplicateVersionScanTasks().then(setDvTasks).catch(() => {});
  };

  const loadDvResults = (taskId: number) => {
    setDvResultsLoading(true);
    taskApi.getDuplicateVersionScanResults(taskId).then(data => {
      setDvResults(data);
    }).catch(() => {}).finally(() => setDvResultsLoading(false));
  };

  const cancelDvPolling = () => {
    if (dvPollTimerRef.current) {
      clearTimeout(dvPollTimerRef.current);
      dvPollTimerRef.current = null;
    }
    setDvPolling(false);
  };

  const pollDvTask = (taskId: number) => {
    cancelDvPolling();
    setDvPolling(true);
    taskApi.getTask(taskId).then(task => {
      setDvTaskStatus(task.status);
      setDvCurrentTask(task);
      if (task.status === 'running' || task.status === 'queued') {
        dvPollTimerRef.current = setTimeout(() => pollDvTask(taskId), 2000);
      } else {
        setDvPolling(false);
        if (task.status === 'done') {
          loadDvResults(taskId);
        }
        refreshDvTasks();
      }
    }).catch(() => {
      setDvPolling(false);
    });
  };

  const handleScanDuplicateVersions = async () => {
    cancelDvPolling();
    setDvLoading(true);
    try {
      const task = await taskApi.createDuplicateVersionScan();
      setDvTaskId(task.id);
      setDvTaskStatus(task.status);
      setDvCurrentTask(task);
      setDvResults(null);
      setDvExpandedGroups(new Set());
      if (task.status === 'running' || task.status === 'queued') {
        pollDvTask(task.id);
      } else if (task.status === 'done') {
        loadDvResults(task.id);
        refreshDvTasks();
      }
    } catch {
      message.error('创建检测任务失败');
    } finally {
      setDvLoading(false);
    }
  };

  const handleSelectDvTask = (taskId: number) => {
    setDvTaskId(taskId);
    setDvResults(null);
    setDvCurrentTask(null);
    setDvExpandedGroups(new Set());
    loadDvResults(taskId);
  };

  const handleDeleteDvTask = async (taskId: number) => {
    try {
      await taskApi.deleteDuplicateVersionScanTask(taskId);
      if (taskId === dvTaskId) {
        setDvTaskId(null);
        setDvResults(null);
        setDvCurrentTask(null);
        setDvExpandedGroups(new Set());
      }
      refreshDvTasks();
      message.success('任务已删除');
    } catch {
      message.error('删除任务失败');
    }
  };

  const toggleDvGroup = (groupId: number) => {
    const next = new Set(dvExpandedGroups);
    if (next.has(groupId)) next.delete(groupId); else next.add(groupId);
    setDvExpandedGroups(next);
  };

  const handleChangeKeep = async (groupId: number, folderCtime: string) => {
    if (!dvTaskId) return;
    try {
      await taskApi.changeDuplicateVersionKeep(dvTaskId, groupId, folderCtime);
      loadDvResults(dvTaskId);
      message.success('已更改保留版本');
    } catch {
      message.error('更改失败');
    }
  };

  const handleExecuteCleanup = async (deleteFiles: boolean) => {
    if (!dvTaskId || !dvResults) return;
    // Collect all clean member IDs that are not yet deleted
    const cleanIds: number[] = [];
    for (const g of dvResults.groups) {
      for (const m of g.members) {
        if (m.role === 'clean' && m.delete_status === 'pending') {
          cleanIds.push(m.id);
        }
      }
    }
    if (cleanIds.length === 0) {
      message.warning('没有需要清理的版本');
      return;
    }
    try {
      const task = await taskApi.createBatchDeleteDuplicateVersionsTask(dvTaskId, cleanIds, deleteFiles);
      deletePolling.startPolling(task.id);
    } catch {
      message.error('创建清理任务失败');
    }
  };

  // ===== Tab 1: Scan duplicates =====
  const handleScanDuplicates = async () => {
    cancelDupPolling();
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
    cancelLowPolling();
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
  const toggleDupPage = () => {
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

  const BATCH_SIZE = 500; // 后端 page_size 上限
  const [selectAllLoading, setSelectAllLoading] = useState(false);

  const selectAllDup = async () => {
    if (!dupTaskId || !dupResults || selectAllLoading) return;
    setSelectAllLoading(true);
    const loadingKey = 'selectAll';
    try {
      message.loading({ content: '正在获取全部数据...', key: loadingKey, duration: 0 });
      const allIds = new Set<number>();
      const totalPages = Math.ceil(dupResults.total / BATCH_SIZE);
      for (let page = 1; page <= totalPages; page++) {
        const data = await taskApi.getDuplicateScanResults(dupTaskId, page, BATCH_SIZE);
        data.items.forEach(r => allIds.add(r.id));
      }
      setDupResultSelectedIds(allIds);
      message.success({ content: `已选中 ${allIds.size} 条`, key: loadingKey });
    } catch {
      message.error({ content: '获取全部结果失败', key: loadingKey });
    } finally {
      setSelectAllLoading(false);
    }
  };

  const toggleDupOne = (id: number) => {
    const next = new Set(dupResultSelectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    setDupResultSelectedIds(next);
  };

  // ===== Low version selection helpers =====
  const toggleLowPage = () => {
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

  const selectAllLow = async () => {
    if (!lowTaskId || !lowResults || selectAllLoading) return;
    setSelectAllLoading(true);
    const loadingKey = 'selectAll';
    try {
      message.loading({ content: '正在获取全部数据...', key: loadingKey, duration: 0 });
      const allSelectableIds = new Set<number>();
      const totalPages = Math.ceil(lowResults.total / BATCH_SIZE);
      for (let page = 1; page <= totalPages; page++) {
        const data = await taskApi.getLowVersionScanResults(lowTaskId, page, BATCH_SIZE);
        data.items.filter(r => r.status_tag === 'will_delete').forEach(r => allSelectableIds.add(r.id));
      }
      setLowResultSelectedIds(allSelectableIds);
      message.success({ content: `已选中 ${allSelectableIds.size} 条待删除项`, key: loadingKey });
    } catch {
      message.error({ content: '获取全部结果失败', key: loadingKey });
    } finally {
      setSelectAllLoading(false);
    }
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
  const openConfirm = (action: () => Promise<void>, deleteFiles: boolean, title: string, body: string, okText?: string) => {
    setConfirmAction(() => action);
    setConfirmDeleteFiles(deleteFiles);
    setConfirmTitle(title);
    setConfirmBody(body);
    setConfirmOkText(okText || '确认删除');
    setConfirmVisible(true);
  };

  const handleConfirmDelete = async () => {
    setConfirmVisible(false);
    if (!confirmAction) return;
    setDeleting(true);
    try {
      await confirmAction();
      // For async delete tasks, we don't show success here - it will be shown when the task completes
      // For sync operations, we still show success
    } catch (err: any) {
      message.error(err?.response?.data?.error || '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  // ===== Delete action builders =====
  const buildDupDeleteAction = (deleteFiles: boolean) => async () => {
    if (!dupTaskId || !dupResults) return;
    const idSet = new Set(dupResultSelectedIds);
    const selectedItems: DeleteTarget[] = [];
    const totalPages = Math.ceil(dupResults.total / BATCH_SIZE);
    for (let page = 1; page <= totalPages; page++) {
      const data = await taskApi.getDuplicateScanResults(dupTaskId, page, BATCH_SIZE);
      data.items.filter(r => idSet.has(r.id)).forEach(r => selectedItems.push(toDeleteTarget(r)));
    }
    if (selectedItems.length === 0) return;
    const task = await taskApi.createBatchDeleteDuplicatesTask(selectedItems, deleteFiles);
    deletePolling.startPolling(task.id);
  };

  const buildLowDeleteAction = (deleteFiles: boolean) => async () => {
    if (!lowTaskId || !lowResults) return;
    const idSet = new Set(lowResultSelectedIds);
    const selectedItems: DeleteTarget[] = [];
    const totalPages = Math.ceil(lowResults.total / BATCH_SIZE);
    for (let page = 1; page <= totalPages; page++) {
      const data = await taskApi.getLowVersionScanResults(lowTaskId, page, BATCH_SIZE);
      data.items
        .filter(r => r.status_tag === 'will_delete' && idSet.has(r.id))
        .forEach(r => selectedItems.push(toDeleteTarget(r)));
    }
    if (selectedItems.length === 0) return;
    // Use thresholds from the scan task (not the live form inputs) so revalidation
    // matches the results the user selected after scanning.
    let scanMainThreshold = mainThreshold;
    let scanDetailThreshold = detailThreshold;
    try {
      const scanTask = await taskApi.getTask(lowTaskId);
      const params = typeof scanTask.params_json === 'string'
        ? JSON.parse(scanTask.params_json || '{}')
        : (scanTask as unknown as { params?: Record<string, number> }).params || {};
      if (typeof params.main_threshold === 'number') scanMainThreshold = params.main_threshold;
      if (typeof params.detail_threshold === 'number') scanDetailThreshold = params.detail_threshold;
    } catch {
      // fall back to form thresholds
    }
    const task = await taskApi.createBatchDeleteLowVersionsTask(
      selectedItems, deleteFiles, scanMainThreshold, scanDetailThreshold,
    );
    deletePolling.startPolling(task.id);
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

  const dvCleanCount = dvResults ? dvResults.groups.reduce(
    (sum, g) => sum + g.members.filter(m => m.role === 'clean' && m.delete_status === 'pending').length, 0
  ) : 0;
  const dvKeepCount = dvResults?.summary.total_keep ?? 0;

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
              onRefresh={refreshDupTasks}
            />
          </div>

          {dupResults && dupResults.items.length === 0 && (
            <div style={{ color: '#52c41a', marginBottom: 16 }}>未发现重复文件夹</div>
          )}

          {dupResults && dupResults.items.length > 0 && (
            <>
              <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center' }}>
                <Checkbox checked={dupAllChecked} indeterminate={dupIndeterminate} onChange={toggleDupPage}>
                  全选当前页
                </Checkbox>
                <Button size="small" onClick={selectAllDup}>全选全部 ({dupResults.total})</Button>
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
          <div style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>
            删除图片数量少于阈值的版本（每个条码至少保留一个版本）
          </div>
          <Space direction="vertical" style={{ marginBottom: 16 }}>
            <Space>
              <Checkbox checked={mainEnabled} onChange={e => setMainEnabled(e.target.checked)}>
                主图删除图片数 &lt;
              </Checkbox>
              <InputNumber min={1} value={mainThreshold} onChange={v => setMainThreshold(v || 3)} disabled={!mainEnabled} style={{ width: 70 }} />
              <span style={{ color: '#666' }}>张的版本</span>
            </Space>
            <Space>
              <Checkbox checked={detailEnabled} onChange={e => setDetailEnabled(e.target.checked)}>
                详情图删除图片数 &lt;
              </Checkbox>
              <InputNumber min={1} value={detailThreshold} onChange={v => setDetailThreshold(v || 5)} disabled={!detailEnabled} style={{ width: 70 }} />
              <span style={{ color: '#666' }}>张的版本</span>
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
              onRefresh={refreshLowTasks}
            />
          </div>

          {lowResults && lowResults.items.length === 0 && (
            <div style={{ color: '#52c41a', marginBottom: 16 }}>所有版本均满足阈值要求</div>
          )}

          {lowResults && lowResults.items.length > 0 && (
            <>
              <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center' }}>
                <Checkbox checked={lowAllChecked} indeterminate={lowIndeterminate} onChange={toggleLowPage}>
                  全选当前页（仅将删除项）
                </Checkbox>
                <Button size="small" onClick={selectAllLow}>
                  全选全部待删除项{lowResults.summary?.will_delete != null ? ` (${lowResults.summary.will_delete})` : ''}
                </Button>
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
    {
      key: 'duplicateVersions',
      label: '检测重复版本',
      children: (
        <div>
          <div style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>
            检测图片张数相同、对应位置图片视觉相同的重复版本（支持 MD5 精确匹配和 pHash 感知哈希相似度）
          </div>

          <Button type="primary" loading={dvLoading} onClick={handleScanDuplicateVersions} style={{ marginBottom: 16 }}>
            检测重复版本
          </Button>

          {dvPolling && dvCurrentTask && (
            <div style={{ marginBottom: 16 }}>
              <TaskProgress task={dvCurrentTask} />
            </div>
          )}

          {dvTaskStatus === 'error' && !dvPolling && (
            <div style={{ color: '#ff4d4f', marginBottom: 16 }}>任务执行失败</div>
          )}

          <Divider>任务历史</Divider>
          <div style={{ marginBottom: 16 }}>
            <TaskList
              tasks={dvTasks}
              onSelectTask={handleSelectDvTask}
              onDeleteTask={handleDeleteDvTask}
              selectedTaskId={dvTaskId}
              typeLabel="重复版本检测"
              onRefresh={refreshDvTasks}
            />
          </div>

          {dvResultsLoading && (
            <div style={{ marginBottom: 16, color: '#1890ff' }}>正在加载结果...</div>
          )}

          {dvResults && dvResults.groups.length === 0 && !dvResultsLoading && (
            <div style={{ color: '#52c41a', marginBottom: 16 }}>未发现重复版本</div>
          )}

          {dvResults && dvResults.groups.length > 0 && (
            <>
              <div style={{ marginBottom: 12, display: 'flex', gap: 16, alignItems: 'center' }}>
                <Text type="secondary">
                  发现 {dvResults.summary.total_groups} 组重复版本，
                  保留 {dvResults.summary.total_keep} 个，
                  建议清理 {dvResults.summary.total_clean} 个
                  {dvResults.summary.total_deleted > 0 && `，已清理 ${dvResults.summary.total_deleted} 个`}
                </Text>
              </div>

              <div style={{ maxHeight: 400, overflowY: 'auto', marginBottom: 16 }}>
                {dvResults.groups.map(group => {
                  const isExpanded = dvExpandedGroups.has(group.group_id);
                  const keepMember = group.members.find(m => m.role === 'keep' || m.role === 'user_selected');
                  const cleanMembers = group.members.filter(m => m.role === 'clean');
                  return (
                    <div key={group.group_id} style={{ border: '1px solid #f0f0f0', borderRadius: 6, marginBottom: 8, padding: 12 }}>
                      <div
                        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }}
                        onClick={() => toggleDvGroup(group.group_id)}
                      >
                        <Space>
                          <Text strong>{group.barcode}</Text>
                          <Tag>{TYPE_LABELS[group.image_type] || group.image_type}</Tag>
                          <Text type="secondary">{group.image_count} 张图</Text>
                          <Text type="secondary">{group.members.length} 个版本</Text>
                        </Space>
                        <Space>
                          {keepMember && (
                            <Tag color="green">保留: {keepMember.version_label} {keepMember.keep_reason && `(${keepMember.keep_reason})`}</Tag>
                          )}
                          <Text type="secondary">{isExpanded ? '▲' : '▼'}</Text>
                        </Space>
                      </div>

                      {isExpanded && (
                        <div style={{ marginTop: 12 }}>
                          <Table
                            rowKey="id"
                            size="small"
                            pagination={false}
                            dataSource={group.members}
                            columns={[
                              {
                                title: '角色', width: 80,
                                render: (_: unknown, m: typeof group.members[0]) => {
                                  if (m.role === 'keep' || m.role === 'user_selected') return <Tag color="green">保留</Tag>;
                                  return <Tag color="red">清理</Tag>;
                                },
                              },
                              { title: '版本', dataIndex: 'version_label', width: 60 },
                              { title: '文件夹时间', dataIndex: 'folder_ctime', width: 180, render: (v: string) => v.slice(0, 19) },
                              { title: '图片数', dataIndex: 'image_count', width: 60 },
                              { title: '大小', dataIndex: 'total_file_size', width: 80, render: (v: number) => fmtSize(v) },
                              {
                                title: '保留原因', dataIndex: 'keep_reason', width: 120,
                                render: (v: string) => v || '-',
                              },
                              {
                                title: '状态', width: 100,
                                render: (_: unknown, m: typeof group.members[0]) => {
                                  if (m.delete_status === 'deleted') return <Tag color="success">已清理</Tag>;
                                  if (m.delete_status === 'failed') return <Tag color="error">失败</Tag>;
                                  if (m.delete_status === 'skipped') return <Tag color="warning">跳过</Tag>;
                                  return <Tag>待处理</Tag>;
                                },
                              },
                              {
                                title: '操作', width: 180,
                                render: (_: unknown, m: typeof group.members[0]) => (
                                  <Space size="small">
                                    {m.role === 'clean' && m.delete_status === 'pending' && (
                                      <Button size="small" type="link" onClick={() => handleChangeKeep(group.group_id, m.folder_ctime)}>
                                        设为保留
                                      </Button>
                                    )}
                                  </Space>
                                ),
                              },
                            ]}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              <Divider />
              <Space>
                <Button
                  danger
                  loading={deleting}
                  onClick={() => {
                    if (!dvResults) return;
                    openConfirm(
                      async () => { await handleExecuteCleanup(false); },
                      false,
                      '确认清理重复版本',
                      `将保留 ${dvKeepCount} 个版本，清理 ${dvCleanCount} 个重复版本（仅删除索引，文件保留）。此操作不可撤销。`,
                    );
                  }}
                >
                  仅删索引
                </Button>
                <Button
                  danger
                  type="primary"
                  loading={deleting}
                  onClick={() => {
                    if (!dvResults) return;
                    openConfirm(
                      async () => { await handleExecuteCleanup(true); },
                      true,
                      '确认清理重复版本及文件',
                      `将保留 ${dvKeepCount} 个版本，清理 ${dvCleanCount} 个重复版本的索引和磁盘文件。此操作不可撤销！`,
                    );
                  }}
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
        {deletePolling.polling && deletePolling.currentTask && (
          <div style={{ marginBottom: 16, padding: 12, background: '#f5f5f5', borderRadius: 6 }}>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>正在删除...</div>
            <TaskProgress task={deletePolling.currentTask} />
          </div>
        )}
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
      </Modal>

      <Modal
        title={confirmTitle}
        open={confirmVisible}
        onOk={handleConfirmDelete}
        onCancel={() => setConfirmVisible(false)}
        okText={confirmOkText}
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
