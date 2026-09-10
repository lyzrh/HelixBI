/** 数据源页（FineBI 数据连接市场风格）：我的数据源卡片 + 连接市场 + 上传/DB/预览/物化 */

import { useEffect, useState } from 'react';
import {
  App as AntApp, Button, Card, Checkbox, Col, Dropdown, Empty, Input, InputNumber, Modal,
  Row, Select, Space, Spin, Tabs, Tag, Upload,
} from 'antd';
import {
  ApiOutlined, CaretRightOutlined, CloudServerOutlined, CloudUploadOutlined, DatabaseOutlined,
  DeleteOutlined, EyeOutlined, FileTextOutlined, MoreOutlined, SearchOutlined, TagOutlined,
} from '@ant-design/icons';
import { api } from '../api/client';
import type { DataSourceInfo } from '../api/types';
import { ResultTable } from '../components/chat/ResultBlocks';
import { useAppStore } from '../stores/appStore';

const DB_TYPES = [
  { value: 'mysql', label: 'MySQL', defaultPort: 3306 },
  { value: 'postgresql', label: 'PostgreSQL', defaultPort: 5432 },
  { value: 'sqlite', label: 'SQLite（本地文件）', defaultPort: undefined },
];

interface DbForm {
  db_type: string; host: string; port: number | null;
  database: string; username: string; password: string; sqlite_path: string;
}

const emptyDbForm: DbForm = {
  db_type: 'mysql', host: '', port: 3306,
  database: '', username: '', password: '', sqlite_path: '',
};

