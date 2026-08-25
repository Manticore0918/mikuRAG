import type { Citation } from './api'

const hiddenLocatorKeys = new Set([
  'page',
  'start_page',
  'end_page',
  'heading_path',
  'source_document_id',
  'source_content_hash',
  'summary_model',
  'summary_prompt_version',
  'element',
  'element_start',
  'element_end',
  'language',
  'line_start',
  'line_end',
  'module',
  'path',
  'source_kind',
  'source_path',
  'source_uri',
  'symbol',
  'tags',
  'text_start',
  'text_end',
  'source_author',
  'source_branch',
  'source_commit',
  'source_repository',
  'source_revision',
  'source_title',
  'source_version',
])

function positivePage(value: string | number | string[] | undefined) {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null
}

function positiveLine(value: string | number | string[] | undefined) {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null
}

export function formatCitationLocator(locator: Citation['locator']) {
  const labels: string[] = []
  const startPage = positivePage(locator.start_page)
  const endPage = positivePage(locator.end_page)
  const legacyPage = positivePage(locator.page)
  if (startPage && endPage && endPage >= startPage) {
    labels.push(startPage === endPage ? `p. ${startPage}` : `pp. ${startPage}-${endPage}`)
  } else if (legacyPage) {
    labels.push(`p. ${legacyPage}`)
  }

  const sourceKind = typeof locator.source_kind === 'string' ? locator.source_kind : null
  const headingPath = locator.heading_path
  if (sourceKind !== 'code' && Array.isArray(headingPath) && headingPath.length) {
    labels.push(headingPath.join(' › '))
  }

  const sourcePath = typeof locator.path === 'string'
    ? locator.path
    : typeof locator.source_path === 'string' ? locator.source_path : null
  const lineStart = positiveLine(locator.line_start)
  const lineEnd = positiveLine(locator.line_end)
  if (sourceKind === 'code' && sourcePath) {
    const range = lineStart
      ? lineEnd && lineEnd !== lineStart ? `${lineStart}-${lineEnd}` : String(lineStart)
      : null
    labels.push(range ? `${sourcePath}:${range}` : sourcePath)
  } else if (lineStart) {
    labels.push(lineEnd && lineEnd !== lineStart ? `lines ${lineStart}-${lineEnd}` : `line ${lineStart}`)
  }

  const symbol = typeof locator.symbol === 'string' ? locator.symbol : null
  if (symbol) labels.push(`symbol ${symbol}`)

  const element = typeof locator.element === 'string' ? locator.element : null
  const elementStart = typeof locator.element_start === 'string' ? locator.element_start : null
  const elementEnd = typeof locator.element_end === 'string' ? locator.element_end : null
  if (element) labels.push(element)
  else if (elementStart) labels.push(elementEnd && elementEnd !== elementStart
    ? `${elementStart} → ${elementEnd}`
    : elementStart)

  const sourceUri = typeof locator.source_uri === 'string' ? locator.source_uri : null
  if (sourceUri) labels.push(sourceUri)

  for (const [key, value] of Object.entries(locator)) {
    if (
      hiddenLocatorKeys.has(key)
      || key === 'parent_chunk_id'
      || key.endsWith('_parent_id')
    ) continue
    const rendered = Array.isArray(value) ? value.join(' › ') : String(value)
    if (rendered) labels.push(`${key}: ${rendered}`)
  }
  return labels.join(' · ') || 'Document excerpt'
}
