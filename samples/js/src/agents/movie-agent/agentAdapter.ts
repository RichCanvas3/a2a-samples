import { createPublicClient, createWalletClient, custom, http, defineChain, encodeFunctionData, encodeAbiParameters, keccak256, zeroAddress, toHex, type Address, type Chain, type PublicClient, type Account } from "viem";
import { identityRegistryAbi } from "../../lib/abi/identityRegistry.js";
import { reputationRegistryAbi } from "../../lib/abi/reputationRegistry.js";
import { createBundlerClient, createPaymasterClient } from 'viem/account-abstraction';
import { buildDelegationSetup } from './session.js';
import { privateKeyToAccount } from 'viem/accounts';
import { keccak256 } from 'viem';
import {
    Implementation,
    toMetaMaskSmartAccount,
    type MetaMaskSmartAccount,
    type DelegationStruct,
    type ExecutionStruct,
    createDelegation,
    type ToMetaMaskSmartAccountReturnType,
    DelegationFramework,
    SINGLE_DEFAULT_MODE,
    getExplorerTransactionLink,
    getExplorerAddressLink,
    createExecution,
    getDelegationHashOffchain,
    Delegation
  } from "@metamask/delegation-toolkit";
import { sepolia } from "viem/chains";
import { getFeedbackDatabase, type FeedbackRecord } from './feedbackStorage.js';

// -------------------- feedbackAuth helpers --------------------
const FEEDBACK_DOMAIN = '0x7f8a2c3b4d9f3e0c1b0d1a29e5a2f6ac2a9f2a0c4c3a2b195e4c2aee2a9f7f60' as `0x${string}`;
type FeedbackAuthPayload = {
  agentId: bigint;
  clientAddress: `0x${string}`;
  indexLimit: bigint;     // uint64
  expiry: bigint;         // uint64
  chainId: bigint;        // uint256
  identityRegistry: `0x${string}`;
  signerAddress: `0x${string}`;
};

async function fetchIdentityRegistry(publicClient: PublicClient, reputationRegistry: `0x${string}`): Promise<`0x${string}`> {
  return await publicClient.readContract({
    address: reputationRegistry,
    abi: reputationRegistryAbi,
    functionName: 'getIdentityRegistry',
    args: [],
  }) as `0x${string}`;
}

async function fetchLastIndex(publicClient: PublicClient, reputationRegistry: `0x${string}`, agentId: bigint, clientAddress: `0x${string}`): Promise<bigint> {
  return await publicClient.readContract({
    address: reputationRegistry,
    abi: reputationRegistryAbi,
    functionName: 'getLastIndex',
    args: [agentId, clientAddress],
  }) as bigint;
}

function encodeFeedbackAuthPayload(payload: FeedbackAuthPayload): `0x${string}` {
  return encodeAbiParameters(
    [{
      type: 'tuple',
      components: [
        { name: 'agentId',          type: 'uint256' },
        { name: 'clientAddress',    type: 'address' },
        { name: 'indexLimit',       type: 'uint64'  },
        { name: 'expiry',           type: 'uint64'  },
        { name: 'chainId',          type: 'uint256' },
        { name: 'identityRegistry', type: 'address' },
        { name: 'signerAddress',    type: 'address' },
      ],
    }],
    ([[
      payload.agentId,
      payload.clientAddress,
      payload.indexLimit,
      payload.expiry,
      payload.chainId,
      payload.identityRegistry,
      payload.signerAddress,
    ]] as any),
  ) as `0x${string}`;
}

async function signFeedbackAuthMessage(params: {
  account: Account;
  message: `0x${string}`;
}): Promise<`0x${string}`> {

  if (params.account?.signMessage) {
    console.info('*************** signFeedbackAuthMessage: params.account', params.account.address);
    return await params.account.signMessage({ message: { raw: params.message } });
  }
}

