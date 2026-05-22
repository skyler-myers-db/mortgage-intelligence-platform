export const API_VERSION = 'v1';
export const API_PREFIX = `/api/${API_VERSION}`;

export function apiPath(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  if (normalized === API_PREFIX || normalized.startsWith(`${API_PREFIX}/`)) {
    return normalized;
  }
  const suffix = normalized.startsWith('/api/')
    ? normalized.slice('/api'.length)
    : normalized;
  return `${API_PREFIX}${suffix}`;
}