export function Datasources() {
  const { message } = AntApp.useApp();

  const [list, setList] = useState<DataSourceInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const packs = useAppStore((s) => s.packs);
  const loadDataSources = useAppStore((s) => s.loadDataSources);
  const loadPacks = useAppStore((s) => s.loadPacks);

  // 上传
  const [uploading, setUploading] = useState(false);

  // DB 连接弹窗
  const [dbOpen, setDbOpen] = useState(false);
  const [dbForm, setDbForm] = useState<DbForm>(emptyDbForm);
  const [dbName, setDbName] = useState('');
  const [dbPack, setDbPack] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [savingDb, setSavingDb] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  // 预览弹窗
  const [previewDs, setPreviewDs] = useState<DataSourceInfo | null>(null);
  const [previewTable, setPreviewTable] = useState<string | null>(null);
  const [previewTables, setPreviewTables] = useState<{ name: string; row_estimate: number | null }[]>([]);
  const [previewData, setPreviewData] = useState<{ columns: string[]; rows: Record<string, unknown>[] } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // SQL 查询（预览弹窗第二 Tab）
  const [previewTab, setPreviewTab] = useState<'data' | 'sql'>('data');
  const [sqlText, setSqlText] = useState('');
  const [sqlResult, setSqlResult] = useState<{ columns: string[]; rows: Record<string, unknown>[]; total: number; truncated: boolean } | null>(null);
  const [sqlRunning, setSqlRunning] = useState(false);

  // 物化弹窗
  const [matDs, setMatDs] = useState<DataSourceInfo | null>(null);
  const [matTable, setMatTable] = useState<string | null>(null);
  const [matTables, setMatTables] = useState<{ name: string; row_estimate: number | null }[]>([]);
  const [matForce, setMatForce] = useState(false);
  const [matting, setMatting] = useState(false);

  const reload = async () => {
    setLoading(true);
    try {
      const items = await api.get<DataSourceInfo[]>('/api/datasources');
      setList(items);
    } catch { /* client.ts 已提示 */ } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
    loadPacks();
  }, [loadPacks]);

  // ---- 上传 ----

  const doUpload = async (opt: { file: unknown }) => {
    const { file } = opt;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file as File);
      const created = await api.postForm<DataSourceInfo>('/api/datasources/upload', fd);
      message.success(`已导入「${created.name}」（${created.row_count ?? '?'} 行，识别语义包：${packs.find((p) => p.id === created.pack_id)?.name ?? created.pack_id ?? '未识别'}）`);
      await reload();
      loadDataSources();
    } catch { /* client.ts 已提示 */ } finally {
      setUploading(false);
    }
  };

  // ---- DB 连接 ----

  const openDbModal = (dbType?: string) => {
    setDbForm({ ...emptyDbForm, ...(dbType ? { db_type: dbType, port: DB_TYPES.find((t) => t.value === dbType)?.defaultPort ?? null } : {}) });
    setDbName('');
    setDbPack(packs[0]?.id ?? null);
    setTestResult(null);
    setDbOpen(true);
  };

  const dbConfigBody = () => ({
    db_type: dbForm.db_type,
    host: dbForm.host,
    port: dbForm.port,
    database: dbForm.database,
    username: dbForm.username,
    password: dbForm.password,
    sqlite_path: dbForm.sqlite_path,
  });

  const testDb = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.post<{ ok: boolean; message: string }>('/api/datasources/test', { config: dbConfigBody() });
      setTestResult(r);
    } catch { /* client.ts 已提示 */ } finally {
      setTesting(false);
    }
  };

  const saveDb = async () => {
    if (!dbName.trim()) { message.warning('请填写数据源名称'); return; }
    setSavingDb(true);
    try {
      const created = await api.post<DataSourceInfo>('/api/datasources/db', {
        name: dbName.trim(), config: dbConfigBody(), pack_id: dbPack,
      });
      message.success(`已连接「${created.name}」，可点击「物化」将表缓存为分析数据`);
      setDbOpen(false);
      await reload();
      loadDataSources();
    } catch { /* client.ts 已提示 */ } finally {
      setSavingDb(false);
    }
  };

  // ---- 预览 ----

  const openPreview = async (ds: DataSourceInfo) => {
    setPreviewDs(ds);
    setPreviewData(null);
    setPreviewTable(null);
    setPreviewTables([]);
    setPreviewTab('data');
    setSqlText('');
    setSqlResult(null);
    setPreviewLoading(true);
    try {
      if (ds.type === 'db') {
        const tables = await api.get<{ name: string; row_estimate: number | null }[]>(`/api/datasources/${ds.id}/tables`);
        setPreviewTables(tables);
        if (tables.length === 1) {
          await loadPreview(ds.id, tables[0].name);
        }
      } else {
        await loadPreview(ds.id, null);
      }
    } catch { /* client.ts 已提示 */ } finally {
      setPreviewLoading(false);
    }
  };

  /** SQL 查询（本地 sqlite 执行，零 token） */
  const runSql = async () => {
    if (!previewDs || !sqlText.trim()) return;
    setSqlRunning(true);
    try {
      setSqlResult(await api.post<{ columns: string[]; rows: Record<string, unknown>[]; total: number; truncated: boolean }>(
        '/api/explore/sql', { data_source_id: previewDs.id, sql: sqlText.trim() },
      ));
    } catch { /* client.ts 已提示 */ } finally {
      setSqlRunning(false);
    }
  };

  const loadPreview = async (dsId: number, table: string | null) => {
    setPreviewLoading(true);
    setPreviewTable(table);
    try {
      const params = table ? `?table=${encodeURIComponent(table)}` : '';
      setPreviewData(await api.get(`/api/datasources/${dsId}/preview${params}`));
    } catch { /* client.ts 已提示 */ } finally {
      setPreviewLoading(false);
    }
  };

  // ---- 物化 ----

  const openMaterialize = async (ds: DataSourceInfo) => {
    setMatDs(ds);
    setMatTable(ds.materialized_table || null);
    setMatForce(false);
    setMatting(false);
    try {
      setMatTables(await api.get(`/api/datasources/${ds.id}/tables`));
    } catch { /* client.ts 已提示 */ }
  };

  const doMaterialize = async () => {
    if (!matDs) return;
    if (!matTable) { message.warning('请选择要物化的表'); return; }
    setMatting(true);
    try {
      const r = await api.post<{ path: string; rows: number; truncated: boolean; cached: boolean }>(
        `/api/datasources/${matDs.id}/materialize`, { table: matTable, force: matForce });
      const tip = r.cached ? '命中缓存，未重新物化' : `已物化 ${r.rows} 行${r.truncated ? '（超上限已截断，保留最近数据）' : ''}`;
      message.success(tip);
      setMatDs(null);
      await reload();
      loadDataSources();
    } catch { /* client.ts 已提示 */ } finally {
      setMatting(false);
    }
  };

  // ---- 语义包切换 / 删除 ----

  const changePack = async (ds: DataSourceInfo, packId: string | null) => {
    try {
      await api.patch(`/api/datasources/${ds.id}`, { pack_id: packId });
      message.success(`「${ds.name}」已切换为「${packs.find((p) => p.id === packId)?.name ?? '无'}」`);
      setList((l) => l.map((x) => (x.id === ds.id ? { ...x, pack_id: packId } : x)));
      loadDataSources();
    } catch { /* client.ts 已提示 */ }
  };

  const remove = async (ds: DataSourceInfo) => {
    try {
      await api.del(`/api/datasources/${ds.id}`);
      message.success('已删除');
      reload();
      loadDataSources();
    } catch { /* client.ts 已提示 */ }
  };

  // ---- 卡片渲染 ----

  const fmtSize = (b: number | null) => {
    if (!b) return '';
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1024 / 1024).toFixed(1)} MB`;
  };

  const filtered = list.filter((d) =>
    !search || d.name.toLowerCase().includes(search.toLowerCase())
    || (d.pack_id ?? '').includes(search.toLowerCase()));

  // 品牌化图标（FineBI 连接市场风格：每个类型独立配色）
  const dsIconMeta = (ds: DataSourceInfo): { icon: React.ReactNode; color: string; bg: string } => {
    if (ds.type === 'file') {
      return { icon: <FileTextOutlined />, color: '#5645D4', bg: 'linear-gradient(135deg,#ECE7F8,#DCD2F5)' };
    }
    if (ds.db_type === 'mysql') {
      return { icon: <CloudServerOutlined />, color: '#0284c7', bg: 'linear-gradient(135deg,#f0f9ff,#e0f2fe)' };
    }
    if (ds.db_type === 'postgresql') {
      return { icon: <DatabaseOutlined />, color: '#2A9D99', bg: 'linear-gradient(135deg,#f0fdfa,#ccfbf1)' };
    }
    return { icon: <DatabaseOutlined />, color: '#7B5CF5', bg: 'linear-gradient(135deg,#f5f3ff,#ede9fe)' };
  };

  // 状态行（小圆点 + 文案，FineBI 风格）
  const statusDot = (ds: DataSourceInfo) => {
    const ok = ds.type === 'file' || !!ds.materialized_path;
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
        <span style={{
          width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
          background: ok ? '#22c55e' : '#f59e0b',
          boxShadow: `0 0 0 3px ${ok ? 'rgba(34,197,94,0.15)' : 'rgba(245,158,11,0.15)'}`,
        }} />
        <span style={{ color: ok ? '#16a34a' : '#d97706' }}>
          {ds.type === 'file' ? '可直接分析'
            : ds.materialized_path ? (ds.materialized_truncated ? '已物化（截断）' : '已物化')
              : '未物化，暂不可分析'}
        </span>
      </span>
    );
  };

  // 元信息一行小灰字
  const metaLine = (ds: DataSourceInfo) => {
    if (ds.type === 'db') {
      return `${ds.db_type}://${ds.host || 'local'}/${ds.database_name}`;
    }
    const parts: string[] = [];
    if (ds.row_count != null) parts.push(`${ds.row_count.toLocaleString()} 行`);
    if (ds.columns?.length) parts.push(`${ds.columns.length} 列`);
    const size = fmtSize(ds.size_bytes);
    if (size) parts.push(size);
    return parts.join(' · ');
  };

  const marketCards = [
    {
      key: 'csv', icon: <FileTextOutlined style={{ fontSize: 28, color: '#5645D4' }} />,
      color: '#5645D4', bg: 'linear-gradient(135deg,#ECE7F8,#DCD2F5)',
      title: '上传数据文件', desc: 'CSV / Excel / Parquet，自动识别字段与语义包',
      action: (
        <Upload accept=".csv,.xlsx,.xls,.parquet" showUploadList={false} customRequest={doUpload}>
          <Button type="primary" block loading={uploading} icon={<CloudUploadOutlined />}>上传文件</Button>
        </Upload>
      ),
    },
    {
      key: 'mysql', icon: <CloudServerOutlined style={{ fontSize: 28, color: '#0284c7' }} />,
      color: '#0284c7', bg: 'linear-gradient(135deg,#f0f9ff,#e0f2fe)',
      title: 'MySQL', desc: '连接业务数据库（ERP / 订单库等），物化后分析',
      action: <Button block icon={<ApiOutlined />} onClick={() => openDbModal('mysql')}>去连接</Button>,
    },
    {
      key: 'postgresql', icon: <DatabaseOutlined style={{ fontSize: 28, color: '#2A9D99' }} />,
      color: '#2A9D99', bg: 'linear-gradient(135deg,#f0fdfa,#ccfbf1)',
      title: 'PostgreSQL', desc: '连接 PostgreSQL 数据库，物化后进入沙箱分析',
      action: <Button block icon={<ApiOutlined />} onClick={() => openDbModal('postgresql')}>去连接</Button>,
    },
    {
      key: 'sqlite', icon: <DatabaseOutlined style={{ fontSize: 28, color: '#7B5CF5' }} />,
      color: '#7B5CF5', bg: 'linear-gradient(135deg,#f5f3ff,#ede9fe)',
      title: 'SQLite', desc: '本地 SQLite 文件，零配置直连',
      action: <Button block icon={<ApiOutlined />} onClick={() => openDbModal('sqlite')}>去连接</Button>,
    },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 1320, margin: '0 auto' }}>
      {/* 顶栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>数据连接</div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
            管理分析用数据：上传文件或连接数据库（数据库需先物化为缓存）
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <Input allowClear prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
          placeholder="搜索名称 / 语义包" style={{ width: 240 }} value={search}
          onChange={(e) => setSearch(e.target.value)} />
      </div>

      {/* 我的数据源 */}
      <SectionTitle title={`我的数据源（${filtered.length}）`} />
      <Spin spinning={loading}>
        <Row gutter={[14, 14]}>
          {filtered.map((ds) => {
            const im = dsIconMeta(ds);
            return (
              <Col key={ds.id} xs={24} sm={12} lg={8} xl={6}>
                <Card
                  size="small" hoverable
                  style={{ borderRadius: 12, height: '100%', position: 'relative', overflow: 'hidden' }}
                  styles={{ body: { padding: 16, display: 'flex', flexDirection: 'column', height: '100%' } }}
                >
                  {/* 顶部品牌色条 */}
                  <div style={{
                    position: 'absolute', top: 0, left: 0, right: 0, height: 3,
                    background: `linear-gradient(90deg, ${im.color}, ${im.color}33)`,
                  }} />

                  {/* 图标 + 名称 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <div style={{
                      width: 46, height: 46, borderRadius: 12, background: im.bg, color: im.color,
                      display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22, flexShrink: 0,
                    }}>{im.icon}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontWeight: 600, fontSize: 14.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {ds.name}
                        </span>
                        {ds.builtin && (
                          <Tag color="blue" bordered={false} style={{ fontSize: 10, lineHeight: '16px', padding: '0 5px', flexShrink: 0 }}>
                            内置
                          </Tag>
                        )}
                      </div>
                      <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {metaLine(ds)}
                      </div>
                    </div>
                  </div>

                  {/* 状态 + 语义包 */}
                  <div style={{ margin: '12px 0 10px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {statusDot(ds)}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <TagOutlined style={{ fontSize: 12, color: '#94a3b8' }} />
                      <Select size="small" variant="borderless" style={{ flex: 1, minWidth: 0 }} allowClear
                        placeholder="设置语义包" value={ds.pack_id ?? undefined}
                        onChange={(v) => changePack(ds, v ?? null)}
                        options={packs.map((p) => ({ value: p.id, label: p.name }))} />
                    </div>
                  </div>

                  {/* 底部操作：预览主操作 + 更多菜单 */}
                  <div style={{ marginTop: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Button size="small" type="primary" ghost icon={<EyeOutlined />}
                      onClick={() => openPreview(ds)}>预览数据</Button>
                    {ds.type === 'db' && (
                      <Button size="small" icon={<CloudUploadOutlined />} onClick={() => openMaterialize(ds)}>物化</Button>
                    )}
                    <div style={{ flex: 1 }} />
                    <Dropdown
                      menu={{
                        items: [
                          ...(ds.type === 'db' ? [{ key: 'mat', icon: <CloudUploadOutlined />, label: '物化缓存' }] : []),
                          { key: 'preview', icon: <EyeOutlined />, label: '数据预览' },
                          ...(ds.builtin ? [] : [{ type: 'divider' as const }, {
                            key: 'del', danger: true, icon: <DeleteOutlined />,
                            label: '删除数据源',
                            onClick: () => {
                              Modal.confirm({
                                title: `删除数据源「${ds.name}」？`,
                                content: '不会删除原文件 / 数据库，仅移除本系统内的连接记录。',
                                okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
                                onOk: () => remove(ds),
                              });
                            },
                          }]),
                        ],
                      }}
                      trigger={['click']}
                    >
                      <Button size="small" type="text" icon={<MoreOutlined />} style={{ color: '#94a3b8' }} />
                    </Dropdown>
                  </div>
                </Card>
              </Col>
            );
          })}
          {!filtered.length && !loading && (
            <Col span={24}>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={search ? '没有匹配的数据源' : '还没有数据源，从下方连接市场开始'} />
            </Col>
          )}
        </Row>
      </Spin>

      {/* 连接市场 */}
      <SectionTitle title="连接市场" style={{ marginTop: 26 }} />
      <Row gutter={[14, 14]}>
        {marketCards.map((c) => (
          <Col key={c.key} xs={24} sm={12} lg={6}>
            <Card
              size="small" hoverable
              style={{ borderRadius: 12, height: '100%', textAlign: 'center' }}
              styles={{ body: { padding: '20px 16px 16px', display: 'flex', flexDirection: 'column', gap: 6, height: '100%' } }}
            >
              <div style={{
                width: 56, height: 56, borderRadius: 14, background: c.bg, margin: '0 auto',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>{c.icon}</div>
              <div style={{ fontWeight: 600, fontSize: 14.5, marginTop: 4 }}>{c.title}</div>
              <div style={{ fontSize: 12.5, color: '#64748b', flex: 1, lineHeight: 1.6, minHeight: 40 }}>{c.desc}</div>
              <div style={{ marginTop: 4 }}>{c.action}</div>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 数据库连接弹窗 */}
      <Modal
        open={dbOpen} title="连接数据库" width={520}
        onOk={saveDb} onCancel={() => setDbOpen(false)}
        okText="保存数据源" cancelText="取消" confirmLoading={savingDb}
      >
        <Space direction="vertical" size={12} style={{ width: '100%', marginTop: 8 }}>
          <div>
            <div style={labelStyle}>数据源名称</div>
            <Input value={dbName} maxLength={40} placeholder="如：ERP 生产库"
              onChange={(e) => setDbName(e.target.value)} />
          </div>
          <div>
            <div style={labelStyle}>数据库类型</div>
            <Select style={{ width: '100%' }} value={dbForm.db_type}
              onChange={(v) => {
                const t = DB_TYPES.find((x) => x.value === v);
                setDbForm((f) => ({ ...f, db_type: v, port: t?.defaultPort ?? null }));
                setTestResult(null);
              }}
              options={DB_TYPES.map((t) => ({ value: t.value, label: t.label }))} />
          </div>

          {dbForm.db_type === 'sqlite' ? (
            <div>
              <div style={labelStyle}>SQLite 文件路径（宿主机绝对路径）</div>
              <Input value={dbForm.sqlite_path} placeholder="D:\data\mydb.sqlite"
                onChange={(e) => setDbForm((f) => ({ ...f, sqlite_path: e.target.value }))} />
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 2 }}>
                  <div style={labelStyle}>主机</div>
                  <Input value={dbForm.host} placeholder="192.168.1.100"
                    onChange={(e) => setDbForm((f) => ({ ...f, host: e.target.value }))} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={labelStyle}>端口</div>
                  <InputNumber style={{ width: '100%' }} value={dbForm.port}
                    onChange={(v) => setDbForm((f) => ({ ...f, port: v }))} />
                </div>
              </div>
              <div>
                <div style={labelStyle}>数据库名</div>
                <Input value={dbForm.database} placeholder="production"
                  onChange={(e) => setDbForm((f) => ({ ...f, database: e.target.value }))} />
              </div>
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <div style={labelStyle}>用户名</div>
                  <Input value={dbForm.username}
                    onChange={(e) => setDbForm((f) => ({ ...f, username: e.target.value }))} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={labelStyle}>密码</div>
                  <Input.Password value={dbForm.password}
                    onChange={(e) => setDbForm((f) => ({ ...f, password: e.target.value }))} />
                </div>
              </div>
            </>
          )}

          <div>
            <div style={labelStyle}>语义包（决定分析口径）</div>
            <Select style={{ width: '100%' }} allowClear placeholder="稍后可在列表中设置"
              value={dbPack ?? undefined} onChange={(v) => setDbPack(v ?? null)}
              options={packs.map((p) => ({ value: p.id, label: p.name }))} />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Button loading={testing} onClick={testDb}>测试连接</Button>
            {testResult && (
              <span style={{ fontSize: 12.5, color: testResult.ok ? '#16a34a' : '#dc2626' }}>
                {testResult.ok ? '✓ ' : '✗ '}{testResult.message}
              </span>
            )}
          </div>
        </Space>
      </Modal>

      {/* 预览弹窗（数据预览 / SQL 查询 双 Tab） */}
      <Modal
        open={!!previewDs}
        title={`数据预览 · ${previewDs?.name ?? ''}`}
        width={900} footer={null}
        onCancel={() => setPreviewDs(null)}
      >
        <Tabs
          activeKey={previewTab}
          onChange={(k) => {
            setPreviewTab(k as 'data' | 'sql');
            if (k === 'sql' && !sqlText) {
              // 预填一个示例：按第一个文本维度分组计数
              const cols = previewData?.columns ?? [];
              const dim = cols.find((c) => /品类|区域|产线|门店|产品|渠道|工序/.test(c)) ?? cols[0];
              if (dim) setSqlText(`SELECT "${dim}", COUNT(*) AS cnt\nFROM t\nGROUP BY "${dim}"\nORDER BY cnt DESC\nLIMIT 20`);
            }
          }}
          items={[
            {
              key: 'data',
              label: '数据预览',
              children: (
                <>
                  {previewDs?.type === 'db' && previewTables.length > 1 && (
                    <Select
                      style={{ width: 280, marginBottom: 12 }}
                      placeholder="选择表" value={previewTable ?? undefined}
                      onChange={(v) => loadPreview(previewDs.id, v)}
                      options={previewTables.map((t) => ({
                        value: t.name,
                        label: `${t.name}${t.row_estimate != null ? `（约 ${t.row_estimate} 行）` : ''}`,
                      }))}
                    />
                  )}
                  <Spin spinning={previewLoading}>
                    {previewData ? (
                      <>
                        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>
                          前 {previewData.rows.length} 行 · {previewData.columns.length} 列
                        </div>
                        <ResultTable rows={previewData.rows} />
                      </>
                    ) : (
                      <div style={{ padding: 40, textAlign: 'center', color: '#94a3b8' }}>
                        {previewDs?.type === 'db' && !previewTable ? '请选择要预览的表' : '加载中…'}
                      </div>
                    )}
                  </Spin>
                </>
              ),
            },
            {
              key: 'sql',
              label: 'SQL 查询',
              children: (
                <div>
                  <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 8 }}>
                    只读 SELECT 查询（本地执行，不消耗模型额度）；数据表名为
                    <Tag style={{ marginLeft: 6 }}>t</Tag>
                    {previewDs?.type === 'db' && !previewDs.materialized_path && (
                      <span style={{ color: '#ea580c' }}>（数据库源需先物化才能查询）</span>
                    )}
                  </div>
                  <Input.TextArea
                    rows={4} value={sqlText}
                    placeholder="SELECT * FROM t LIMIT 10"
                    style={{ fontFamily: 'Consolas, Monaco, monospace', fontSize: 12.5 }}
                    onChange={(e) => setSqlText(e.target.value)}
                  />
                  <div style={{ display: 'flex', gap: 10, marginTop: 10, marginBottom: 12 }}>
                    <Button type="primary" size="small" icon={<CaretRightOutlined />}
                      loading={sqlRunning} onClick={runSql} disabled={!sqlText.trim()}>执行查询</Button>
                    <Button size="small" onClick={() => { setSqlText(''); setSqlResult(null); }}>清空</Button>
                  </div>
                  {sqlResult && (
                    <>
                      <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>
                        返回 {sqlResult.total} 行{sqlResult.truncated ? '（已截断至 500 行）' : ''} · 耗时本地计算
                      </div>
                      <ResultTable rows={sqlResult.rows} />
                    </>
                  )}
                </div>
              ),
            },
          ]}
        />
      </Modal>

      {/* 物化弹窗 */}
      <Modal
        open={!!matDs}
        title={`物化缓存 · ${matDs?.name ?? ''}`}
        onOk={doMaterialize} onCancel={() => setMatDs(null)}
        okText="开始物化" cancelText="取消" confirmLoading={matting}
      >
        <div style={{ fontSize: 12.5, color: '#64748b', margin: '8px 0 12px' }}>
          沙箱无网络，数据库表会先缓存为 parquet 文件再进入分析。超过行数上限将按时间字段保留最近数据。
        </div>
        <div style={labelStyle}>选择表</div>
        <Select
          style={{ width: '100%' }} placeholder="选择要物化的表"
          value={matTable ?? undefined} onChange={setMatTable}
          options={matTables.map((t) => ({
            value: t.name,
            label: `${t.name}${t.row_estimate != null ? `（约 ${t.row_estimate} 行）` : ''}`,
          }))}
        />
        <div style={{ marginTop: 12 }}>
          <Checkbox checked={matForce} onChange={(e) => setMatForce(e.target.checked)}>
            强制刷新（忽略 TTL 缓存重新物化）
          </Checkbox>
        </div>
        {matDs?.materialized_path && (
          <div style={{ marginTop: 8, fontSize: 12, color: '#94a3b8' }}>
            当前缓存：{matDs.materialized_table} · {matDs.row_count?.toLocaleString() ?? '?'} 行 · {matDs.materialized_at}
          </div>
        )}
      </Modal>
    </div>
  );
}

function SectionTitle({ title, style }: { title: string; style?: React.CSSProperties }) {
  return (
    <div style={{ fontSize: 14.5, fontWeight: 600, margin: '0 0 12px', display: 'flex', alignItems: 'center', gap: 8, ...style }}>
      <span style={{ width: 3, height: 14, borderRadius: 2, background: 'linear-gradient(#5645D4,#7B5CF5)', display: 'inline-block' }} />
      {title}
    </div>
  );
}

const labelStyle: React.CSSProperties = { fontSize: 12.5, color: '#64748b', marginBottom: 4 };