export async function createFeedbackAuth(params: {
  publicClient: PublicClient;
  reputationRegistry: `0x${string}`;
  agentId: bigint;
  clientAddress: `0x${string}`;
  signer: Account;
  walletClient?: any;
  indexLimitOverride?: bigint;
  expirySeconds?: number;
  identityRegistryOverride?: `0x${string}`;
  chainIdOverride?: bigint;
}): Promise<`0x${string}`> {
  const {
    publicClient,
    reputationRegistry,
    agentId,
    clientAddress,
    signer,
    walletClient,
    indexLimitOverride,
    expirySeconds = 3600,
    identityRegistryOverride,
    chainIdOverride,
  } = params;

  const nowSec = BigInt(Math.floor(Date.now() / 1000));
  const chainId = chainIdOverride ?? BigInt(publicClient.chain?.id ?? 0);
  const identityRegistry = identityRegistryOverride ?? await fetchIdentityRegistry(publicClient, reputationRegistry);
  const U64_MAX = 18446744073709551615n;
  const lastIndexFetched = indexLimitOverride !== undefined
    ? (indexLimitOverride - 1n)
    : await fetchLastIndex(publicClient, reputationRegistry, agentId, clientAddress);
  const lastIndex = lastIndexFetched > U64_MAX ? U64_MAX : lastIndexFetched;
  let indexLimit = indexLimitOverride ?? (lastIndex + 1n);
  if (indexLimit > U64_MAX) {
    console.warn('[FeedbackAuth] Computed indexLimit exceeds uint64; clamping to max');
    indexLimit = U64_MAX;
  }
  let expiry = nowSec + BigInt(expirySeconds);
  if (expiry > U64_MAX) {
    console.warn('[FeedbackAuth] Computed expiry exceeds uint64; clamping to max');
    expiry = U64_MAX;
  }

  const payload: FeedbackAuthPayload = {
    agentId,
    clientAddress,
    indexLimit,
    expiry,
    chainId,
    identityRegistry,
    signerAddress: signer.address as `0x${string}`,
  };

  // Build the domain-separated inner hash exactly as the contract does
  const inner = keccak256(
    encodeAbiParameters(
      [
        { type: 'bytes32' },  // FEEDBACK_DOMAIN
        { type: 'uint64'  },  // chainId
        { type: 'address' },  // reputationRegistry (address(this))
        { type: 'address' },  // identityRegistry
        { type: 'uint256' },  // agentId
        { type: 'address' },  // clientAddress
        { type: 'uint64'  },  // indexLimit
        { type: 'uint64'  },  // expiry
        { type: 'address' },  // signer
      ],
      [
        FEEDBACK_DOMAIN,
        BigInt(chainId),
        reputationRegistry,
        identityRegistry,
        agentId,
        clientAddress,
        indexLimit,
        expiry,
        payload.signerAddress,
      ],
    ),
  );

  // Sign inner; the solidity code calls toEthSignedMessageHash(inner), which viem applies for signMessage
  const signature = await signFeedbackAuthMessage({ account: signer, message: inner });

  const full = encodeAbiParameters(
    [
      {
        type: 'tuple',
        components: [
          { name: 'agentId',          type: 'uint256' },
          { name: 'clientAddress',    type: 'address' },
          { name: 'indexLimit',       type: 'uint64'  },
          { name: 'expiry',           type: 'uint64'  },
          { name: 'chainId',          type: 'uint256' },
          { name: 'identityRegistry', type: 'address' },
          { name: 'signerAddress',    type: 'address' },
        ],
      },
      { type: 'bytes' },
    ],
    ([[
      payload.agentId,
      payload.clientAddress,
      payload.indexLimit,
      payload.expiry,
      payload.chainId,
      payload.identityRegistry,
      payload.signerAddress,
    ], signature] as any),
  );
  return full as `0x${string}`;
}

export type AgentInfo = {
  agentId: bigint;
  agentDomain: string;
  agentAddress: Address;
};

