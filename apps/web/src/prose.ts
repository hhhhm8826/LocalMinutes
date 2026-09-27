// Display only: preserve editable and stored source text.
const abbreviations = new Set(['mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'vs', 'etc', 'e.g', 'i.e'])
export function sentenceLines(value: string = ''): string {
  const text = value.replace(/\r\n?/g, '\n')
  return text.replace(/\.(["'”’」』)\]]*)(?:[ \t]+|(?=[가-힣]))/g, (match: string, closing: string, offset: number) => {
    const token = text.slice(0, offset).match(/[^\s]+$/)?.[0] || ''
    if (abbreviations.has(token.toLowerCase()) || /^(?:[A-Za-z]\.)*[A-Za-z]$/.test(token) || /^[0-9.]+$/.test(token) || token.endsWith('.')) return match
    if (!/[ \t]$/.test(match) && (token.includes('://') || token.includes('@') || token.startsWith('www.'))) return match
    return '.' + closing + (text[offset + match.length] === '\n' ? '' : '\n')
  })
}
