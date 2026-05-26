import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

export interface ScanRoot {
  id: number; path: string; recursive: boolean; enabled: boolean;
  allow_fuzzy: boolean; fuzzy_image_type: string;
}

export interface ScanLog {
  id: number; action: string; status: string;
  message: string; details: string; created_at: string;
}

export interface ImageRec {
  id: number; barcode: string; image_type: string; sequence: number;
  filename: string; ext: string; file_path: string; file_size: number;
  md5_hash: string; // stores size_mtime fingerprint, not actual MD5
  content_md5: string; // real MD5 of file content, computed at scan time
  folder_path: string; folder_ctime: string;
  scan_root_id: number; confirmed: boolean; status: string;
  created_at: string; updated_at: string;
}

export interface ImageVersion {
  id: number; barcode: string; image_type: string; version_label: string;
  folder_ctime: string; content_hash: string; is_latest: boolean;
  created_at: string;
  duplicate_mtimes: string[];
}

export interface BarcodeRec {
  barcode: string;
  main_count: number;
  detail_count: number;
  main_versions: number;
  detail_versions: number;
}

export interface Paginated<T> {
  items: T[]; total: number; page: number; page_size: number;
}

export interface ImageListParams {
  barcode?: string; barcode_exact?: string; image_type?: string; scan_root_id?: number;
  page?: number; page_size?: number; sort?: string; order?: string;
}

export interface BarcodeListParams {
  barcode?: string;
  page?: number; page_size?: number; sort?: string; order?: string;
}

export const scanRootApi = {
  list: () => api.get<ScanRoot[]>('/scan-roots').then(r => r.data),
  create: (data: { path: string; recursive?: boolean; allow_fuzzy?: boolean; fuzzy_image_type?: string }) =>
    api.post<ScanRoot>('/scan-roots', data).then(r => r.data),
  update: (id: number, data: { recursive?: boolean; enabled?: boolean; allow_fuzzy?: boolean; fuzzy_image_type?: string }) =>
    api.put<ScanRoot>(`/scan-roots/${id}`, data).then(r => r.data),
  delete: (id: number) => api.delete(`/scan-roots/${id}`).then(r => r.data),
};

export const scanApi = {
  trigger: (data?: { root_ids?: number[]; scan_mode?: 'full' | 'incremental' }) =>
    api.post('/scan', data || {}).then(r => r.data),
  checkNew: (root_ids: number[]) =>
    api.post<{ new_root_ids: number[] }>('/scan-roots/check-new', { root_ids }).then(r => r.data),
  getActive: () =>
    api.get<ScanJobStatus & { job_id: string } | null>('/scan/status').then(r => r.data),
  getStatus: (jobId: string) =>
    api.get<ScanJobStatus>(`/scan/status/${jobId}`).then(r => r.data),
};

export interface ScanJobStatus {
  status: 'running' | 'done' | 'error';
  phase: 'starting' | 'scan_start' | 'scanning' | 'thumbnails' | 'versioning' | 'root_done' | 'done' | 'error';
  current_root_path?: string;
  current_root_index?: number;
  total_roots?: number;
  current_file?: string;
  added: number;
  skipped: number;
  broken_cleaned: number;
  broken_new: number;
  thumbnail_total: number;
  thumbnail_current: number;
  error?: string;
}

export const scanLogApi = {
  list: () => api.get<ScanLog[]>('/scan-logs').then(r => r.data),
};

export const imageApi = {
  list: (params: ImageListParams) =>
    api.get<Paginated<ImageRec>>('/images', { params }).then(r => r.data),
  get: (id: number) =>
    api.get<{ image: ImageRec; versions: ImageVersion[] }>(`/images/${id}`).then(r => r.data),
  update: (id: number, data: Partial<ImageRec>) =>
    api.put<ImageRec>(`/images/${id}`, data).then(r => r.data),
  delete: (id: number, deleteFile = false) =>
    api.delete(`/images/${id}`, { params: { delete_file: deleteFile } }).then(r => r.data),
  thumbnailUrl: (id: number) => `/api/thumbnails/${id}`,
  fileUrl: (id: number) => `/api/images/${id}/file`,
  batchDelete: (ids: number[], deleteFile = false) =>
    api.post('/images/batch-delete', { ids, delete_file: deleteFile }).then(r => r.data),
  batchExport: (ids: number[], image_type?: string, flat?: boolean) =>
    api.post<{ task_id: number; total: number; scanroot_excluded: number; version_filtered: number }>('/images/batch-export', { ids, image_type, flat }).then(r => r.data),
  getBarcodeImageIds: (barcodes: string[]) =>
    api.post<{ image_ids: number[]; barcode_counts: Record<string, number> }>('/barcodes/image-ids', { barcodes }).then(r => r.data),
};

