import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { sentenceLines } from './src/prose.ts'
const cases: [string, string][] = JSON.parse(readFileSync(new URL('../../tests/fixtures/sentence-lines.json', import.meta.url), 'utf8'))
for (const [source, expected] of cases) {
  test(source, () => {
    assert.equal(sentenceLines(source), expected)
    assert.equal(sentenceLines(expected), expected)
  })
}
