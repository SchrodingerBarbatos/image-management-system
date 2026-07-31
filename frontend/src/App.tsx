import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { ConfigProvider, Spin, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { getAppTheme, ThemeMode } from './theme/theme';
import AppShell, { ViewKey } from './shell/AppShell';
import LibraryView from './views/LibraryView';
import { pendingApi } from './services/api';

const ScanView = React.lazy(() => import('./views/ScanView'));
const PendingView = React.lazy(() => import('./views/PendingView'));
const BatchView = React.lazy(() => import('./views/BatchView'));
const ExportView = React.lazy(() => import('./views/ExportView'));
const LogsView = React.lazy(() => import('./views/LogsView'));

const ViewFallback: React.FC = () => (
  <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 80 }}>
    <Spin />
  </div>
);

const PENDING_POLL_MS = 20_000;
const THEME_STORAGE_KEY = 'image-library-theme';

const getInitialTheme = (): ThemeMode => {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
};

const App: React.FC = () => {
  const [active, setActive] = useState<ViewKey>('library');
  const [pendingCount, setPendingCount] = useState(0);
  const [themeMode, setThemeMode] = useState<ThemeMode>(getInitialTheme);
  const [libraryRevision, setLibraryRevision] = useState(0);

  const refreshPending = useCallback(() => {
    pendingApi.count().then(setPendingCount).catch(() => {});
  }, []);

  const refreshExternalData = useCallback(() => {
    refreshPending();
    setLibraryRevision((revision) => revision + 1);
  }, [refreshPending]);

  useEffect(() => {
    refreshPending();
    const t = setInterval(refreshPending, PENDING_POLL_MS);
    return () => clearInterval(t);
  }, [refreshPending]);

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
    document.documentElement.style.colorScheme = themeMode;
    try {
      localStorage.setItem(THEME_STORAGE_KEY, themeMode);
    } catch {
      // 无痕模式或存储被禁用时仍允许本次会话切换主题。
    }
  }, [themeMode]);

  return (
    <ConfigProvider locale={zhCN} theme={getAppTheme(themeMode)}>
      <AntApp>
        <AppShell
          active={active}
          onNavigate={setActive}
          pendingCount={pendingCount}
          themeMode={themeMode}
          onThemeToggle={() => setThemeMode((mode) => (mode === 'light' ? 'dark' : 'light'))}
        >
          {/* 图库常驻保活：切换视图不丢失搜索/分页/选择状态 */}
          <div className="view" style={active === 'library' ? undefined : { display: 'none' }}>
            <LibraryView onDataChanged={refreshPending} refreshRevision={libraryRevision} />
          </div>
          <Suspense fallback={<ViewFallback />}>
            {active === 'scan' && (
              <div className="view">
                <ScanView onScanComplete={refreshExternalData} />
              </div>
            )}
            {active === 'pending' && (
              <div className="view">
                <PendingView onConfirmed={refreshExternalData} />
              </div>
            )}
            {active === 'batch' && (
              <div className="view">
                <BatchView onCompleted={refreshExternalData} />
              </div>
            )}
            {active === 'export' && (
              <div className="view">
                <ExportView />
              </div>
            )}
            {active === 'logs' && (
              <div className="view">
                <LogsView />
              </div>
            )}
          </Suspense>
        </AppShell>
      </AntApp>
    </ConfigProvider>
  );
};

export default App;
