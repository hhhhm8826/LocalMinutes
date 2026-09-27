import { confirmAction } from './confirm'
import { useEffect, useId } from 'react'

const pending = new Set<string>()
export function useUnsaved(value: boolean) {
  const id = useId()
  useEffect(() => {
    if (value) pending.add(id)
    else pending.delete(id)
    const handler = (event: BeforeUnloadEvent) => { if (pending.size) event.preventDefault() }
    window.addEventListener('beforeunload', handler)
    return () => { pending.delete(id); window.removeEventListener('beforeunload', handler) }
  }, [id, value])
}
export async function confirmNavigation() {
  return pending.size === 0 || confirmAction('저장하지 않은 변경이 있습니다. 변경을 버리고 이동할까요?')
}