export type AgentAdapterConfig = {
  registryAddress: Address;
  rpcUrl?: string;
};

export function createAgentAdapter(config: AgentAdapterConfig) {
  function getPublicClient() {
    if (config.rpcUrl) {
      return createPublicClient({ transport: http(config.rpcUrl) });
    }
    if (typeof window !== 'undefined' && (window as any).ethereum) {
      return createPublicClient({ transport: custom((window as any).ethereum) });
    }
    throw new Error('Missing RPC URL. Provide config.rpcUrl or ensure window.ethereum is available.');
  }

  async function getAgentCount(): Promise<bigint> {
    const publicClient = getPublicClient();
    return await publicClient.readContract({
      address: config.registryAddress,
      abi: identityRegistryAbi,
      functionName: "getAgentCount",
      args: [],
    }) as bigint;
  }

  async function getAgent(agentId: bigint): Promise<AgentInfo> {
    const publicClient = getPublicClient();
    const res = await publicClient.readContract({
      address: config.registryAddress,
      abi: identityRegistryAbi,
      functionName: "getAgent",
      args: [agentId],
    }) as any;
    return {
      agentId: BigInt(res.agentId ?? agentId),
      agentDomain: res.agentDomain,
      agentAddress: res.agentAddress as Address,
    };
  }

  async function resolveByDomain(agentDomain: string): Promise<AgentInfo> {
    const publicClient = getPublicClient();
    const res = await publicClient.readContract({
      address: config.registryAddress,
      abi: identityRegistryAbi,
      functionName: "resolveByDomain",
      args: [agentDomain],
    }) as any;
    return {
      agentId: BigInt(res.agentId),
      agentDomain: res.agentDomain,
      agentAddress: res.agentAddress as Address,
    };
  }

  async function resolveByAddress(agentAddress: Address): Promise<AgentInfo> {
    const publicClient = getPublicClient();
    const res = await publicClient.readContract({
      address: config.registryAddress,
      abi: identityRegistryAbi,
      functionName: "resolveByAddress",
      args: [agentAddress],
    }) as any;
    return {
      agentId: BigInt(res.agentId),
      agentDomain: res.agentDomain,
      agentAddress: res.agentAddress as Address,
    };
  }

  function getWalletClient() {
    if (typeof window === "undefined") return null;
    const eth: any = (window as any).ethereum;
    if (!eth) return null;
    const chain = inferChainFromProvider(eth, config.rpcUrl);
    return createWalletClient({ chain, transport: custom(eth) });
  }

  function inferChainFromProvider(provider: any, fallbackRpcUrl?: string): Chain {
    // Best-effort sync read; if it fails, default to mainnet + provided rpc
    const rpcUrl = fallbackRpcUrl || 'https://rpc.ankr.com/eth';
    let chainIdHex: string | undefined;
    try { chainIdHex = provider?.chainId; } catch {}
    const readChainId = () => {
      if (chainIdHex && typeof chainIdHex === 'string') return chainIdHex;
      return undefined;
    };
    const hex = readChainId();
    const id = hex ? parseInt(hex, 16) : 1;
    return defineChain({
      id,
      name: `chain-${id}`,
      nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
      rpcUrls: { default: { http: [rpcUrl] }, public: { http: [rpcUrl] } },
    });
  }

  async function registerByDomainWithProvider(agentDomain: string, eip1193Provider: any): Promise<`0x${string}`> {
    const accounts = await eip1193Provider.request({ method: 'eth_accounts' }).catch(() => []);
    const from: Address = (accounts && accounts[0]) as Address;
    if (!from) throw new Error('No account from provider');
    const chain = inferChainFromProvider(eip1193Provider, config.rpcUrl);
    const walletClient = createWalletClient({ chain, transport: custom(eip1193Provider as any) });
    const hash = await walletClient.writeContract({
      address: config.registryAddress,
      abi: identityRegistryAbi,
      functionName: 'registerByDomain',
      args: [agentDomain, from],
      account: from,
      chain,
    });
    return hash as `0x${string}`;
  }

  return {
    // getPublicClient intentionally not exported; consumers use helpers below
    getAgentCount,
    getAgent,
    resolveByDomain,
    resolveByAddress,
    getWalletClient,
    registerByDomainWithProvider,
  };
}


