import { useEffect, useState } from 'react'
import { Button, Select, Space, message } from 'antd'
import { updateTrajectory } from '../services/api'

interface TagManagerProps {
  sessionId: string
  tags: string[]
  onUpdated?: () => void | Promise<void>
}

export default function TagManager({ sessionId, tags, onUpdated }: TagManagerProps) {
  const [value, setValue] = useState<string[]>(tags)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setValue(tags)
  }, [tags])

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateTrajectory(sessionId, { tags: value })
      message.success('标签已保存')
      await onUpdated?.()
    } catch {
      message.error('标签保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space wrap size="middle" style={{ width: '100%' }}>
      <Select
        mode="tags"
        value={value}
        onChange={setValue}
        style={{ minWidth: 280, flex: 1 }}
        placeholder="添加标签"
        disabled={saving}
      />
      <Button onClick={handleSave} loading={saving}>
        保存标签
      </Button>
    </Space>
  )
}
