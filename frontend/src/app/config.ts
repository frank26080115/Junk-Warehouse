// frontend/src/app/config.ts
import appconfig from '../../../config/appconfig.json';
export const APP_TZ = (appconfig.timezone ?? 'UTC') as string;

export function formatLocal(d: Date | string) {
  const date = typeof d === 'string' ? new Date(d) : d;
  return new Intl.DateTimeFormat(undefined, {
    timeZone: APP_TZ,
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}
