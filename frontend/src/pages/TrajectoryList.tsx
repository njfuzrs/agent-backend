import { useState } from 'react'
import type { Key, ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  SearchOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { FilterValue, SorterResult, TablePaginationConfig } from 'antd/es/table/interface'
import dayjs from 'dayjs'
import {
  batchUpdateTrajectories,
  exportTrajectories,
  fetchTrajectories,
} from '../services/api'
import type { TrajectoryListItem } from '../types/trajectory'
import { downloadBlob, formatCurrency, formatDuration, formatTokens } from '../utils/format'

const { RangePicker } = DatePicker

const TOOL_SOURCE_COLORS: Record<string, string> = {
  'claude-code': 'blue',
  codex: 'green',
  'gemini-cli': 'orange',
  'sid-code': 'purple',
}

const EXIT_STATUS_ICON: Record<string, ReactNode> = {
  end_turn: <CheckCircleOutlined style={{ color: '#52c41a' }} />,
  tool_use: <WarningOutlined style={{ color: '#faad14' }} />,
  error: <CloseCircleOutlined style={{ color: '#ff4d4f' }} />,
}

export default function TrajectoryList() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [filters, setFilters] = useState<Record<string, unknown>>({})
  const [sort, setSort] = useState('-start_time')
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([])
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchSaving, setBatchSaving] = useState(false)
  const [batchForm] = Form.useForm()

  const queryParams = { page, page_size: pageSize, sort, ...filters }

  const { data, isLoading } = useQuery({
    queryKey: ['trajectories', queryParams],
    queryFn: () => fetchTrajectories(queryParams),
  })

  const updateFilter = (key: string, value: unknown) => {
    setFilters(current => {
      const next = { ...current }
      if (value === undefined || value === null || value === '') {
        delete next[key]
      } else {
        next[key] = value
      }
      return next
    })
    setPage(1)
  }

  const columns: ColumnsType<TrajectoryListItem> = [
    {
      title: '时间',
      dataIndex: 'start_time',
      width: 130,
      sorter: true,
      render: (value: string | null) => value ? dayjs(value).format('MM-DD HH:mm') : '-',
    },
    {
      title: '来源',
      dataIndex: 'tool_source',
      width: 110,
      render: (value: string) => <Tag color={TOOL_SOURCE_COLORS[value] || 'default'}>{value}</Tag>,
    },
    {
      title: '模型',
      dataIndex: 'model',
      width: 150,
      ellipsis: true,
      render: (value: string) => value?.replace('claude-', '') || '-',
    },
    {
      title: '首条输入',
      dataIndex: 'first_prompt',
      ellipsis: true,
      render: (value: string) => (
        <Tooltip title={value} placement="topLeft">
          <span>{value?.slice(0, 80) || '-'}</span>
        </Tooltip>
      ),
    },
    {
      title: '步骤',
      dataIndex: 'total_steps',
      width: 70,
      sorter: true,
      align: 'right',
    },
    {
      title: 'Token',
      dataIndex: 'total_tokens',
      width: 90,
      sorter: true,
      align: 'right',
      render: (value: number) => formatTokens(value),
    },
    {
      title: '成本',
      dataIndex: 'total_cost_usd',
      width: 90,
      sorter: true,
      align: 'right',
      render: (value: number) => formatCurrency(value),
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      width: 90,
      align: 'right',
      render: (value: number | null) => formatDuration(value),
    },
    {
      title: '状态',
      dataIndex: 'exit_status',
      width: 70,
      align: 'center',
      render: (value: string) => EXIT_STATUS_ICON[value] || <span>{value || '-'}</span>,
    },
    {
      title: '质量',
      dataIndex: 'quality_status',
      width: 140,
      render: (_value: string, record) => (
        <Space size={4} direction="vertical">
          <Tag color={record.quality_status === 'approved' ? 'success' : record.quality_status === 'rejected' ? 'error' : 'default'}>
            {record.quality_status}
          </Tag>
          <span style={{ color: '#8c8c8c', fontSize: 12 }}>
            {record.quality_rating ? `${'★'.repeat(record.quality_rating)}${'☆'.repeat(5 - record.quality_rating)}` : '未评分'}
          </span>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      fixed: 'right',
      render: (_value, record) => (
        <Button type="link" onClick={() => navigate(`/trajectories/${record.session_id}`)}>
          查看
        </Button>
      ),
    },
  ]

  const handleTableChange = (
    pagination: TablePaginationConfig,
    _filters: Record<string, FilterValue | null>,
    sorter: SorterResult<TrajectoryListItem> | SorterResult<TrajectoryListItem>[]
  ) => {
    setPage(pagination.current ?? 1)
    setPageSize(pagination.pageSize ?? 20)
    if (Array.isArray(sorter)) {
      return
    }

    const field = typeof sorter.field === 'string' ? sorter.field : undefined
    if (field) {
      const direction = sorter.order === 'ascend' ? '' : '-'
      setSort(`${direction}${field}`)
    }
  }

  const handleBatchApply = async () => {
    const values = await batchForm.validateFields()
    setBatchSaving(true)
    try {
      const payload = {
        session_ids: selectedRowKeys,
        quality_status: values.quality_status,
        task_type: values.task_type,
        project_name: values.project_name,
        tags: values.tags?.length ? values.tags : undefined,
      }
      await batchUpdateTrajectories(payload)
      message.success(`已更新 ${selectedRowKeys.length} 条轨迹`)
      setBatchOpen(false)
      setSelectedRowKeys([])
      batchForm.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['trajectories'] })
    } catch {
      message.error('批量更新失败')
    } finally {
      setBatchSaving(false)
    }
  }

  const handleExport = async () => {
    try {
      const blob = await exportTrajectories(selectedRowKeys.map(String))
      downloadBlob(blob, `trajectories-${dayjs().format('YYYYMMDD-HHmmss')}.zip`)
      message.success('导出成功')
    } catch {
      message.error('导出失败')
    }
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
        轨迹列表
      </Typography.Title>

      <Card size="small">
        <Space wrap size="middle">
          <Input
            placeholder="搜索用户输入..."
            prefix={<SearchOutlined />}
            allowClear
            style={{ width: 260 }}
            onChange={event => updateFilter('search', event.target.value)}
          />
          <Select
            placeholder="工具来源"
            allowClear
            style={{ width: 140 }}
            options={[
              { value: 'claude-code', label: 'Claude Code' },
              { value: 'codex', label: 'Codex' },
              { value: 'gemini-cli', label: 'Gemini CLI' },
              { value: 'sid-code', label: 'sid-code' },
            ]}
            onChange={value => updateFilter('tool_source', value)}
          />
          <Select
            placeholder="退出状态"
            allowClear
            style={{ width: 130 }}
            options={[
              { value: 'end_turn', label: '✅ end_turn' },
              { value: 'tool_use', label: '⚠️ tool_use' },
              { value: 'error', label: '❌ error' },
              { value: 'unknown', label: 'unknown' },
            ]}
            onChange={value => updateFilter('exit_status', value)}
          />
          <Select
            placeholder="质量状态"
            allowClear
            style={{ width: 140 }}
            options={[
              { value: 'unreviewed', label: '未评审' },
              { value: 'approved', label: '通过' },
              { value: 'rejected', label: '拒绝' },
            ]}
            onChange={value => updateFilter('quality_status', value)}
          />
          <Select
            placeholder="任务类型"
            allowClear
            style={{ width: 140 }}
            options={[
              { value: 'bug_fix', label: 'Bug Fix' },
              { value: 'feature', label: 'Feature' },
              { value: 'refactor', label: 'Refactor' },
              { value: 'explain', label: 'Explain' },
              { value: 'other', label: 'Other' },
            ]}
            onChange={value => updateFilter('task_type', value)}
          />
          <Input
            placeholder="项目名"
            allowClear
            style={{ width: 180 }}
            onChange={event => updateFilter('project_name', event.target.value)}
          />
          <RangePicker
            onChange={dates => {
              if (dates?.[0] && dates?.[1]) {
                updateFilter('start_date', dates[0].format('YYYY-MM-DD'))
                updateFilter('end_date', dates[1].format('YYYY-MM-DD'))
                return
              }
              updateFilter('start_date', undefined)
              updateFilter('end_date', undefined)
            }}
          />
        </Space>
      </Card>

      <Card size="small">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <Typography.Text style={{ color: '#8c8c8c' }}>
            共 {data?.total ?? 0} 条，已选 {selectedRowKeys.length} 条
          </Typography.Text>
          <Space wrap>
            <Button onClick={() => setBatchOpen(true)} disabled={!selectedRowKeys.length}>
              批量标注
            </Button>
            <Button onClick={handleExport} disabled={!selectedRowKeys.length}>
              批量导出
            </Button>
          </Space>
        </div>
      </Card>

      <Table
        rowKey="session_id"
        rowSelection={{
          selectedRowKeys,
          onChange: keys => setSelectedRowKeys(keys),
        }}
        columns={columns}
        dataSource={data?.items || []}
        loading={isLoading}
        pagination={{
          current: page,
          pageSize,
          total: data?.total || 0,
          showSizeChanger: true,
          showTotal: total => `共 ${total} 条`,
        }}
        onChange={handleTableChange}
        size="small"
        scroll={{ x: 1480 }}
      />

      <Modal
        title="批量标注"
        open={batchOpen}
        onOk={handleBatchApply}
        confirmLoading={batchSaving}
        onCancel={() => setBatchOpen(false)}
        destroyOnHidden
      >
        <Form form={batchForm} layout="vertical">
          <Form.Item name="quality_status" label="质量状态" rules={[{ required: true, message: '请选择质量状态' }]}>
            <Select
              options={[
                { value: 'unreviewed', label: '未评审' },
                { value: 'approved', label: '通过' },
                { value: 'rejected', label: '拒绝' },
              ]}
            />
          </Form.Item>
          <Form.Item name="task_type" label="任务类型">
            <Select
              allowClear
              options={[
                { value: 'bug_fix', label: 'Bug Fix' },
                { value: 'feature', label: 'Feature' },
                { value: 'refactor', label: 'Refactor' },
                { value: 'explain', label: 'Explain' },
                { value: 'other', label: 'Other' },
              ]}
            />
          </Form.Item>
          <Form.Item name="project_name" label="项目名">
            <Input placeholder="批量设置项目名" />
          </Form.Item>
          <Form.Item name="tags" label="标签">
            <Select mode="tags" placeholder="输入一个或多个标签" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