export async function getAgentByDomain(params: {
  publicClient: PublicClient,
  registry: `0x${string}`,
  domain: string,
}): Promise<`0x${string}` | null> {
  const { publicClient, registry } = params;
  const domain = params.domain.trim().toLowerCase();
  const zero = '0x0000000000000000000000000000000000000000';
  try {
    const info: any = await publicClient.readContract({ address: registry, abi: identityRegistryAbi as any, functionName: 'resolveByDomain' as any, args: [domain] });
    const addr = (info?.agentAddress ?? info?.[2]) as `0x${string}` | undefined;
    if (addr && addr !== zero) return addr;
  } catch {}
  const fns: Array<'agentOfDomain' | 'getAgent' | 'agents'> = ['agentOfDomain', 'getAgent', 'agents'];
  for (const fn of fns) {
    try {
      const addr = await publicClient.readContract({ address: registry, abi: identityRegistryAbi as any, functionName: fn as any, args: [domain] }) as `0x${string}`;
      if (addr && addr !== zero) return addr;
    } catch {}
  }
  return null;
}

export async function getAgentInfoByDomain(params: {
  publicClient: PublicClient,
  registry: `0x${string}`,
  domain: string,
}): Promise<{ agentId: bigint; agentAddress: `0x${string}` } | null> {
  const { publicClient, registry } = params;
  const domain = params.domain.trim().toLowerCase();
  try {
    const info: any = await publicClient.readContract({
      address: registry,
      abi: identityRegistryAbi as any,
      functionName: 'resolveByDomain' as any,
      args: [domain],
    });
    const agentId = BigInt(info?.agentId ?? info?.[0] ?? 0);
    const agentAddress = (info?.agentAddress ?? info?.[2]) as `0x${string}` | undefined;
    if (agentId > 0n && agentAddress) return { agentId, agentAddress };
  } catch {}
  return null;
}

export async function deploySmartAccountIfNeeded(params: {
  bundlerUrl: string,
  chain: Chain,
  account: { isDeployed: () => Promise<boolean> }
}): Promise<boolean> {
  const { bundlerUrl, chain, account } = params;
  const isDeployed = await account.isDeployed();
  if (isDeployed) return false;
  const bundlerClient = createBundlerClient({ transport: http(bundlerUrl), chain: chain as any, paymaster: true as any, paymasterContext: { mode: 'SPONSORED' } } as any);
  
  // Set generous gas limits for deployment
  const gasConfig = {
    callGasLimit: 2000000n, // 2M gas for deployment (higher than regular calls)
    verificationGasLimit: 2000000n, // 2M gas for verification
    preVerificationGas: 200000n, // 200K gas for pre-verification
    maxFeePerGas: 1000000000n, // 1 gwei max fee
    maxPriorityFeePerGas: 1000000000n, // 1 gwei priority fee
  };
  
  console.info('*************** deploySmartAccountIfNeeded with gas config:', gasConfig);
  const userOperationHash = await (bundlerClient as any).sendUserOperation({ 
    account, 
    calls: [{ to: zeroAddress }],
    ...gasConfig
  });
  await (bundlerClient as any).waitForUserOperationReceipt({ hash: userOperationHash });
  return true;
}

