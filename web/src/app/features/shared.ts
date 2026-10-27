/** Small helpers shared by feature pages. */
export const STATUS_LABEL: Record<string, string> = {
  new: 'Queued', analysing: 'Reading RFP', analysed: 'Analysed', drafting: 'Drafting', drafted: 'Draft ready',
  assembling: 'Building PDF', ready: 'PDF ready', failed: 'Failed',
  met: 'Met', partial: 'Needs attention', gap: 'Gap', review: 'Confirm', undertaking: 'Undertaking',
  queued: 'Queued', running: 'Running', awaiting_approval: 'Waiting for approval', done: 'Done',
  pending: 'Pending', approved: 'Approved', rejected: 'Rejected', sent: 'Sent',
  uploaded: 'Queued', processing: 'Reading', ready_doc: 'Ready',
};

export function chipClass(status: string): string {
  if (['met', 'ready', 'done', 'drafted', 'analysed', 'sent', 'approved', 'undertaking'].includes(status)) return 'chip ok';
  if (['gap', 'failed', 'rejected'].includes(status)) return 'chip bad';
  if (['partial', 'review', 'awaiting_approval', 'pending'].includes(status)) return 'chip warn';
  return 'chip blue';
}

/** A date-only value ("2027-08-09") as a local calendar date; new Date() would read it as UTC midnight. */
export function localDate(date: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(date);
}

function calendarDays(d: Date): number {
  const t = new Date();
  return Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
    - new Date(t.getFullYear(), t.getMonth(), t.getDate()).getTime()) / 86_400_000);
}

/** Whole calendar days from today to the date (0 = today, negative = past). */
export function daysUntil(date: string): number { return calendarDays(localDate(date)); }

/** Calendar days to a bid's due date-time, and whether it has passed. A due date stored without a time (local
 *  midnight) counts as the whole day; one with a time has passed once that time is over. */
export function dueIn(due: string): { days: number; passed: boolean } {
  const d = new Date(due), days = calendarDays(d);
  const hasTime = d.getHours() !== 0 || d.getMinutes() !== 0;
  return { days, passed: days < 0 || (hasTime && d.getTime() < Date.now()) };
}

export function validity(date: string | null): { cls: string; text: string } | null {
  if (!date) return null;
  const days = daysUntil(date);
  const shown = localDate(date).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  if (days < 0) return { cls: 'chip bad', text: `Expired ${shown}` };
  if (days < 120) return { cls: 'chip warn', text: `Expires ${shown}` };
  return { cls: 'chip ok', text: `Valid till ${shown}` };
}
