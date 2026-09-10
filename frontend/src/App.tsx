import { useEffect } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import { AppLayout } from './layouts/AppLayout';
import { Workbench } from './pages/Workbench';
import { Chat } from './pages/Chat';
import { Datasources } from './pages/Datasources';
import { Agents } from './pages/Agents';
import { Skills } from './pages/Skills';
import { Insights } from './pages/Insights';
import { Dashboards } from './pages/Dashboards';
import { Explore } from './pages/Explore';
import { Usage } from './pages/Usage';
import { theme } from './theme';
import { useAppStore } from './stores/appStore';
import { useLangStore } from './i18n';

dayjs.locale('zh-cn');

export default function App() {
  const location = useLocation();
  const loadHealth = useAppStore((s) => s.loadHealth);
  const lang = useLangStore((s) => s.lang);

  useEffect(() => {
    dayjs.locale(lang === 'en' ? 'en' : 'zh-cn');
  }, [lang]);

  useEffect(() => {
    loadHealth();
    const timer = setInterval(loadHealth, 60_000);
    return () => clearInterval(timer);
  }, [loadHealth]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);

  return (
    <ConfigProvider locale={lang === 'en' ? enUS : zhCN} theme={theme}>
      <AntdApp>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Workbench />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/chat/:sessionId" element={<Chat />} />
            <Route path="/explore" element={<Explore />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/insights" element={<Insights />} />
            <Route path="/dashboards" element={<Dashboards />} />
            <Route path="/datasources" element={<Datasources />} />
            <Route path="/usage" element={<Usage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </AntdApp>
    </ConfigProvider>
  );
}