export async function sendSponsoredUserOperation(params: {
  bundlerUrl: string,
  chain: Chain,
  account: any,
  calls: { to: `0x${string}`; data?: `0x${string}`; value?: bigint }[],
}): Promise<`0x${string}`> {
  const { bundlerUrl, chain, account, calls } = params;
  const paymasterClient = createPaymasterClient({ transport: http(bundlerUrl) } as any);
  const bundlerClient = createBundlerClient({
    transport: http(process.env.BUNDLER_URL || ''),
    paymaster: true,
    chain: sepolia,
    paymasterContext: {
      mode:             'SPONSORED',
    },
  });

  // Set generous gas limits for the user operation
  const gasConfig = {
    callGasLimit: 1000000n, // 1M gas for the call
    verificationGasLimit: 1000000n, // 1M gas for verification
    preVerificationGas: 100000n, // 100K gas for pre-verification
    maxFeePerGas: 1000000000n, // 1 gwei max fee
    maxPriorityFeePerGas: 1000000000n, // 1 gwei priority fee
  };
  
  const userOpHash = await (bundlerClient as any).sendUserOperation({ 
    account, 
    calls, 
    ...gasConfig
  });
  console.info("*************** sendSponsoredUserOperation: userOpHash", userOpHash);

  const userOperationReceipt = await bundlerClient.waitForUserOperationReceipt({ hash: userOpHash });
  console.info("*************** sendSponsoredUserOperation: userOperationReceipt", userOperationReceipt);
  return userOpHash as `0x${string}`;
}


// -------------------- Reputation Registry (ERC-8004-like) via Delegation Toolkit --------------------



async function encodeDelegationRedeem(params: {
  delegationChain: any[];
  execution: ExecutionStruct;
}): Promise<`0x${string}`> {
  const { delegationChain, execution } = params;

  // Normalize signed delegation shape: v0.11 expects a flat object
  // { delegate, authority, caveats, salt, signature }
  // whereas newer packages may provide { message: { ... }, signature }.
  const normalizeSignedDelegation = (sd: any) => {
    if (sd && sd.message && typeof sd.message === 'object') {
      const { delegate, delegator, authority, caveats, salt } = sd.message;
      console.info('*************** normalizeSignedDelegation: caveats from message:', JSON.stringify(caveats, null, 2));
      
      return {
        delegate,
        delegator,
        authority,
        caveats: Array.isArray(caveats) ? caveats : [],
        salt,
        signature: sd.signature,
      };
    }
    return sd;
  };

  const normalizedChain = delegationChain.map((sd) => normalizeSignedDelegation(sd));

  console.info('***************  encodeDelegationRedeem: ', normalizedChain);
  const data = DelegationFramework.encode.redeemDelegations({
    delegations: [ normalizedChain ],
    modes: [SINGLE_DEFAULT_MODE],
    executions: [[execution]]
  });
  return data;
}

