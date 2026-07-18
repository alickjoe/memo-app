interface MinutesProps {
  minutes: {
    summary: string
    key_points: string[]
    action_items: string[]
    next_steps: string
  }
  language?: string
}

const LABELS: Record<string, { summary: string; keyPoints: string; actionItems: string; nextSteps: string }> = {
  zh: { summary: '摘要', keyPoints: '关键讨论点', actionItems: '行动项', nextSteps: '下一步' },
  en: { summary: 'Summary', keyPoints: 'Key Discussion Points', actionItems: 'Action Items', nextSteps: 'Next Steps' },
}

export default function MinutesPanel({ minutes, language = 'en' }: MinutesProps) {
  const labels = LABELS[language] || LABELS.en
  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Summary */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">{labels.summary}</h2>
        <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
          {minutes.summary}
        </p>
      </section>

      {/* Key Discussion Points */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">{labels.keyPoints}</h2>
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
        <h2 className="text-sm font-semibold text-gray-700 mb-3">{labels.actionItems}</h2>
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
        <h2 className="text-sm font-semibold text-gray-700 mb-3">{labels.nextSteps}</h2>
        <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
          {minutes.next_steps}
        </p>
      </section>
    </div>
  )
}
