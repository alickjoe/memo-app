import { useTranslation } from 'react-i18next'
import i18n from '../i18n/config'

export interface Meeting {
  id: string
  title: string
  audio_path: string
  duration_seconds: number
  created_at: string
  status: 'recording' | 'processing' | 'done' | 'error'
}

interface MeetingCardProps {
  meeting: Meeting
  onClick: () => void
  onDelete?: () => void
}

export default function MeetingCard({ meeting, onClick, onDelete }: MeetingCardProps) {
  const { t } = useTranslation()

  const formatDateTime = (dateStr: string): string => {
    const d = new Date(dateStr)
    const locale = i18n.language === 'zh' ? 'zh-CN' : 'en-US'
    const datePart = d.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
    const timePart = d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
    return `${datePart} ${timePart}`
  }

  const formatDuration = (seconds: number): string => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}:${String(s).padStart(2, '0')}`
  }

  const statusMap: Record<string, { label: string; color: string }> = {
    recording: { label: t('meetingCard.recording'), color: 'bg-red-100 text-red-700' },
    processing: { label: t('meetingCard.processing'), color: 'bg-yellow-100 text-yellow-700' },
    done: { label: t('meetingCard.done'), color: 'bg-green-100 text-green-700' },
    error: { label: t('meetingCard.error'), color: 'bg-gray-100 text-gray-500' },
  }

  const status = statusMap[meeting.status] || statusMap.processing

  return (
    <div
      onClick={onClick}
      className="flex items-center gap-4 px-4 py-3 bg-white border border-gray-100 rounded-lg cursor-pointer hover:border-gray-200 hover:shadow-sm transition-all"
    >
      <div className="flex-1 min-w-0">
        <h3 className="text-sm font-medium text-gray-900 truncate">
          {meeting.title || t('meetingCard.untitled')}
        </h3>
        <p className="text-xs text-gray-400 mt-0.5">
          {formatDateTime(meeting.created_at)} &middot; {formatDuration(meeting.duration_seconds)}
        </p>
      </div>
      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${status.color}`}>
        {status.label}
      </span>
      {onDelete && (
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          className="text-gray-300 hover:text-red-500 transition-colors text-sm leading-none px-1"
          title={t('meetingCard.delete')}
        >
          ×
        </button>
      )}
    </div>
  )
}
