'use client';

function parseInline(text, keyPrefix = 'inline') {
  const nodes = [];
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\((https?:\/\/[^)\s]+)\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
  let lastIndex = 0;
  let match;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    const key = `${keyPrefix}-${nodes.length}`;

    if (token.startsWith('**') || token.startsWith('__')) {
      nodes.push(<strong key={key}>{parseInline(token.slice(2, -2), key)}</strong>);
    } else if (token.startsWith('`')) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith('[')) {
      const labelEnd = token.indexOf('](');
      const label = token.slice(1, labelEnd);
      const href = token.slice(labelEnd + 2, -1);
      nodes.push(
        <a key={key} href={href} target="_blank" rel="noreferrer">
          {label}
        </a>
      );
    } else {
      nodes.push(<em key={key}>{parseInline(token.slice(1, -1), key)}</em>);
    }

    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes.length ? nodes : text;
}

function flushParagraph(blocks, paragraph, keyPrefix) {
  if (!paragraph.length) return;
  const text = paragraph.join(' ');
  blocks.push(<p key={`${keyPrefix}-p-${blocks.length}`}>{parseInline(text, `${keyPrefix}-p-${blocks.length}`)}</p>);
  paragraph.length = 0;
}

export default function MarkdownMessage({ text }) {
  const lines = String(text || '').split(/\r?\n/);
  const blocks = [];
  const paragraph = [];
  let list = null;
  let code = null;

  const flushList = () => {
    if (!list) return;
    const Tag = list.ordered ? 'ol' : 'ul';
    blocks.push(
      <Tag key={`md-list-${blocks.length}`}>
        {list.items.map((item, index) => (
          <li key={index}>{parseInline(item, `md-list-${blocks.length}-${index}`)}</li>
        ))}
      </Tag>
    );
    list = null;
  };

  lines.forEach((line) => {
    if (line.trim().startsWith('```')) {
      flushParagraph(blocks, paragraph, 'md');
      flushList();
      if (code) {
        blocks.push(<pre key={`md-code-${blocks.length}`}><code>{code.join('\n')}</code></pre>);
        code = null;
      } else {
        code = [];
      }
      return;
    }

    if (code) {
      code.push(line);
      return;
    }

    if (!line.trim()) {
      flushParagraph(blocks, paragraph, 'md');
      flushList();
      return;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph(blocks, paragraph, 'md');
      flushList();
      const level = heading[1].length;
      const Tag = `h${level + 2}`;
      blocks.push(<Tag key={`md-heading-${blocks.length}`}>{parseInline(heading[2], `md-heading-${blocks.length}`)}</Tag>);
      return;
    }

    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph(blocks, paragraph, 'md');
      const orderedList = Boolean(ordered);
      if (!list || list.ordered !== orderedList) {
        flushList();
        list = { ordered: orderedList, items: [] };
      }
      list.items.push((unordered || ordered)[1]);
      return;
    }

    const quote = line.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph(blocks, paragraph, 'md');
      flushList();
      blocks.push(<blockquote key={`md-quote-${blocks.length}`}>{parseInline(quote[1], `md-quote-${blocks.length}`)}</blockquote>);
      return;
    }

    paragraph.push(line.trim());
  });

  flushParagraph(blocks, paragraph, 'md');
  flushList();

  if (code) {
    blocks.push(<pre key={`md-code-${blocks.length}`}><code>{code.join('\n')}</code></pre>);
  }

  return <div className="markdown-message">{blocks}</div>;
}
