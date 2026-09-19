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
import { Login } from './pages/Login';
import { Register } from './pages/Register';
import { Members } from './pages/Members';
import { theme } from './theme';
import { useAppStore } from './stores/appStore';
import { useAuthStore } from './stores/authStore';
import { useLangStore } from './i18n';

dayjs.locale('zh-cn');

export default function App() {
  const location = useLocation();
  const loadHealth = useAppStore((s) => s.loadHealth);
  const lang = useLangStore((s) => s.lang);
  const token = useAuthStore((s) => s.token);
  const loadMe = useAuthStore((s) => s.loadMe);
  const loadProfile = useAppStore((s) => s.loadProfile);

  useEffect(() => {
    dayjs.locale(lang === 'en' ? 'en' : 'zh-cn');
  }, [lang]);

  useEffect(() => {
    if (token) {
      loadMe();
      loadProfile();
    }
  }, [token, loadMe, loadProfile]);

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
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          {token ? (
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
              <Route path="/members" element={<Members />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          ) : (
            <Route path="*" element={<Navigate to="/login" replace />} />
          )}
        </Routes>
      </AntdApp>
    </ConfigProvider>
  );
}
