type Headers = Record<string, string>;

type RequestOptions = {
  data?: Record<string, unknown>;
  headers?: Headers;
  timeout?: number;
};

export type LiveCampaignResponse = {
  status(): number;
  json(): Promise<unknown>;
};

export type LiveCampaignRequest = {
  get(url: string, options?: RequestOptions): Promise<LiveCampaignResponse>;
  patch(url: string, options?: RequestOptions): Promise<LiveCampaignResponse>;
  post(url: string, options?: RequestOptions): Promise<LiveCampaignResponse>;
};

type Sleep = (milliseconds: number) => Promise<void>;

const defaultSleep: Sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export async function reconcileGenieCampaignAction(
  request: LiveCampaignRequest,
  options: {
    apiUrl: string;
    authHeaders: Headers;
    submittedPayload: Record<string, unknown>;
    attempts?: number;
    sleep?: Sleep;
  },
): Promise<string> {
  const attempts = options.attempts ?? 5;
  const sleep = options.sleep ?? defaultSleep;
  let lastResult = 'no replay attempted';
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let replay: LiveCampaignResponse;
    try {
      replay = await request.post(`${options.apiUrl}/api/genie/actions`, {
        headers: { ...options.authHeaders, 'Content-Type': 'application/json' },
        data: options.submittedPayload,
        timeout: 30_000,
      });
    } catch {
      lastResult = 'POST transport error';
      if (attempt + 1 < attempts) await sleep(1_000);
      continue;
    }
    lastResult = `POST ${replay.status()}`;
    if (replay.status() === 200) {
      try {
        const body = record(await replay.json());
        const campaignId = typeof body?.campaign_id === 'string'
          ? body.campaign_id.trim()
          : '';
        if (campaignId) return campaignId;
        lastResult = 'POST 200 without campaign_id';
      } catch {
        lastResult = 'POST 200 with unreadable response';
      }
    } else if (replay.status() !== 429 && replay.status() < 500) {
      throw new Error(`Genie campaign replay was rejected: ${lastResult}`);
    }
    if (attempt + 1 < attempts) await sleep(1_000);
  }
  throw new Error(`Genie campaign replay did not recover its durable ID: ${lastResult}`);
}

export async function archiveLiveCampaign(
  request: LiveCampaignRequest,
  options: {
    adminBearer: string;
    apiUrl: string;
    campaignId: string;
    expectedName: string;
    attempts?: number;
    sleep?: Sleep;
  },
): Promise<void> {
  if (!options.adminBearer) {
    throw new Error('live campaign teardown requires the distinct admin bearer');
  }
  const attempts = options.attempts ?? 10;
  const sleep = options.sleep ?? defaultSleep;
  let lastResult = 'no request attempted';
  let observedName: string | null = null;
  let capturedName = false;

  const captureName = (body: Record<string, unknown>): void => {
    if (capturedName) return;
    capturedName = true;
    observedName = typeof body.name === 'string' ? body.name : null;
  };
  const assertExpectedNameAfterCleanup = (): void => {
    if (observedName !== options.expectedName) {
      throw new Error(
        `live campaign marker was not persisted: expected ${options.expectedName}, `
        + `got ${observedName ?? 'missing name'}`,
      );
    }
  };

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    let current: LiveCampaignResponse;
    try {
      current = await request.get(`${options.apiUrl}/api/campaigns/${options.campaignId}`, {
        headers: { Authorization: `Bearer ${options.adminBearer}` },
        timeout: 30_000,
      });
    } catch {
      lastResult = 'GET transport error';
      if (attempt + 1 < attempts) await sleep(1_000);
      continue;
    }
    lastResult = `GET ${current.status()}`;
    if (current.status() === 200) {
      let campaign: Record<string, unknown> | null = null;
      try {
        campaign = record(await current.json());
      } catch {
        lastResult = 'GET 200 with unreadable response';
      }
      if (!campaign) {
        if (attempt + 1 < attempts) await sleep(1_000);
        continue;
      }
      captureName(campaign);
      const currentStatus = typeof campaign.status === 'string' ? campaign.status : '';
      if (currentStatus === 'archived') {
        assertExpectedNameAfterCleanup();
        return;
      }
      if (!currentStatus) {
        lastResult = 'GET 200 without campaign status';
        if (attempt + 1 < attempts) await sleep(1_000);
        continue;
      }
      let patchStatus: number | null = null;
      try {
        const archived = await request.patch(
          `${options.apiUrl}/api/campaigns/${options.campaignId}`,
          {
            headers: {
              Authorization: `Bearer ${options.adminBearer}`,
              'Content-Type': 'application/json',
            },
            data: {
              status: 'archived',
              expected_status: currentStatus,
              rationale: 'Archive the exact live Genie action fixture.',
            },
            timeout: 30_000,
          },
        );
        patchStatus = archived.status();
        lastResult = `PATCH ${patchStatus}`;
      } catch {
        lastResult = 'PATCH transport error';
      }
      if (
        patchStatus !== null
        && patchStatus !== 200
        && patchStatus !== 409
        && patchStatus !== 429
        && patchStatus < 500
      ) {
        throw new Error(`live campaign teardown was rejected: ${lastResult}`);
      }
      // A transport exception does not prove the PATCH failed. Always perform
      // the same-attempt GET so the final retry can reconcile a committed
      // archive and still validate its persisted run marker.
      let confirmed: LiveCampaignResponse;
      try {
        confirmed = await request.get(
          `${options.apiUrl}/api/campaigns/${options.campaignId}`,
          {
            headers: { Authorization: `Bearer ${options.adminBearer}` },
            timeout: 30_000,
          },
        );
      } catch {
        lastResult = 'final GET transport error';
        if (attempt + 1 < attempts) await sleep(1_000);
        continue;
      }
      lastResult = `final GET ${confirmed.status()}`;
      if (confirmed.status() === 200) {
        let body: Record<string, unknown> | null = null;
        try {
          body = record(await confirmed.json());
        } catch {
          lastResult = 'final GET 200 with unreadable response';
        }
        if (body) {
          captureName(body);
          if (body.status === 'archived') {
            assertExpectedNameAfterCleanup();
            return;
          }
        } else if (lastResult === `final GET ${confirmed.status()}`) {
          lastResult = 'final GET 200 with malformed response';
        }
      }
    }
    if (attempt + 1 < attempts) await sleep(1_000);
  }
  throw new Error(`live campaign teardown did not converge: ${lastResult}`);
}
