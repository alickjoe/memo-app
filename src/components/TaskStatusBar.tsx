import { useEffect, useState } from 'react'

interface TaskStatusBarProps {
  /** 进行中提示文字（含阶段信息） */
  text: string
  /** 左侧点缀色（默认蓝色：进行中） */
  color?: 'blue' | 'green'
  /** 点击 spinner 后的附加提示（灰色小字） */
  hint?: string
}

/**
 * 任务进行中横幅：spinner + 文字 + 已耗时秒数 ticker。
 * 供"重新生成纪要 / 重新转写"等后台任务复用，让用户确认任务"活着"。
 */
export default function TaskStatusBar({ text, color = 'blue', hint }: TaskStatusBarProps) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  const bg = color === 'blue' ? 'bg-blue-50 border-blue-200' : 'bg-green-50 border-green-200'
  const spinner = color === 'blue' ? 'border-blue-500' : 'border-green-500'
  const textCls = color === 'blue' ? 'text-blue-700' : 'text-green-700'
  const hintCls = color === 'blue' ? 'text-blue-500' : 'text-green-500'

  return (
    <div className={`${bg} border-b px-6 py-2.5 flex items-center gap-3`}>
      <div className={`w-4 h-4 border-2 ${spinner} border-t-transparent rounded-full animate-spin`} />
      <span className={`text-sm font-medium ${textCls}`}>{text}</span>
      <span className={`text-xs ${hintCls}`}>
        {elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m${elapsed % 60}s`}
      </span>
      {hint && <span className={`text-xs ${hintCls} ml-auto`}>{hint}</span>}
    </div>
  )
}
