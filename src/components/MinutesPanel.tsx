import { useTranslation } from 'react-i18next'

interface MinutesProps {
  minutes: {
    summary: string
    key_points: string[]
    action_items: string[]
    next_steps: string
    language?: string | null
  }
}

/** 纪要标题语言：优先用生成时记录的输出语言；历史行无 language 时按内容启发式判断 */
function resolveMinutesLng(minutes: MinutesProps['minutes'], defaultLng: string): string {
  if (minutes.language === 'zh' || minutes.language === 'en') return minutes.language
  const text = `${minutes.summary}${minutes.next_steps}`
  const cjk = (text.match(/[\u4e00-\u9fff]/g) || []).length
  if (cjk > 20) return 'zh'
  if (text.trim().length > 0 && cjk === 0) return 'en'
  return defaultLng
}

export default function MinutesPanel({ minutes }: MinutesProps) {
  const { t, i18n } = useTranslation()
  const lng = resolveMinutesLng(minutes, i18n.language || 'en')
  const h = (key: string) => t(key, { lng })
  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Summary */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">{h('minutes.summary')}</h2>
        <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
          {minutes.summary}
        </p>
      </section>

      {/* Key Discussion Points */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">{h('minutes.keyPoints')}</h2>
        <ul className="space-y-2">
          {minutes.key_points.map((point, idx) => (
            <li key={idx} className="flex gap-2 text-sm text-gray-600">
              <span className="text-primary-500 mt-0.5">&bull;</span>
              <span>{point}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* Action Items */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">{h('minutes.actionItems')}</h2>
        <ul className="space-y-2">
          {minutes.action_items.map((item, idx) => (
            <li key={idx} className="flex items-center gap-2 text-sm text-gray-600">
              <input type="checkbox" className="rounded border-gray-300" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* Next Steps */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">{h('minutes.nextSteps')}</h2>
        <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
          {minutes.next_steps}
        </p>
      </section>
    </div>
  )
}
