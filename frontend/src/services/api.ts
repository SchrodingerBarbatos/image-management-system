import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

export interface ScanRoot {
  id: number; path: string; recursive: boolean; enabled: boolean;
}

export interface ScanLog {
  id: number; action: string; status: string;
  message: string; details: string; created_at: string;
}

export interface ImageRec {
  id: number; barcode: string; image_type: string; sequence: number;
  filename: string; ext: string; file_path: string; file_size: number;
  md5_hash: string; folder_path: string; folder_mtime: string;
  scan_root_id: number; confirmed: boolean; status: string;
  created_at: string; updated_at: string;
}

export interface ImageVersion {
  id: number; barcode: string; version_label: string;
  folder_mtime: string; content_hash: string; is_latest: boolean;
  created_at: string;
}

export interface Paginated<T> {
  items: T[]; total: number; page: number; page_size: number;
}

export interface ImageListParams {
  barcode?: string; image_type?: string; scan_root_id?: number;
  page?: number; page_size?: number; sort?: string; order?: string;
}

export const scanRootApi = {
  list: () => api.get<ScanRoot[]>('/scan-roots').then(r => r.data),
  create: (data: { path: string; recursive?: boolean }) =>
    api.post<ScanRoot>('/scan-roots', data).then(r => r.data),
  update: (id: number, data: { recursive?: boolean; enabled?: boolean }) =>
    api.put<ScanRoot>(`/scan-roots/${id}`, data).then(r => r.data),
  delete: (id: number) => api.delete(`/scan-roots/${id}`).then(r => r.data),
};

export const scanApi = {
  trigger: (data?: { root_id?: number; allow_fuzzy?: boolean }) =>
    api.post('/scan', data || {}).then(r => r.data),
};

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
  delete: (id: number) => api.delete(`/images/${id}`),
  thumbnailUrl: (id: number) => `/api/thumbnails/${id}`,
  fileUrl: (id: number) => `/api/images/${id}/file`,
  batchDelete: (ids: number[]) =>
    api.post('/images/batch-delete', { ids }).then(r => r.data),
  batchExport: (ids: number[], image_type?: string) =>
    api.post<{ task_id: number }>('/images/batch-export', { ids, image_type }).then(r => r.data),
};

export const pendingApi = {
  list: () => api.get<ImageRec[]>('/pending').then(r => r.data),
  confirm: (items: { id: number; image_type: string }[]) =>
    api.post('/pending/confirm', items).then(r => r.data),
  ignore: (id: number) => api.delete(`/pending/${id}`),
};

export const exportApi = {
  uploadExcel: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return api.post<{ columns: string[]; upload_id: string }>('/export/excel', fd).then(r => r.data);
  },
  generateZip: (data: { barcode_column: string; image_type: string; upload_id: string; selected_barcodes?: string[] }) =>
    api.post<{ task_id: number }>('/export/zip', data).then(r => r.data),
  downloadUrl: (taskId: number) => `/api/export/download/${taskId}`,
};
