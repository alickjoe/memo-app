interface MinutesProps {
  minutes: {
    summary: string
    key_points: string[]
    action_items: string[]
    next_steps: string
  }
}

export default function MinutesPanel({ minutes }: MinutesProps) {
  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* 摘要 */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">摘要</h2>
        <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
          {minutes.summary}
        </p>
      </section>

      {/* 关键讨论点 */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">关键讨论点</h2>
        <ul className="space-y-2">
          {minutes.key_points.map((point, idx) => (
            <li key={idx} className="flex gap-2 text-sm text-gray-600">
              <span className="text-primary-500 mt-0.5">&bull;</span>
              <span>{point}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* 行动项 */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">行动项</h2>
        <ul className="space-y-2">
          {minutes.action_items.map((item, idx) => (
            <li key={idx} className="flex items-center gap-2 text-sm text-gray-600">
              <input type="checkbox" className="rounded border-gray-300" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* 下一步 */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">下一步</h2>
        <p className="text-sm text-gray-600 leading-relaxed whitespace-pre-wrap">
          {minutes.next_steps}
        </p>
      </section>
    </div>
  )
}
