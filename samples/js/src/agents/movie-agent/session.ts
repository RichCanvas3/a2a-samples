import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { defineChain, http, createPublicClient, type Chain } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';

type Hex = `0x${string}`;

type SessionPackage = {
  chainId: number;
  aa: Hex; // smart account (delegator)
  sessionAA?: Hex; // delegate smart account (optional)
  reputationRegistry: Hex;
  selector: Hex;
  sessionKey: {
    privateKey: Hex;
    address: Hex;
    validAfter: number;
    validUntil: number;
  };
  entryPoint: Hex;
  bundlerUrl: string;
  delegationRedeemData?: Hex; // optional pre-encoded redeemDelegations call data
  signedDelegation: {
    message: {
      delegate: Hex;
      delegator: Hex;
      authority: Hex;
      caveats: any[];
      salt: Hex;
      signature: Hex;
    };
    signature: Hex;
  };
};

export type DelegationSetup = {
  chainId: number;
  chain: Chain;
  rpcUrl: string;
  bundlerUrl: string;
  entryPoint: Hex;
  aa: Hex;
  sessionAA?: Hex;
  reputationRegistry: Hex;
  selector: Hex;
  sessionKey: SessionPackage['sessionKey'];
  signedDelegation: SessionPackage['signedDelegation'];
  delegationRedeemData?: Hex;
  publicClient: any;
};

export function loadSessionPackage(): SessionPackage {
  const __filename = fileURLToPath(import.meta.url);
  const __dirname = path.dirname(__filename);
  const p = path.join(__dirname, 'sessionPackage.json.secret');
  const raw = fs.readFileSync(p, 'utf-8');
  const parsed = JSON.parse(raw);
  return parsed as SessionPackage;
}

export function validateSessionPackage(pkg: SessionPackage): void {
  if (!pkg.chainId) throw new Error('sessionPackage.chainId is required');
  if (!pkg.aa) throw new Error('sessionPackage.aa is required');
  if (!pkg.entryPoint) throw new Error('sessionPackage.entryPoint is required');
  if (!pkg.bundlerUrl) throw new Error('sessionPackage.bundlerUrl is required');
  if (!pkg.sessionKey?.privateKey || !pkg.sessionKey?.address) {
    throw new Error('sessionPackage.sessionKey.privateKey and address are required');
  }
  if (!pkg.signedDelegation?.signature) {
    throw new Error('sessionPackage.signedDelegation.signature is required');
  }
}

function defaultRpcUrlFor(chainId: number): string | null {
  if (process.env.RPC_URL) return process.env.RPC_URL;
  if (process.env.JSON_RPC_URL) return process.env.JSON_RPC_URL;
  switch (chainId) {
    case 11155111: return 'https://rpc.sepolia.org';
    case 1: return 'https://rpc.ankr.com/eth';
    default: return null;
  }
}

export function buildDelegationSetup(pkg?: SessionPackage): DelegationSetup {
  const session = pkg ?? loadSessionPackage();
  validateSessionPackage(session);
  const rpcUrl = defaultRpcUrlFor(session.chainId);
  if (!rpcUrl) throw new Error(`RPC URL not provided and no default known for chainId ${session.chainId}`);
  const chain = defineChain({
    id: session.chainId,
    name: `chain-${session.chainId}`,
    nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
    rpcUrls: { default: { http: [rpcUrl] }, public: { http: [rpcUrl] } },
  });
  const publicClient: any = createPublicClient({ transport: http(rpcUrl) });
  return {
    chainId: session.chainId,
    chain,
    rpcUrl,
    bundlerUrl: session.bundlerUrl,
    entryPoint: session.entryPoint,
    aa: session.aa,
    sessionAA: session.sessionAA,
    reputationRegistry: session.reputationRegistry,
    selector: session.selector,
    sessionKey: session.sessionKey,
    signedDelegation: session.signedDelegation,
    delegationRedeemData: session.delegationRedeemData,
    publicClient,
  };
}

export async function buildAgentAccountFromSession(): Promise<any> {
  const sp = buildDelegationSetup();
  const client = createPublicClient({ transport: http(sp.rpcUrl) });
  // Try multiple known builder APIs from permissionless/accounts to maximize compatibility across versions
  let mod: any;
  try {
    mod = await import('permissionless/accounts');
  } catch (e) {
    throw new Error('permissionless/accounts not installed. Install it or provide agentAccount externally.');
  }

  const attempts: Array<() => Promise<any>> = [];
  const owner = privateKeyToAccount(sp.sessionKey.privateKey);

  
  // Most recent APIs expect an owner Account, not raw privateKey
  if (typeof mod?.toSimpleSmartAccount === 'function') {
    attempts.push(() => mod.toSimpleSmartAccount({ client, owner, entryPoint: sp.entryPoint }));
  }
  if (typeof mod?.to7702SimpleSmartAccount === 'function') {
    attempts.push(() => mod.to7702SimpleSmartAccount({ client, owner, entryPoint: sp.entryPoint }));
  }
  if (typeof mod?.privateKeyToSimpleSmartAccount === 'function') {
    attempts.push(() => mod.privateKeyToSimpleSmartAccount({ client, privateKey: sp.sessionKey.privateKey, entryPoint: sp.entryPoint }));
  }

  for (const fn of attempts) {
    try {
      const account = await fn();
      if (account) return account;
    } catch {}
  }

  const available = Object.keys(mod || {});
  throw new Error(`No compatible smart account builder found in permissionless/accounts. Available exports: ${available.join(', ')}`);
}


