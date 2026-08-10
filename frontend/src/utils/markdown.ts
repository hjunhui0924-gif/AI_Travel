import { marked } from "marked";
import DOMPurify from "dompurify";

marked.setOptions({ breaks: true, gfm: true });

/** Render trusted-structure Markdown from the backend with XSS filtering. */
export function renderMarkdown(text: string): string {
  const html = marked.parse(text ?? "", { async: false });
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}

/** Inline markdown for sentence-level answer segments. */
export function renderInlineMarkdown(text: string): string {
  const html = marked.parseInline(text ?? "", { async: false });
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}

/** Only http/https URLs may become clickable links (handoff §10). */
export function safeExternalUrl(url: string | undefined | null): string | null {
  const raw = url?.trim();
  if (!raw || !/^https?:\/\//i.test(raw)) return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch {
    return null;
  }
  return null;
}

/** Backend image attachments may be inline raster data or HTTPS/HTTP URLs. */
export function safeImageUrl(url: string | undefined | null): string | null {
  const raw = url?.trim();
  if (!raw) return null;
  if (/^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(raw)) return raw;
  return safeExternalUrl(raw);
}
