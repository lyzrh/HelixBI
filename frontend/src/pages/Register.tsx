/** 注册页：与登录页共用 AuthScreen（Tab 卡片），默认落在「注册」Tab。 */

import { AuthScreen } from './Login';
import { useAuthStore } from '../stores/authStore';
import { Navigate } from 'react-router-dom';

export function Register() {
  const token = useAuthStore((s) => s.token);
  if (token) return <Navigate to="/" replace />;
  return <AuthScreen initialTab="register" />;
}
