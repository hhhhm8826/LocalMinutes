let active = false

export function confirmAction(message: string): Promise<boolean> {
  if (active) return Promise.resolve(false)
  active = true
  const previous = document.activeElement as HTMLElement | null
  return new Promise(resolve => {
    const dialog = document.createElement('dialog')
    dialog.className = 'confirmation-modal'
    dialog.setAttribute('aria-labelledby', 'confirmation-title')
    dialog.setAttribute('aria-describedby', 'confirmation-message')
    const title = document.createElement('h2')
    title.id = 'confirmation-title'
    title.textContent = '작업 확인'
    const description = document.createElement('p')
    description.id = 'confirmation-message'
    description.textContent = message
    const actions = document.createElement('div')
    actions.className = 'confirmation-actions'
    const cancel = document.createElement('button')
    cancel.type = 'button'
    cancel.textContent = '취소'
    cancel.autofocus = true
    const accept = document.createElement('button')
    accept.type = 'button'
    accept.className = 'primary'
    accept.textContent = '확인'
    let settled = false
    const finish = (accepted: boolean) => {
      if (settled) return
      settled = true
      dialog.close()
      dialog.remove()
      active = false
      if (previous?.isConnected) previous.focus()
      resolve(accepted)
    }
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return
      event.preventDefault()
      const next = document.activeElement === cancel ? accept : cancel
      next.focus()
    })
    cancel.onclick = () => finish(false)
    accept.onclick = () => finish(true)
    dialog.addEventListener('cancel', event => { event.preventDefault(); finish(false) })
    actions.append(cancel, accept)
    dialog.append(title, description, actions)
    document.body.append(dialog)
    dialog.showModal()
    cancel.focus()
  })
}
