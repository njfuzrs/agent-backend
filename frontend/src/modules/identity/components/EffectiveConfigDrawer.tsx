import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Descriptions, Drawer, Empty, Progress, Space, Spin, Table, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { evaluateDeviceBudget } from '../../cost/services/api'
import { fetchEvents } from '../../event/services/api'
import { fetchFlags } from '../../flag/services/api'
import type { FlagItem } from '../../flag/types/flag'
import { evaluateDevicePolicy } from '../../policy/services/api'
import type { DeviceListItem } from '../types/identity'

// D10：回答「这台设备现在被什么管着」。只读，复用已有接口，不新增后端端点：
//   策略 → GET /policies/evaluate（device > team > org 只取一份）
//   预算 → GET /budgets/evaluate（含本周期 used / limit）
//   flag → GET /flags（全局，不分设备；disabled 的不下发）
//   最近一次 policy_enforced → GET /events?event_name=policy_enforced&limit=1

const LAYER_LABEL: Record<string, string> = { device: '设备层', team: '团队层', org: '组织层' }
const PERIOD_LABEL: Record<string, string> = { session: '单会话', daily: '每日', weekly: '每周', monthly: '每月' }

function formatValue(value: unknown) {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function formatUsd(value: number) {
  return `$${value.toFixed(2)}`
}

export default function EffectiveConfigDrawer({
  device,
  onClose,
}: {
  device: DeviceListItem | null
  onClose: () => void
}) {
  const deviceId = device?.device_id ?? ''
  const enabled = device !== null

  const policy = useQuery({
    queryKey: ['effective-policy', deviceId],
    queryFn: () => evaluateDevicePolicy(deviceId),
    enabled,
  })
  const budget = useQuery({
    queryKey: ['effective-budget', deviceId],
    queryFn: () => evaluateDeviceBudget(deviceId),
    enabled,
  })
  const flags = useQuery({ queryKey: ['flags'], queryFn: fetchFlags, enabled })
  const lastEnforced = useQuery({
    queryKey: ['effective-last-enforced', deviceId],
    queryFn: () => fetchEvents({ device_id: deviceId, event_name: 'policy_enforced', limit: 1 }),
    enabled,
  })

  const activeFlags = (flags.data ?? []).filter(f => !f.disabled)
  const enforcedEvent = lastEnforced.data?.items[0]
  const usedPercent = budget.data && budget.data.limit_usd > 0
    ? Math.min(100, Math.round((budget.data.used_usd / budget.data.limit_usd) * 100))
    : 0

  return (
    <Drawer
      title={device ? `生效配置 · ${device.device_id}` : ''}
      open={enabled}
      onClose={onClose}
      size={720}
      destroyOnHidden
    >
      {device && (
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
            组织 <code>{device.org_id}</code>
            {device.team_id ? <>，团队 <code>{device.team_id}</code></> : '，未进团队'}。
            策略与预算各按 设备 &gt; 团队 &gt; 组织 只取一份，不合并字段；上层被整份遮蔽。
            这里是服务端此刻的求值结果，客户端要等下一次拉取才生效。
          </Typography.Paragraph>

          <Card size="small" title="策略">
            {policy.isLoading ? <Spin /> : policy.isError ? (
              <Alert type="error" showIcon title="读取策略失败" />
            ) : policy.data ? (
              <Space orientation="vertical" style={{ width: '100%' }}>
                <Space>
                  <Tag color="blue">{LAYER_LABEL[policy.data.layer] ?? policy.data.layer}</Tag>
                  <span>#{policy.data.policy_id} · {policy.data.scope_id}</span>
                </Space>
                <Typography.Paragraph
                  copyable
                  style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}
                >
                  {JSON.stringify(policy.data.settings, null, 2)}
                </Typography.Paragraph>
              </Space>
            ) : (
              <Empty description="三层都没有启用的策略，客户端只用本地 managed-settings" />
            )}
          </Card>

          <Card size="small" title="预算">
            {budget.isLoading ? <Spin /> : budget.isError ? (
              <Alert type="error" showIcon title="读取预算失败" />
            ) : budget.data ? (
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="命中">
                  <Tag color="blue">{LAYER_LABEL[budget.data.layer] ?? budget.data.layer}</Tag>
                  #{budget.data.budget_id} · {budget.data.scope_id}
                </Descriptions.Item>
                <Descriptions.Item label="执行">
                  {budget.data.enforcement === 'block' ? <Tag color="red">超额拦截</Tag> : <Tag>只告警</Tag>}
                </Descriptions.Item>
                <Descriptions.Item label="周期">
                  {PERIOD_LABEL[budget.data.period] ?? budget.data.period}（{budget.data.period_key}）
                </Descriptions.Item>
                <Descriptions.Item label="已用 / 上限">
                  {formatUsd(budget.data.used_usd)} / {formatUsd(budget.data.limit_usd)}
                </Descriptions.Item>
                <Descriptions.Item label="进度" span={2}>
                  <Progress
                    percent={usedPercent}
                    status={usedPercent >= 100 ? 'exception' : 'normal'}
                    aria-label="预算使用进度"
                  />
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description="三层都没有启用的预算" />
            )}
          </Card>

          <Card size="small" title={`Feature Flag（全局，${activeFlags.length} 个下发中）`}>
            {flags.isError ? (
              <Alert type="error" showIcon title="读取 flag 失败" />
            ) : (
              <Table<FlagItem>
                rowKey="key"
                size="small"
                loading={flags.isLoading}
                dataSource={activeFlags}
                pagination={false}
                locale={{ emptyText: '没有下发中的 flag，客户端全部用默认值' }}
                columns={[
                  { title: 'key', dataIndex: 'key', width: 220, render: (v: string) => <code>{v}</code> },
                  { title: '值', dataIndex: 'value', render: formatValue },
                ]}
              />
            )}
          </Card>

          <Card size="small" title="最近一次 policy_enforced">
            {lastEnforced.isLoading ? <Spin /> : lastEnforced.isError ? (
              <Alert type="error" showIcon title="读取事件失败" />
            ) : enforcedEvent ? (
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="收到时间">
                  {dayjs(enforcedEvent.received_at).format('YYYY-MM-DD HH:mm:ss')}
                </Descriptions.Item>
                <Descriptions.Item label="会话">{enforcedEvent.session_id || '—'}</Descriptions.Item>
                <Descriptions.Item label="metadata">
                  <code style={{ fontSize: 12 }}>{JSON.stringify(enforcedEvent.metadata)}</code>
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description="这台设备还没上报过 policy_enforced，无法确认客户端已应用策略" />
            )}
          </Card>
        </Space>
      )}
    </Drawer>
  )
}
