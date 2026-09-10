import { useState } from 'react'

/**
 * 应用根组件。
 *
 * 当前为脚手架验证页：确认 Tailwind 4 类名生效，后续任务将替换为正式布局。
 */
function App() {
  const [count, setCount] = useState(0)

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-neutral-950 text-neutral-100">
      <h1 className="text-3xl font-bold tracking-tight">SynlysAgent</h1>
      <p className="text-sm text-neutral-400">
        Vite + React + TypeScript + Tailwind CSS 4 就绪
      </p>
      <button
        type="button"
        onClick={() => setCount((count) => count + 1)}
        className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium transition-colors hover:bg-indigo-400"
      >
        Count is {count}
      </button>
    </div>
  )
}

export default App
