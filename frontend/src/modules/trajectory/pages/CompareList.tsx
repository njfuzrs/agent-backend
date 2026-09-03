import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import {
  createCompareGroup,
  deleteCompareGroup,
  fetchCompareGroups,
} from '../services/api'
import type { CompareGroupListItem } from '../types/trajectory'

export default function CompareList() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()

  const { data, isLoading } = useQuery({
    queryKey: ['compare-groups'],
    queryFn: fetchCompareGroups,
  })

  const createMutation = useMutation({
    mutationFn: createCompareGroup,
    onSuccess: async created => {
      message.success('对比组已创建')
      setCreateOpen(false)
      form.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['compare-groups'] })
      navigate(`/compare/${created.id}`)
    },
    onError: () => {
      message.error('创建对比组失败')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteCompareGroup,
    onSuccess: async () => {
      message.success('对比组已删除')
      await queryClient.invalidateQueries({ queryKey: ['compare-groups'] })
    },
    onError: () => {
      message.error('删除对比组失败')
    },
  })

  const columns: ColumnsType<CompareGroupListItem> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_value, record) => (
        <Button type="link" style={{ paddingInline: 0 }} onClick={() => navigate(`/compare/${record.id}`)}>
          {record.name}
        </Button>
      ),
    },
    {
      title: '任务描述',
      dataIndex: 'task_prompt',
      ellipsis: true,
      render: (value: string) => value || '-',
    },
    {
      title: '说明',
      dataIndex: 'description',
      ellipsis: true,
      render: (value: string) => value || '-',
    },
    {
      title: '轨迹数',
      dataIndex: 'item_count',
      width: 90,
      align: 'right',
    },
    {
      title: '来源',
      dataIndex: 'tool_sources',
      width: 220,
      render: (value: string[]) => (
        <Space wrap size={[4, 4]}>
          {value.length ? value.map(item => <Tag key={item}>{item}</Tag>) : '-'}
        </Space>
      ),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 180,
      render: (value: string) => dayjs(value).format('YYYY-MM-DD HH:mm'),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_value, record) => (
        <Popconfirm
          title="删除该对比组？"
          onConfirm={() => deleteMutation.mutate(record.id)}
          okButtonProps={{ loading: deleteMutation.isPending }}
        >
          <Button type="link" danger style={{ paddingInline: 0 }}>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ]

  const handleCreate = async () => {
    const values = await form.validateFields()
    createMutation.mutate({
      name: values.name,
      description: values.description,
      task_prompt: values.task_prompt,
    })
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
            多工具对比
          </Typography.Title>
          <Typography.Text style={{ color: '#8c8c8c' }}>
            管理对比组，并对同一任务下的不同轨迹做横向分析
          </Typography.Text>
        </div>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          新建对比组
        </Button>
      </div>

      <Card size="small">
        <Table
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          pagination={false}
          size="small"
          scroll={{ x: 980 }}
        />
      </Card>

      <Modal
        title="新建对比组"
        open={createOpen}
        onOk={handleCreate}
        confirmLoading={createMutation.isPending}
        onCancel={() => setCreateOpen(false)}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入对比组名称' }]}>
            <Input placeholder="例如：修复 login bug" />
          </Form.Item>
          <Form.Item name="task_prompt" label="任务提示词">
            <Input.TextArea rows={3} placeholder="例如：修复登录页面空指针异常" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} placeholder="记录场景、约束或对比目的" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
