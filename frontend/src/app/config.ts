// frontend/src/app/config.ts
import appconfig from '../../../config/appconfig.json';

export const APP_TZ = (appconfig.timezone ?? 'UTC') as string;

// Keep the pin window definition in one place so all views remain consistent.
const rawPinHours = Number(appconfig.pin_open_expiry_hours ?? 36);
export const PIN_OPEN_EXPIRY_HOURS = Number.isFinite(rawPinHours) && rawPinHours > 0 ? rawPinHours : 36;
export const PIN_OPEN_EXPIRY_MS = PIN_OPEN_EXPIRY_HOURS * 60 * 60 * 1000;

export function formatLocal(d: Date | string) {
  const date = typeof d === 'string' ? new Date(d) : d;
  return new Intl.DateTimeFormat(undefined, {
    timeZone: APP_TZ,
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}
