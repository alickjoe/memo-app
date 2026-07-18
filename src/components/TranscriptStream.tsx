import { useTranslation } from 'react-i18next'

interface TranscriptSegment {
  speaker: string
  text: string
  start_time: number
}

interface TranscriptStreamProps {
  segments: TranscriptSegment[]
}

export default function TranscriptStream({ segments }: TranscriptStreamProps) {
  const { t } = useTranslation()
  if (segments.length === 0) {
    return (
      <div className="text-center text-gray-400 text-sm py-8">
        {t('transcript.waitingAudio')}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {segments.map((seg, idx) => (
        <div key={idx} className="flex gap-3 items-start group">
          <span
            className={`text-xs font-medium px-1.5 py-0.5 rounded mt-0.5 ${
              seg.speaker === 'Speaker A'
                ? 'bg-blue-100 text-blue-700'
                : 'bg-green-100 text-green-700'
            }`}
          >
            {seg.speaker}
          </span>
          <div className="flex-1">
            <p className="text-sm text-gray-700 leading-relaxed">{seg.text}</p>
            <span className="text-xs text-gray-300 mt-0.5">
              {formatTime(seg.start_time)}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}