export async function giveFeedbackWithDelegation(params: {
  agentId: bigint;
  score?: number; // 0..100
  tag1?: `0x${string}`; // bytes32
  tag2?: `0x${string}`; // bytes32
  fileuri?: string;
  filehash?: `0x${string}`; // bytes32
  feedbackAuth?: `0x${string}`; // bytes
  agentAccount?: any; // Smart account configured for the session key
}): Promise<`0x${string}`> {
  const { agentId, agentAccount } = params;
  const sp = buildDelegationSetup();
  console.info('*************** buildDelegationSetup: sp.signedDelegation:', JSON.stringify(sp.signedDelegation, null, 2));

  const clientPrivateKey = (process.env.CLIENT_PRIVATE_KEY || '').trim() as `0x${string}`;
  if (!clientPrivateKey || !clientPrivateKey.startsWith('0x')) {
    throw new Error('CLIENT_PRIVATE_KEY not set or invalid. Please set a 0x-prefixed 32-byte hex in .env');
  }
  const clientAccount = privateKeyToAccount(clientPrivateKey);
  const clientAddress = clientAccount.address as `0x${string}`;

  // Encode the target contract call
  const zeroBytes32 = '0x0000000000000000000000000000000000000000000000000000000000000000' as `0x${string}`;
  const score = typeof params.score === 'number' ? Math.max(0, Math.min(100, Math.floor(params.score))) : 80;
  const tag1 = params.tag1 || zeroBytes32;
  const tag2 = params.tag2 || zeroBytes32;
  const fileuri = params.fileuri || '';
  const filehash = params.filehash || zeroBytes32;

  // If feedbackAuth not provided, build and sign it (EIP-191 / ERC-1271 verification on-chain)
  let feedbackAuth = (params.feedbackAuth || '0x') as `0x${string}`;
  if (!params.feedbackAuth || params.feedbackAuth === '0x') {
    const publicClient = createPublicClient({ chain: sepolia, transport: http(sp.rpcUrl) });

    // signer: agent owner/operator derived from session key
    console.info('*************** createFeedbackAuth: sp.sessionAA', sp.sessionAA);
    const owner = sp.sessionAA
    const ownerEOA = privateKeyToAccount(sp.sessionKey.privateKey);

    const signerSmartAccount = await toMetaMaskSmartAccount({
      client: publicClient,
      chain: sepolia,
      implementation: Implementation.Hybrid,
      address: sp.sessionAA as `0x${string}`,
      signatory: { account: ownerEOA as any },
    } as any);

    console.info('*************** createFeedbackAuth: owner', owner);
    console.info('*************** createFeedbackAuth: clientAddress', clientAddress);
    console.info('*************** createFeedbackAuth: sp.reputationRegistry', sp.reputationRegistry);
    console.info('*************** createFeedbackAuth: agentId', agentId);
    console.info('*************** createFeedbackAuth: expirySeconds', Number(process.env.ERC8004_FEEDBACKAUTH_TTL_SEC || 3600));
    feedbackAuth = await createFeedbackAuth({
      publicClient,
      reputationRegistry: sp.reputationRegistry as `0x${string}`,
      agentId,
      clientAddress,
      signer: signerSmartAccount,
      // expirySeconds, indexLimitOverride can be passed via env if needed
      expirySeconds: Number(process.env.ERC8004_FEEDBACKAUTH_TTL_SEC || 3600),
    });
  }
  console.info('*************** createFeedbackAuth: feedbackAuth', feedbackAuth);


  // Simple EOA example: send direct tx from client EOA (from .env CLIENT_PRIVATE_KEY) to ReputationRegistry.giveFeedback

  // construct wallet client bound to rpc; pass account explicitly to writeContract
  const walletClient = createWalletClient({ chain: sepolia, transport: http(sp.rpcUrl) }) as any;
  console.info('*************** call reputation registry giveFeedback');
  const txHash = await walletClient.writeContract({
    address: sp.reputationRegistry as `0x${string}`,
    abi: reputationRegistryAbi,
    functionName: 'giveFeedback',
    args: [agentId, score, tag1, tag2, fileuri, filehash, feedbackAuth],
    account: clientAccount,
  });
  console.info('*************** createFeedbackAuth: txHash', txHash);
  const receiptClient = createPublicClient({ chain: sepolia, transport: http(sp.rpcUrl) });
  const receipt = await receiptClient.waitForTransactionReceipt({ hash: txHash as `0x${string}` });
  console.info('*************** createFeedbackAuth: receipt', receipt);
  return txHash as `0x${string}`;

  /*

  const callData = encodeFunctionData({
    abi: reputationRegistryAbi,
    functionName: 'giveFeedback',
    args: [agentId, score, tag1, tag2, fileuri, filehash, feedbackAuth],
  });

  const execution = {
    target: sp.reputationRegistry as `0x${string}`,
    value: 0n,
    callData: callData as `0x${string}`,
  };

  // Wrap in delegation framework call data
  let data: `0x${string}`;
  try {
    console.info('***************  encodeDelegationRedeem: ', sp.signedDelegation, execution);
    data = await encodeDelegationRedeem({ delegationChain: [sp.signedDelegation], execution });
    console.info('***************  encodeDelegationRedeem: ', data);
  } catch (e: any) {
    console.info('***************  encodeDelegationRedeem: error', e);
    if (sp.delegationRedeemData) {
      data = sp.delegationRedeemData as `0x${string}`;
    } else {
      throw e;
    }
  }

  // Build session smart account from sessionKey if not provided
  let account = agentAccount;
  if (!account) {
    console.info('*************** construct session account toMetaMaskSmartAccount');
    console.info("********** sp", sp.chain);
    const owner = privateKeyToAccount(sp.sessionKey.privateKey);

    const publicClient = createPublicClient({
      chain: sepolia,
      transport: http(sp.rpcUrl),
    });

    account = await toMetaMaskSmartAccount({
      client: publicClient,
      chain: sepolia,
      implementation: Implementation.Hybrid,
      deployParams: [owner.address, [], [], []],
      signatory: { account: owner as any },
      deploySalt: toHex(10),
    } as any);
  }

  console.info('*************** construct session account toMetaMaskSmartAccount: account', account);



  // data is guaranteed by encodeDelegationRedeem

  const userOpHash = await sendSponsoredUserOperation({
    bundlerUrl: process.env.BUNDLER_URL || '',
    chain: sepolia,
    account,
    calls: [
      { to: senderAA, data, value: 0n }
    ],
  });

  console.info("*************** sendSponsoredUserOperation: userOpHash", userOpHash);

  return userOpHash as `0x${string}`;
  */

}


