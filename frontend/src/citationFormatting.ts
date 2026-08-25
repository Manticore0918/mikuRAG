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
])

function positivePage(value: string | number | string[] | undefined) {
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

  const headingPath = locator.heading_path
  if (Array.isArray(headingPath) && headingPath.length) {
    labels.push(headingPath.join(' › '))
  }
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
