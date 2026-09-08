// lib/markdown.js — 安全 Markdown 渲染（页面不出现任何裸 md 符号）
//  - marked + DOMPurify 兜底 XSS
//  - [[wikilink]] / [[wikilink|显示名]] → 可点击芯片 <a class="wl" data-path=...>
//  - 流式阶段用 renderPlain（纯文本），彻底避免残留 # /* 一闪而过
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const WL_RE = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// [[path]] / [[path|display]] → <a class="wl" data-path="path">display</a>
function preprocessWikilinks(src) {
  return String(src).replace(WL_RE, (m, path, disp) => {
    const p = path.trim()
    const label = (disp || p).trim()
    if (!p) return m
    return `<a class="wl" data-path="${escapeHtml(p)}">${escapeHtml(label)}</a>`
  })
}

// 轻量兜底：清除一切可能残留的裸 md 符号（流式未闭合 / 特殊场景）
export function stripResidualMd(src) {
  return String(src)
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[\[([^\]|]*)(?:\|[^\]]*)?\]\]/g, '$1')
    .replace(/(^|\n)\s{0,3}#{1,6}\s+/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/(^|[^*\n])\*([^*\n]+)\*(?!\*)/g, '$1$2')
    .replace(/\|/g, '，')
    .replace(/>\s*/g, '')
    .replace(/^\s*[-*]\s+/gm, '· ')
    .replace(/^(\d+)[.、]\s+/gm, '$1. ')
    // —— 流式安全兜底：清掉未闭合 / 半截残留的裸符号（# 标题、**、`、[[ 等）——
    .replace(/\*\*/g, '')
    .replace(/\*/g, '')
    .replace(/#/g, '')
    .replace(/`/g, '')
    .replace(/\[\[|\]\]/g, '')
    .replace(/\[|\]/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

// 完成态渲染后打磨：清除标记解析残留的裸符号（未闭合 **、孤 #、反引号等）。
// 仅作用于「标签外文本」，完整保护 <pre>/<code> 代码块（其中的 #、* 是代码内容）。
function cleanText(t) {
  return t
    .replace(/\*\*([^*]*)\*\*/g, '$1')
    .replace(/\*\*/g, '')
    .replace(/(^|[^*\n])\*([^*\n]+)\*(?!\*)/g, '$1$2')
    .replace(/\*+/g, '')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/`/g, '')
    .replace(/\[\[|\]\]/g, '')
    .replace(/(^|\n)\s*#{1,6}\s*/g, '$1')
}

const POLISH_RE = /<pre[\s\S]*?<\/pre>|<code>[\s\S]*?<\/code>|<[^>]+>|[^<]+/g

export function polishHtml(html) {
  return String(html).replace(POLISH_RE, (m) => {
    if (m[0] === '<') return m   // 标签 / 代码块：原样保留
    return cleanText(m)
  })
}

// 完成态：渲染为安全 HTML（无裸符号，含未闭合标记兜底）
export function renderMd(src) {
  const withLinks = preprocessWikilinks(src || '')
  // gfm 表格 → 外包可横向滚动的容器，避免超宽表格撑破气泡
  const html = marked
    .parse(withLinks, { breaks: true, gfm: true })
    .replace(/<table>/g, '<div class="md-table-wrap"><table>')
    .replace(/<\/table>/g, '</table></div>')
  return DOMPurify.sanitize(polishHtml(html), { ADD_ATTR: ['data-path'] })
}

// 流式态：纯文本（保留换行，彻底无裸符号）
export function renderPlain(src) {
  return DOMPurify.sanitize(stripResidualMd(src || ''))
}

// 提取 markdown 正文首屏（FileViewer 用）：去掉 YAML frontmatter
export function stripFrontmatter(src) {
  const s = String(src || '')
  if (s.startsWith('---')) {
    const end = s.indexOf('\n---', 3)
    if (end !== -1) return s.slice(end + 4)
  }
  return s
}
