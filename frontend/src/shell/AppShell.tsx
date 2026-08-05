import React from 'react';
import {
  BarcodeOutlined,
  ScanOutlined,
  ExclamationCircleOutlined,
  ToolOutlined,
  FileExcelOutlined,
  SettingOutlined,
  MoonOutlined,
  SunOutlined,
} from '@ant-design/icons';
import { Led } from '../components/ui';
import type { ThemeMode } from '../theme/theme';

export type ViewKey = 'library' | 'scan' | 'pending' | 'batch' | 'export' | 'logs';

const NAV_ITEMS: { key: ViewKey; label: string; icon: React.ReactNode }[] = [
  { key: 'library', label: '图库', icon: <BarcodeOutlined /> },
  { key: 'scan', label: '扫描', icon: <ScanOutlined /> },
  { key: 'pending', label: '待确认', icon: <ExclamationCircleOutlined /> },
  { key: 'batch', label: '批量', icon: <ToolOutlined /> },
  { key: 'export', label: '导出', icon: <FileExcelOutlined /> },
  { key: 'logs', label: '设置', icon: <SettingOutlined /> },
];

const VIEW_META: Record<ViewKey, { eyebrow: string; title: string }> = {
  library: { eyebrow: 'BARCODE LIBRARY', title: '条码图库' },
  scan: { eyebrow: 'SCANNER', title: '扫描目录' },
  pending: { eyebrow: 'REVIEW QUEUE', title: '待确认图片' },
  batch: { eyebrow: 'BATCH OPS', title: '批量清理' },
  export: { eyebrow: 'EXCEL EXPORT', title: 'Excel 批量导出' },
  logs: { eyebrow: 'SYSTEM SETTINGS', title: '系统设置' },
};

/** 图片库系统 logo：图片轮廓 + 山形 */
const LogoMark: React.FC = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden>
    <rect x="2" y="3.5" width="16" height="11" rx="2" stroke="currentColor" strokeWidth="1.4" />
    <path d="M2 11.5 6.5 8l3.5 3 3-2.5 5 4" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    <circle cx="7" cy="6.5" r="1.1" fill="currentColor" />
    <path d="M5 17.5h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" opacity="0.5" />
  </svg>
);

interface AppShellProps {
  active: ViewKey;
  onNavigate: (v: ViewKey) => void;
  pendingCount: number;
  themeMode: ThemeMode;
  onThemeToggle: () => void;
  children: React.ReactNode;
}

const AppShell: React.FC<AppShellProps> = ({
  active,
  onNavigate,
  pendingCount,
  themeMode,
  onThemeToggle,
  children,
}) => {
  const meta = VIEW_META[active];
  return (
    <div className="app-shell">
      <nav className="nav-rail">
        <div className="nav-logo">
          <LogoMark />
        </div>
        <div className="nav-items">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              className={`nav-item${active === item.key ? ' on' : ''}`}
              onClick={() => onNavigate(item.key)}
            >
              {item.icon}
              <span>{item.label}</span>
              {item.key === 'pending' && pendingCount > 0 && (
                <span className="nav-badge">{pendingCount > 99 ? '99+' : pendingCount}</span>
              )}
            </button>
          ))}
        </div>
        <div className="nav-foot">v1.1</div>
      </nav>

      <div className="main-col">
        <header className="topbar">
          <span className="topbar-eyebrow">{meta.eyebrow}</span>
          <span className="topbar-title">{meta.title}</span>
          <div className="topbar-right">
            {pendingCount > 0 && active !== 'pending' && (
              <button
                className="seg-btn"
                style={{ display: 'flex', alignItems: 'center', gap: 7 }}
                onClick={() => onNavigate('pending')}
              >
                <Led color="amber" />
                <span>
                  待确认 <span className="mono">{pendingCount}</span>
                </span>
              </button>
            )}
            <button
              type="button"
              className="theme-toggle"
              onClick={onThemeToggle}
              title={themeMode === 'light' ? '切换为暗色' : '切换为亮色'}
              aria-label={themeMode === 'light' ? '切换为暗色主题' : '切换为亮色主题'}
              aria-pressed={themeMode === 'dark'}
            >
              {themeMode === 'light' ? <MoonOutlined /> : <SunOutlined />}
              <span>{themeMode === 'light' ? '暗色' : '亮色'}</span>
            </button>
          </div>
        </header>
        <main className="view-outlet">{children}</main>
      </div>
    </div>
  );
};

export default AppShell;