export const barcodeApi = {
  list: (params: BarcodeListParams) =>
    api.get<Paginated<BarcodeRec>>('/barcodes', { params }).then(r => r.data),
  deleteDuplicateImages: (barcode: string, folderCtime: string, imageType: string, deleteFile = false) =>
    api.delete(`/barcodes/${barcode}/duplicate-images`, { params: { folder_ctime: folderCtime, image_type: imageType, delete_file: deleteFile } }).then(r => r.data),
};

export const pendingApi = {
  count: () => api.get<{ count: number }>('/pending/count').then(r => r.data.count),
  list: () => api.get<ImageRec[]>('/pending').then(r => r.data),
  confirm: (items: { id: number; image_type: string }[]) =>
    api.post('/pending/confirm', items).then(r => r.data),
  ignore: (id: number) => api.delete(`/pending/${id}`),
};

export const exportApi = {
  uploadExcel: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return api.post<{ columns: string[]; sheets: string[]; sheet_columns: Record<string, string[]>; upload_id: string }>('/export/excel', fd).then(r => r.data);
  },
  generateZip: (data: { barcode_column: string; image_type: string; upload_id: string; sheet_name?: string; selected_barcodes?: string[]; flat?: boolean }) =>
    api.post<{ task_id: number; total_images: number; total_barcodes: number; excluded_barcodes: number }>('/export/zip', data).then(r => r.data),
  getProgress: (taskId: number) =>
    api.get<{ status: string; progress: number; total: number; error_message?: string }>(`/export/progress/${taskId}`).then(r => r.data),
  deleteTask: (taskId: number) =>
    api.delete(`/export/tasks/${taskId}`).then(r => r.data),
  listTasks: () =>
    api.get<{ id: number; status: string; total_images: number; created_at: string; file_available: boolean; error_message?: string; has_detail: boolean }[]>('/export/tasks').then(r => r.data),
  downloadUrl: (taskId: number) => `/api/export/download/${taskId}`,
  detailUrl: (taskId: number) => `/api/export/tasks/${taskId}/detail`,
};

export const versionApi = {
  delete: (id: number, deleteFile = false) =>
    api.delete(`/versions/${id}`, { params: { delete_file: deleteFile } }).then(r => r.data),
};

export interface BarcodeSetting {
  barcode: string;
  default_main_ctime: string;
  default_detail_ctime: string;
}

export const barcodeSettingApi = {
  get: (barcode: string) =>
    api.get<BarcodeSetting>(`/barcode-settings/${barcode}`).then(r => r.data),
  update: (barcode: string, data: { default_main_ctime?: string; default_detail_ctime?: string }) =>
    api.put<BarcodeSetting>(`/barcode-settings/${barcode}`, data).then(r => r.data),
};

export interface DuplicateGroup {
  barcode: string;
  image_type: string;
  version_label: string;
  version_folder_ctime: string;
  folder_ctime: string;
  image_count: number;
  total_file_size: number;
}

export interface LowVersionGroup {
  barcode: string;
  image_type: string;
  version_label: string;
  folder_ctime: string;
  image_count: number;
  total_file_size: number;
  is_latest: boolean;
  is_only_version: boolean;
  meets_threshold: boolean;
  threshold: number;
  status_tag: 'will_delete' | 'keep_threshold' | 'keep_only' | 'keep_disabled';
}

interface BatchDeleteResult {
  deleted_image_count: number;
  deleted_item_count: number;
  affected_barcodes: string[];
}

export const batchApi = {
  listDuplicates: () =>
    api.get<{ groups: DuplicateGroup[]; total_duplicate_count: number; total_barcode_count: number }>('/batch/duplicates').then(r => r.data),
  deleteDuplicates: (items: { barcode: string; image_type: string; folder_ctime: string }[], deleteFiles: boolean) =>
    api.post<BatchDeleteResult>('/batch/delete-duplicates', { items, delete_files: deleteFiles }).then(r => r.data),
  listLowVersions: (mainThreshold: number, detailThreshold: number) =>
    api.get<{ groups: LowVersionGroup[]; summary: Record<string, number> }>('/batch/low-versions', {
      params: { main_threshold: mainThreshold, detail_threshold: detailThreshold },
    }).then(r => r.data),
  deleteLowVersions: (items: { barcode: string; image_type: string; folder_ctime: string }[], deleteFiles: boolean, mainThreshold: number, detailThreshold: number) =>
    api.post<BatchDeleteResult>('/batch/delete-low-versions', { items, delete_files: deleteFiles, main_threshold: mainThreshold, detail_threshold: detailThreshold }).then(r => r.data),
};

