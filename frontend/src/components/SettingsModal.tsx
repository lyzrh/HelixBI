/** 设置中心（Tabs）：LLM 接口配置 + 偏好设置 + 个人资料 */

import { useEffect, useState } from 'react';
import {
  App as AntApp, AutoComplete, Avatar, Button, Input, Modal, Radio, Space, Switch, Tabs, Tag,
} from 'antd';
import {
  ApiOutlined, CheckCircleFilled, CloseCircleFilled, SlidersOutlined, ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { api } from '../api/client';
import { useAppStore } from '../stores/appStore';
import { useT, useLangStore } from '../i18n';

/** 服务商预设（OpenAI 兼容接口） */
const PROVIDERS = [
  { key: 'deepseek', label: 'DeepSeek', base_url: 'https://api.deepseek.com', model: 'deepseek-chat' },
  { key: 'zhipu', label: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  { key: 'qwen', label: '阿里通义', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { key: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { key: 'custom', label: '自定义', base_url: '', model: '' },
];

const AVATAR_COLORS = ['#5645D4', '#7B5CF5', '#2A9D99', '#ea580c', '#dc2626', '#0891b2', '#4f46e5', '#be185d'];

/** 职位预设：可从下拉选择，也可自由输入 */
const ROLE_OPTIONS = [
  '数据分析师', '运营经理', '产品经理', '财务分析师', '生产主管', '供应链经理',
  '销售总监', '市场专员', '电商运营', 'IT 工程师', '总经理 / 高管', '门店店长',
].map((v) => ({ value: v }));

interface LlmSettings {
  base_url: string;
  model: string;
  api_key_masked: string;
  configured: boolean;
}

interface UserProfile {
  nickname: string;
  role: string;
  avatar_color: string;
}

interface UserPreferences {
  answer_style: 'concise' | 'standard' | 'detailed';
  temperature: number;
  followups_enabled: boolean;
  custom_instructions: string;
}

export function SettingsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = AntApp.useApp();
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const setLang = useLangStore((s) => s.setLang);
  const loadHealth = useAppStore((s) => s.loadHealth);

  // ---- LLM ----
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState(''); // 留空 = 不修改
  const [model, setModel] = useState('');
  const [masked, setMasked] = useState('');
  const [activeProvider, setActiveProvider] = useState<string>('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [saving, setSaving] = useState(false);

  // ---- 个人资料 ----
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [profileSaving, setProfileSaving] = useState(false);

  // ---- 偏好设置 ----
  const [prefs, setPrefs] = useState<UserPreferences | null>(null);
  const [prefsSaving, setPrefsSaving] = useState(false);

  const loadLlm = async () => {
    try {
      const s = await api.get<LlmSettings>('/api/settings/llm');
      setBaseUrl(s.base_url);
      setModel(s.model);
      setMasked(s.api_key_masked);
      const hit = PROVIDERS.find((p) => p.base_url === s.base_url);
      setActiveProvider(hit ? hit.key : 'custom');
    } catch { /* client 已提示 */ }
  };

  const loadProfile = async () => {
    try {
      setProfile(await api.get<UserProfile>('/api/settings/profile'));
    } catch { /* client 已提示 */ }
  };

  const loadPrefs = async () => {
    try {
      setPrefs(await api.get<UserPreferences>('/api/settings/preferences'));
    } catch { /* client 已提示 */ }
  };

  useEffect(() => {
    if (open) {
      setApiKey('');
      setTestResult(null);
      loadLlm();
      loadProfile();
      loadPrefs();
    }
  }, [open]);

  const pickProvider = (key: string) => {
    const p = PROVIDERS.find((x) => x.key === key);
    if (!p) return;
    setActiveProvider(key);
    if (key !== 'custom') {
      setBaseUrl(p.base_url);
      setModel(p.model);
    }
    setTestResult(null);
  };

  const doTest = async () => {
    if (!apiKey && !masked) { message.warning(t('set.needKey')); return; }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.post<{ ok: boolean; message: string }>('/api/settings/llm/test', {
        base_url: baseUrl || null,
        api_key: apiKey || null,
        model: model || null,
      });
      setTestResult(r);
    } catch { /* client 已提示 */ } finally {
      setTesting(false);
    }
  };

  const doSave = async () => {
    if (!baseUrl.trim()) { message.warning(t('set.needUrl')); return; }
    if (!model.trim()) { message.warning(t('set.needModel')); return; }
    if (!apiKey && !masked) { message.warning(t('set.needKey')); return; }
    setSaving(true);
    try {
      const r = await api.put<{ message: string }>('/api/settings/llm', {
        base_url: baseUrl.trim(),
        api_key: apiKey || null,
        model: model.trim(),
      });
      message.success(r.message);
      setApiKey('');
      await loadLlm();
      loadHealth();
    } catch { /* client 已提示 */ } finally {
      setSaving(false);
    }
  };

  const saveProfile = async () => {
    if (!profile) return;
    if (!profile.nickname.trim()) { message.warning(t('set.needNickname')); return; }
    setProfileSaving(true);
    try {
      const saved = await api.put<UserProfile>('/api/settings/profile', {
        nickname: profile.nickname.trim(),
        role: profile.role.trim(),
        avatar_color: profile.avatar_color,
      });
      setProfile(saved);
      message.success(t('set.profileSaved'));
    } catch { /* client 已提示 */ } finally {
      setProfileSaving(false);
    }
  };

  const savePrefs = async () => {
    if (!prefs) return;
    setPrefsSaving(true);
    try {
      const saved = await api.put<UserPreferences>('/api/settings/preferences', {
        answer_style: prefs.answer_style,
        temperature: prefs.temperature,
        followups_enabled: prefs.followups_enabled,
        custom_instructions: prefs.custom_instructions,
      });
      setPrefs(saved);
      message.success(t('set.prefsSaved'));
    } catch { /* client 已提示 */ } finally {
      setPrefsSaving(false);
    }
  };

  const items = [
    {
      key: 'llm',
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <ApiOutlined /> {t('set.tabLlm')}
        </span>
      ),
      children: (
        <div style={{ paddingTop: 4 }}>
          <div style={labelStyle}>{t('set.provider')}</div>
          <Space size={8} wrap style={{ marginBottom: 14 }}>
            {PROVIDERS.map((p) => (
              <Tag.CheckableTag
                key={p.key}
                checked={activeProvider === p.key}
                onChange={() => pickProvider(p.key)}
                style={{ padding: '3px 12px', fontSize: 13, borderRadius: 6 }}
              >
                {p.key === 'custom' ? t('set.providerCustom') : p.label}
              </Tag.CheckableTag>
            ))}
          </Space>

          <div style={labelStyle}>{t('set.baseUrl')}</div>
          <Input
            value={baseUrl} placeholder="https://api.deepseek.com"
            onChange={(e) => { setBaseUrl(e.target.value); setActiveProvider('custom'); setTestResult(null); }}
            style={{ marginBottom: 12 }}
          />

          <div style={labelStyle}>
            {t('set.apiKey')}
            {masked && (
              <span style={{ fontWeight: 400, color: '#94a3b8', marginLeft: 8, fontSize: 12 }}>
                {t('set.apiKeyKeep', { m: masked })}
              </span>
            )}
          </div>
          <Input.Password
            value={apiKey} placeholder={masked ? t('set.apiKeyKeepShort', { m: masked }) : 'sk-...'}
            onChange={(e) => { setApiKey(e.target.value); setTestResult(null); }}
            style={{ marginBottom: 12 }}
          />

          <div style={labelStyle}>{t('set.modelName')}</div>
          <Input
            value={model} placeholder="deepseek-chat"
            onChange={(e) => { setModel(e.target.value); setActiveProvider('custom'); setTestResult(null); }}
            style={{ marginBottom: 16 }}
          />

          {testResult && (
            <div style={{
              display: 'flex', alignItems: 'flex-start', gap: 8, padding: '8px 12px', borderRadius: 8,
              background: testResult.ok ? '#f0fdf4' : '#fef2f2',
              border: `1px solid ${testResult.ok ? '#bbf7d0' : '#fecaca'}`,
              fontSize: 13, marginBottom: 14,
            }}>
              {testResult.ok
                ? <CheckCircleFilled style={{ color: '#16a34a', marginTop: 2 }} />
                : <CloseCircleFilled style={{ color: '#dc2626', marginTop: 2 }} />}
              <span style={{ color: testResult.ok ? '#166534' : '#991b1b' }}>{testResult.message}</span>
            </div>
          )}

          <div style={{ display: 'flex', gap: 10 }}>
            <Button icon={<ThunderboltOutlined />} loading={testing} onClick={doTest}>{t('set.test')}</Button>
            <div style={{ flex: 1 }} />
            <Button type="primary" icon={<ApiOutlined />} loading={saving} onClick={doSave}>{t('set.save')}</Button>
          </div>

          <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 14, lineHeight: 1.7 }}>
            {t('set.llmNote')}
          </div>
        </div>
      ),
    },
    {
      key: 'prefs',
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <SlidersOutlined /> {t('set.tabPrefs')}
        </span>
      ),
      children: (
        <div style={{ paddingTop: 4 }}>
          <div style={labelStyle}>{t('set.lang')}</div>
          <Radio.Group
            value={lang}
            onChange={(e) => setLang(e.target.value)}
            style={{ marginBottom: 6 }}
          >
            <Radio.Button value="zh">{t('set.langZh')}</Radio.Button>
            <Radio.Button value="en">{t('set.langEn')}</Radio.Button>
          </Radio.Group>
          <div style={{ fontSize: 12, color: '#94a3b8', marginTop: -6, marginBottom: 16, lineHeight: 1.6 }}>
            {t('set.langHint')}
          </div>

          <div style={labelStyle}>{t('set.answerStyle')}</div>
          <Radio.Group
            value={prefs?.answer_style ?? 'standard'}
            onChange={(e) => setPrefs((p) => (p ? { ...p, answer_style: e.target.value } : p))}
            style={{ marginBottom: 14 }}
          >
            <Radio.Button value="concise">{t('set.styleConcise')}</Radio.Button>
            <Radio.Button value="standard">{t('set.styleStandard')}</Radio.Button>
            <Radio.Button value="detailed">{t('set.styleDetailed')}</Radio.Button>
          </Radio.Group>
          <div style={{ fontSize: 12, color: '#94a3b8', marginTop: -8, marginBottom: 16, lineHeight: 1.6 }}>
            {t('set.styleHint')}
          </div>

          <div style={labelStyle}>{t('set.creativity')}</div>
          <Radio.Group
            value={prefs?.temperature ?? 0}
            onChange={(e) => setPrefs((p) => (p ? { ...p, temperature: e.target.value } : p))}
            style={{ marginBottom: 6 }}
          >
            <Radio.Button value={0}>{t('set.cPrecise')}</Radio.Button>
            <Radio.Button value={0.5}>{t('set.cBalanced')}</Radio.Button>
            <Radio.Button value={0.9}>{t('set.cCreative')}</Radio.Button>
          </Radio.Group>
          <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 16, lineHeight: 1.6 }}>
            {t('set.creativityHint')}
          </div>

          <div style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 8 }}>
            {t('set.followups')}
            <Switch
              size="small"
              checked={prefs?.followups_enabled ?? true}
              onChange={(v) => setPrefs((p) => (p ? { ...p, followups_enabled: v } : p))}
            />
            <span style={{ fontWeight: 400, color: '#94a3b8', fontSize: 12 }}>
              {prefs?.followups_enabled ? t('set.followupsOn') : t('set.followupsOff')}
            </span>
          </div>
          <div style={{ height: 12 }} />

          <div style={labelStyle}>{t('set.customInstr')}</div>
          <Input.TextArea
            rows={4} maxLength={500} showCount
            value={prefs?.custom_instructions ?? ''}
            placeholder={'会附加到每次分析的指令中，例如：\n· 我是零售行业从业者，回答时优先关注库存周转与毛利\n· 结论中尽量使用表格对比呈现'}
            onChange={(e) => setPrefs((p) => (p ? { ...p, custom_instructions: e.target.value } : p))}
            style={{ marginBottom: 14 }}
          />

          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button type="primary" loading={prefsSaving} onClick={savePrefs}>{t('set.savePrefs')}</Button>
          </div>
        </div>
      ),
    },
    {
      key: 'profile',
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <UserOutlined /> {t('set.tabProfile')}
        </span>
      ),
      children: (
        <div style={{ paddingTop: 4 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 18 }}>
            <Avatar size={56} style={{
              background: profile?.avatar_color ?? '#5645D4', fontSize: 22, fontWeight: 600,
            }}>
              {(profile?.nickname || 'U').slice(0, 1)}
            </Avatar>
            <div>
              <div style={{ fontWeight: 600, fontSize: 15 }}>{profile?.nickname || t('set.unnamed')}</div>
              <div style={{ fontSize: 12.5, color: '#94a3b8' }}>{profile?.role || '—'}</div>
            </div>
          </div>

          <div style={labelStyle}>{t('set.nickname')}</div>
          <Input
            value={profile?.nickname ?? ''} maxLength={20} placeholder={t('set.nicknamePh')}
            onChange={(e) => setProfile((p) => (p ? { ...p, nickname: e.target.value } : p))}
            style={{ marginBottom: 12 }}
          />

          <div style={labelStyle}>{t('set.role')}</div>
          <AutoComplete
            value={profile?.role ?? ''}
            options={ROLE_OPTIONS}
            maxLength={20}
            placeholder={t('set.rolePh')}
            filterOption={(input, option) =>
              (option?.value ?? '').toLowerCase().includes(input.toLowerCase())}
            onChange={(v) => setProfile((p) => (p ? { ...p, role: v } : p))}
            style={{ width: '100%', marginBottom: 14 }}
          />

          <div style={labelStyle}>{t('set.avatarColor')}</div>
          <Space size={10} wrap style={{ marginBottom: 18 }}>
            {AVATAR_COLORS.map((c) => (
              <div
                key={c}
                onClick={() => setProfile((p) => (p ? { ...p, avatar_color: c } : p))}
                style={{
                  width: 30, height: 30, borderRadius: '50%', background: c, cursor: 'pointer',
                  border: profile?.avatar_color === c ? '3px solid #0f172a' : '3px solid transparent',
                  transform: profile?.avatar_color === c ? 'scale(1.08)' : 'none',
                  transition: 'all .15s',
                }}
              />
            ))}
          </Space>

          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button type="primary" loading={profileSaving} onClick={saveProfile}>{t('set.saveProfile')}</Button>
          </div>
        </div>
      ),
    },
  ];

  return (
    <Modal open={open} title={t('set.title')} width={560} onCancel={onClose} footer={null}>
      <Tabs items={items} defaultActiveKey="llm" style={{ marginTop: 4 }} />
    </Modal>
  );
}

const labelStyle: React.CSSProperties = { fontSize: 13, color: '#475569', marginBottom: 6 };
