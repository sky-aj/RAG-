import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const markdown = new MarkdownIt({ html: false, linkify: false, breaks: false })
markdown.renderer.rules.text = (tokens, index, _options, env) => {
  const escaped = markdown.utils.escapeHtml(tokens[index].content)
  const inLink = tokens.slice(0, index).reduce((depth, token) =>
    depth + (token.type === 'link_open' ? 1 : token.type === 'link_close' ? -1 : 0), 0)
  const citations = env?.citations
  if (inLink || !(citations instanceof Set)) return escaped
  return escaped.replace(/【片段\s*(\d+)】/g, (label, number: string) =>
    citations.has(Number(number))
      ? `<button type="button" class="citation-link" data-citation="${Number(number)}">${label}</button>`
      : label)
}
markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}

export function renderMarkdown(text: string, citations: number[] = []): string {
  return DOMPurify.sanitize(markdown.render(text, { citations: new Set(citations) }), {
    USE_PROFILES: { html: true }, FORBID_TAGS: ['img', 'iframe', 'style', 'form', 'input']
  })
}
