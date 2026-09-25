import { toast } from './toast';

/**
 * Copy a link to the clipboard and say so in a shell toast (audit tables-09;
 * extracted from Portfolio Builder's "Copy link", which keeps its exact copy).
 *
 * It never throws: an unavailable or blocked Clipboard API (Safari private
 * mode, an old browser, a denied permission) raises the failure toast
 * instead. The URL is shown to nobody and sent nowhere else: it is never
 * passed to RUM or to the client error log.
 */
export interface CopyLinkMessages {
  success: string;
  successDetail?: string | null;
  failure: string;
  failureDetail?: string | null;
}

export async function copyLink(url: string, messages: CopyLinkMessages): Promise<boolean> {
  let copied = false;
  try {
    await navigator.clipboard.writeText(url);
    copied = true;
  } catch {
    copied = false;
  }
  if (copied) toast.success(messages.success, { detail: messages.successDetail ?? null });
  else toast.error(messages.failure, { detail: messages.failureDetail ?? null });
  return copied;
}