export async function addFeedback(params: {
  agentId?: bigint;
  domain?: string;
  rating: number; // 1-5 scale
  comment: string;
  feedbackAuthId?: string;
  taskId?: string;
  contextId?: string;
  isReserve?: boolean;
  proofOfPayment?: string;
}): Promise<{
  status: string;
  agentId: string;
  domain: string;
  rating: number;
  comment: string;
  feedbackId?: number;
}> {
  const { rating, comment, feedbackAuthId, taskId, contextId, isReserve = false, proofOfPayment } = params;
  
  // Use environment variables or defaults
  const agentId = params.agentId || BigInt(process.env.AGENT_CLIENT_ID || '12');
  const domain = params.domain || process.env.AGENT_DOMAIN || 'movieclient.localhost:3001';
  
  try {
    console.info('ERC-8004: addFeedback(agentId=%s, domain=%s, rating=%s)', agentId.toString(), domain, rating);
    
    // Get chain ID from environment or default to Sepolia
    const chainId = process.env.ERC8004_CHAIN_ID || '11155111';
    
    const finalFeedbackAuthId = feedbackAuthId || '';
    
    // Determine agent skill ID
    const agentSkillId = isReserve ? 'reserve:v1' : 'finder:v1';
    
    // Convert rating from 1-5 scale to percentage (0-100)
    const ratingPct = Math.max(0, Math.min(100, rating * 20));
    
    // Create feedback record
    const feedbackRecord: Omit<FeedbackRecord, 'id' | 'createdAt'> = {
      feedbackAuthId: finalFeedbackAuthId || '',
      agentSkillId,
      taskId: taskId ? String(taskId) : '',
      contextId: contextId ? String(contextId) : '',
      rating: ratingPct,
      domain,
      notes: comment,
      proofOfPayment: proofOfPayment || undefined
    };
    
    // Save to database
    const feedbackDb = getFeedbackDatabase();
    const feedbackId = feedbackDb.addFeedback(feedbackRecord);
    
    console.info('ERC-8004: Feedback saved with ID:', feedbackId);
    
    return {
      status: 'ok',
      agentId: agentId.toString(),
      domain,
      rating,
      comment,
      feedbackId
    };
    
  } catch (error: any) {
    console.info('ERC-8004: addFeedback failed:', error?.message || error);
    return {
      status: 'error',
      agentId: agentId.toString(),
      domain,
      rating,
      comment
    };
  }
}


