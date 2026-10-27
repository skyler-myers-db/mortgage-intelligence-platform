/**
 * Ids the expanded row's preview and the table share (the preview is a lazy
 * chunk, LeadRowExpanded; these stay with the table so its focus logic
 * imports nothing of that chunk).
 */

/**
 * id of the expanded row's receipt block: the queue's "View receipt" toast
 * action opens the row and moves focus here (tabIndex -1).
 */
export function leadReceiptAnchorId(borrowerId: string): string {
  return `lead-receipt-${borrowerId}`;
}
