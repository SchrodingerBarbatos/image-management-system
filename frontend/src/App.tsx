import React from 'react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import Home from './pages/Home';

const App: React.FC = () => (
  <ConfigProvider locale={zhCN}>
    <Home />
  </ConfigProvider>
);

export default App;