// ---------- Batch Task Framework ----------

export interface BatchTaskInfo {
  id: number;
  task_type: 'duplicate_scan' | 'low_version_scan';
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled' | 'interrupted';
  progress: number;
  total: number;
  result_count: number;
  error_message: string;
  params_json: string;
  created_at: string;
  started_at: string;
  finished_at: string;
}

export interface DuplicateScanResultItem {
  id: number;
  barcode: string;
  image_type: string;
  version_label: string;
  version_folder_ctime: string;
  folder_ctime: string;
  image_count: number;
  total_file_size: number;
  delete_status: 'pending' | 'deleted' | 'skipped' | 'failed';
  delete_message: string;
  deleted_at: string;
}

export interface LowVersionScanResultItem {
  id: number;
  barcode: string;
  image_type: string;
  version_label: string;
  folder_ctime: string;
  image_count: number;
  total_file_size: number;
  is_latest: boolean;
  is_only_version: boolean;
  meets_threshold: boolean;
  main_threshold: number;
  detail_threshold: number;
  status_tag: 'will_delete' | 'keep_threshold' | 'keep_only' | 'keep_disabled';
  delete_status: 'pending' | 'deleted' | 'skipped' | 'failed';
  delete_message: string;
  deleted_at: string;
}

export interface PaginatedResults<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface TaskBatchDeleteResult {
  deleted_image_count: number;
  skipped_count: number;
  affected_barcodes: string[];
}

export const taskApi = {
  // Common task endpoints
  listTasks: (params?: { type?: string; status?: string }) =>
    api.get<BatchTaskInfo[]>('/tasks', { params }).then(r => r.data),
  getTask: (taskId: number) =>
    api.get<BatchTaskInfo>(`/tasks/${taskId}`).then(r => r.data),
  deleteTask: (taskId: number) =>
    api.delete<{ ok: true } | { error: string }>(`/tasks/${taskId}`).then(r => r.data),

  // Duplicate scan
  createDuplicateScan: () =>
    api.post<BatchTaskInfo>('/batch/duplicate-scan/tasks').then(r => r.data),
  listDuplicateScanTasks: () =>
    api.get<BatchTaskInfo[]>('/batch/duplicate-scan/tasks').then(r => r.data),
  getDuplicateScanTask: (taskId: number) =>
    api.get<BatchTaskInfo>(`/batch/duplicate-scan/tasks/${taskId}`).then(r => r.data),
  getDuplicateScanResults: (taskId: number, page: number, pageSize: number) =>
    api.get<PaginatedResults<DuplicateScanResultItem>>(`/batch/duplicate-scan/tasks/${taskId}/results`, {
      params: { page, page_size: pageSize },
    }).then(r => r.data),
  deleteDuplicateScanTask: (taskId: number) =>
    api.delete(`/batch/duplicate-scan/tasks/${taskId}`).then(r => r.data),
  deleteDuplicateScanResults: (taskId: number, resultIds: number[], deleteFiles: boolean) =>
    api.post<TaskBatchDeleteResult>(`/batch/duplicate-scan/tasks/${taskId}/delete`, {
      mode: 'selected', result_ids: resultIds, delete_files: deleteFiles,
    }).then(r => r.data),

  // Low version scan
  createLowVersionScan: (params: { main_enabled: boolean; main_threshold: number; detail_enabled: boolean; detail_threshold: number }) =>
    api.post<BatchTaskInfo>('/batch/low-version-scan/tasks', params).then(r => r.data),
  listLowVersionScanTasks: () =>
    api.get<BatchTaskInfo[]>('/batch/low-version-scan/tasks').then(r => r.data),
  getLowVersionScanTask: (taskId: number) =>
    api.get<BatchTaskInfo>(`/batch/low-version-scan/tasks/${taskId}`).then(r => r.data),
  getLowVersionScanResults: (taskId: number, page: number, pageSize: number, filters?: { status_tag?: string; delete_status?: string }) =>
    api.get<PaginatedResults<LowVersionScanResultItem>>(`/batch/low-version-scan/tasks/${taskId}/results`, {
      params: { page, page_size: pageSize, ...filters },
    }).then(r => r.data),
  deleteLowVersionScanTask: (taskId: number) =>
    api.delete(`/batch/low-version-scan/tasks/${taskId}`).then(r => r.data),
  deleteLowVersionScanResults: (taskId: number, resultIds: number[], deleteFiles: boolean) =>
    api.post<TaskBatchDeleteResult>(`/batch/low-version-scan/tasks/${taskId}/delete`, {
      mode: 'selected', result_ids: resultIds, delete_files: deleteFiles,
    }).then(r => r.data),
};

