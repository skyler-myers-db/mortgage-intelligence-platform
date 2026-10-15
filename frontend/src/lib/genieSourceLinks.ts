import { SOURCE_LINE_RE, catalogExplorerUrl } from './ucAssetLinks';

/**
 * Where the "Source: catalog.schema.table" linker splits a run of Genie
 * answer text (GenieAnswer.markdown.tsx renderSourceLinks). One pure splitter
 * read by two consumers: the renderer turns each `link` part into the Catalog
 * Explorer anchor, and the figure highlighter (lib/genieFigureTokens) counts
 * figure tokens over exactly the same parts, so a split can never create or
 * hide a figure the count did not see.
 *
 * Degrades to one text part whenever the workspace host is unknown or no
 * captured token is a 3-part UC name with a resolvable link.
 */
export type SourceLinkPart =
  | { kind: 'text'; text: string }
  | { kind: 'link'; text: string; href: string };

export function splitSourceLinks(text: string, workspaceHost: string | null | undefined): SourceLinkPart[] {
  if (!workspaceHost || !text.includes('.')) return [{ kind: 'text', text }];
  const out: SourceLinkPart[] = [];
  const re = new RegExp(SOURCE_LINE_RE.source, SOURCE_LINE_RE.flags);
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const [whole, label, asset] = match;
    const href = catalogExplorerUrl(workspaceHost, asset);
    if (!href) continue;
    if (match.index > last) out.push({ kind: 'text', text: text.slice(last, match.index) });
    out.push({ kind: 'text', text: label });
    out.push({ kind: 'link', text: asset, href });
    last = match.index + whole.length;
  }
  if (out.length === 0) return [{ kind: 'text', text }];
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}
