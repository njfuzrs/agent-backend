import { useEffect, useState } from 'react'
import { Button, Input, Rate, Select, Space, message } from 'antd'
import type { TrajectoryMeta } from '../types/trajectory'
import { updateTrajectory } from '../services/api'

const TASK_TYPE_OPTIONS = [
  { value: 'bug_fix', label: 'Bug Fix' },
  { value: 'feature', label: 'Feature' },
  { value: 'refactor', label: 'Refactor' },
  { value: 'explain', label: 'Explain' },
  { value: 'other', label: 'Other' },
]

interface QualityRatingProps {
  meta: TrajectoryMeta
  onUpdated?: () => void | Promise<void>
}

export default function QualityRating({ meta, onUpdated }: QualityRatingProps) {
  const [rating, setRating] = useState(meta.quality_rating || 0)
  const [qualityStatus, setQualityStatus] = useState(meta.quality_status)
  const [qualityNotes, setQualityNotes] = useState(meta.quality_notes)
  const [taskType, setTaskType] = useState(meta.task_type)
  const [projectName, setProjectName] = useState(meta.project_name)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setRating(meta.quality_rating || 0)
    setQualityStatus(meta.quality_status)
    setQualityNotes(meta.quality_notes)
    setTaskType(meta.task_type)
    setProjectName(meta.project_name)
  }, [meta])

  const saveUpdate = async (payload: Record<string, unknown>, successText: string) => {
    setSaving(true)
    try {
      await updateTrajectory(meta.session_id, payload)
      message.success(successText)
      await onUpdated?.()
    } catch {
      message.error('保存失败')
      throw new Error('save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleRatingChange = async (value: number) => {
    const next = value || 0
    const prev = rating
    setRating(next)
    try {
      await saveUpdate({ quality_rating: next }, '评分已保存')
    } catch {
      setRating(prev)
    }
  }

  const handleStatusChange = async (value: string) => {
    const prev = qualityStatus
    setQualityStatus(value)
    try {
      await saveUpdate({ quality_status: value }, '状态已保存')
    } catch {
      setQualityStatus(prev)
    }
  }

  const handleMetaSave = async () => {
    await saveUpdate(
      {
        quality_notes: qualityNotes,
        task_type: taskType || 'other',
        project_name: projectName,
      },
      '备注与分类已保存'
    )
  }

  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <Space size="large" wrap>
        <span>
          质量评分：
          <Rate value={rating} onChange={handleRatingChange} disabled={saving} />
        </span>
        <span>
          状态：
          <Select
            value={qualityStatus}
            onChange={handleStatusChange}
            size="small"
            style={{ width: 140 }}
            disabled={saving}
            options={[
              { value: 'unreviewed', label: '未评审' },
              { value: 'approved', label: '通过' },
              { value: 'rejected', label: '拒绝' },
            ]}
          />
        </span>
      </Space>

      <Space wrap size="middle" style={{ width: '100%' }}>
        <Select
          value={taskType || 'other'}
          onChange={setTaskType}
          style={{ width: 180 }}
          options={TASK_TYPE_OPTIONS}
          disabled={saving}
        />
        <Input
          value={projectName}
          onChange={event => setProjectName(event.target.value)}
          placeholder="项目名"
          style={{ width: 220 }}
          disabled={saving}
        />
        <Button type="primary" onClick={handleMetaSave} loading={saving}>
          保存备注与分类
        </Button>
      </Space>

      <Input.TextArea
        value={qualityNotes}
        onChange={event => setQualityNotes(event.target.value)}
        placeholder="质量备注"
        autoSize={{ minRows: 3, maxRows: 6 }}
        disabled={saving}
      />
    </div>
  )
}
