import { createPublicClient, createWalletClient, custom, http, defineChain, encodeFunctionData, zeroAddress, toHex, type Address, type Chain, type PublicClient } from "viem";
import { identityRegistryAbi } from "../../lib/abi/identityRegistry.js";
import { createBundlerClient, createPaymasterClient } from 'viem/account-abstraction';
import { buildDelegationSetup } from './session.js';
import { privateKeyToAccount } from 'viem/accounts';

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

// -------------------- AA helpers for Identity Registration (per user spec) --------------------

export const identityRegistrationAbi = [
  {
    type: 'function',
    name: 'newAgent',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'domain', type: 'string' },
      { name: 'agentAccount', type: 'address' },
    ],
    outputs: [
      { name: 'agentId', type: 'uint256' },
    ],
  },
  {
    type: 'function',
    name: 'resolveByDomain',
    stateMutability: 'view',
    inputs: [{ name: 'agentDomain', type: 'string' }],
    outputs: [
      {
        name: 'agentInfo',
        type: 'tuple',
        components: [
          { name: 'agentId', type: 'uint256' },
          { name: 'agentDomain', type: 'string' },
          { name: 'agentAddress', type: 'address' },
        ],
      },
    ],
  },
  { type: 'function', name: 'agentOfDomain', stateMutability: 'view', inputs: [{ name: 'domain', type: 'string' }], outputs: [{ name: 'agent', type: 'address' }] },
  { type: 'function', name: 'getAgent', stateMutability: 'view', inputs: [{ name: 'domain', type: 'string' }], outputs: [{ name: 'agent', type: 'address' }] },
  { type: 'function', name: 'agents', stateMutability: 'view', inputs: [{ name: 'domain', type: 'string' }], outputs: [{ name: 'agent', type: 'address' }] },
] as const;

export function encodeNewAgent(domain: string, agentAccount: `0x${string}`): `0x${string}` {
  return encodeFunctionData({
    abi: identityRegistrationAbi,
    functionName: 'newAgent',
    args: [domain, agentAccount],
  });
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
    const info: any = await publicClient.readContract({ address: registry, abi: identityRegistrationAbi as any, functionName: 'resolveByDomain' as any, args: [domain] });
    const addr = (info?.agentAddress ?? info?.[2]) as `0x${string}` | undefined;
    if (addr && addr !== zero) return addr;
  } catch {}
  const fns: Array<'agentOfDomain' | 'getAgent' | 'agents'> = ['agentOfDomain', 'getAgent', 'agents'];
  for (const fn of fns) {
    try {
      const addr = await publicClient.readContract({ address: registry, abi: identityRegistrationAbi as any, functionName: fn as any, args: [domain] }) as `0x${string}`;
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
      abi: identityRegistrationAbi as any,
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

export async function ensureIdentityWithAA(params: {
  publicClient: PublicClient,
  bundlerUrl: string,
  chain: Chain,
  registry: `0x${string}`,
  domain: string,
  agentAccount: any,
}): Promise<`0x${string}`> {
  const { publicClient, bundlerUrl, chain, registry, domain, agentAccount } = params;
  const existing = await getAgentByDomain({ publicClient, registry, domain });
  console.info('********************* ensureIdentityWithAA: existing', existing);
  if (existing) return existing;

  console.log('********************* deploySmartAccountIfNeeded');
  await deploySmartAccountIfNeeded({ bundlerUrl, chain, account: agentAccount });
  const agentAddress = await agentAccount.getAddress();
  const data = encodeNewAgent(domain.trim().toLowerCase(), agentAddress as `0x${string}`);
  await sendSponsoredUserOperation({ bundlerUrl, chain, account: agentAccount, calls: [{ to: registry, data, value: 0n }] });
  console.info("*************** getAgentByDomain ********");
  const updated = await getAgentByDomain({ publicClient, registry, domain });
  console.log('********************* ensureIdentityWithAA: updated', updated);
  return (updated ?? agentAddress) as `0x${string}`;
}

// -------------------- Reputation Registry (acceptFeedback) via Delegation Toolkit --------------------

export const reputationRegistryAbi = [
  {
    type: 'function',
    name: 'acceptFeedback',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'agentClientId', type: 'uint256' },
      { name: 'agentServerId', type: 'uint256' },
    ],
    outputs: [],
  },
  {
    type: 'function',
    name: 'getFeedbackAuthId',
    stateMutability: 'view',
    inputs: [
      { name: 'clientAgentId', type: 'uint256' },
      { name: 'serverAgentId', type: 'uint256' },
    ],
    outputs: [
      { name: 'feedbackAuthId', type: 'bytes32' },
    ],
  },
] as const;

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

export async function acceptFeedbackWithDelegation(params: {
  agentClientId: bigint;
  agentServerId: bigint;
  agentAccount?: any; // Smart account configured for the session key
}): Promise<`0x${string}`> {
  const { agentClientId, agentServerId, agentAccount } = params;
  const sp = buildDelegationSetup();
  const senderAA = (sp.sessionAA || sp.aa) as `0x${string}`;

  // Encode the target contract call
  const callData = encodeFunctionData({
    abi: reputationRegistryAbi,
    functionName: 'acceptFeedback',
    args: [agentClientId, agentServerId],
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
}

export async function getFeedbackAuthId(params: {
  clientAgentId: bigint;
  serverAgentId: bigint;
  publicClient?: PublicClient;
  reputationRegistry?: `0x${string}`;
}): Promise<string | null> {
  const { clientAgentId, serverAgentId } = params;
  
  const sp = buildDelegationSetup();
  
  // Use a more reliable Sepolia RPC URL

  const publicClient = createPublicClient({
    chain: sepolia,
    transport: http(process.env.RPC_URL),
  });

  let reputationRegistry = params.reputationRegistry;
  
  if (!reputationRegistry) {
    reputationRegistry = reputationRegistry || sp.reputationRegistry as `0x${string}`;
  }

  try {
    console.info('ERC-8004: getFeedbackAuthId(client=%s, server=%s)', clientAgentId.toString(), serverAgentId.toString());
    console.info('ERC-8004: reputationRegistry', reputationRegistry);

    
    // Test RPC connection first
    console.info('ERC-8004: Testing RPC connection...');
    const blockNumber = await publicClient.getBlockNumber();
    console.info('ERC-8004: RPC connection OK, block number:', blockNumber.toString());
    
    // Add timeout to prevent hanging
    const timeoutPromise = new Promise<never>((_, reject) => {
      setTimeout(() => reject(new Error('Contract call timeout after 10 seconds')), 10000);
    });
    
    const contractPromise = publicClient.readContract({
      address: reputationRegistry,
      abi: reputationRegistryAbi,
      functionName: 'getFeedbackAuthId',
      args: [clientAgentId, serverAgentId],
    }) as Promise<`0x${string}`>;
    
    console.info('ERC-8004: Making contract call...');
    const result = await Promise.race([contractPromise, timeoutPromise]);

    if (!result || result === '0x0000000000000000000000000000000000000000000000000000000000000000') {
      console.info('ERC-8004: getFeedbackAuthId -> null (zero result)');
      return null;
    }

    console.info('ERC-8004: getFeedbackAuthId -> %s', result);
    return result;
  } catch (error: any) {
    console.info('ERC-8004: get_feedback_auth_id view failed: %s', error?.message || error);
    return null;
  }
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
  const agentId = params.agentId || BigInt(process.env.AGENT_CLIENT_ID || '1');
  const domain = params.domain || process.env.AGENT_DOMAIN || 'movieassistant.localhost:3001';
  
  try {
    console.info('ERC-8004: addFeedback(agentId=%s, domain=%s, rating=%s)', agentId.toString(), domain, rating);
    
    // Get chain ID from environment or default to Sepolia
    const chainId = process.env.ERC8004_CHAIN_ID || '11155111';
    
    let finalFeedbackAuthId = feedbackAuthId;
    
    // Try to get feedback auth ID if not provided
    if (!finalFeedbackAuthId) {
      console.info('ERC-8004: No feedback auth ID provided, attempting to retrieve from contract...');
      
      try {
        // Get client and server agent IDs from environment or use defaults
        const clientAgentId = BigInt(process.env.AGENT_CLIENT_ID || '1');
        const serverAgentId = BigInt(process.env.AGENT_SERVER_ID || '4');
        
        console.info('ERC-8004: Attempting to get feedback auth ID for client=%s, server=%s', clientAgentId.toString(), serverAgentId.toString());
        
        // Call the actual getFeedbackAuthId function
        console.info('ERC-8004: Calling getFeedbackAuthId...');
        const authId = await getFeedbackAuthId({
          clientAgentId,
          serverAgentId
        });
        
        console.info('ERC-8004: getFeedbackAuthId returned:', authId, '(type:', typeof authId, ')');
        
        if (authId && authId !== '0x0000000000000000000000000000000000000000000000000000000000000000') {
          finalFeedbackAuthId = authId;
          console.info('ERC-8004: Retrieved valid feedback auth ID:', finalFeedbackAuthId);
        } else {
          // Fallback to CAIP-10 format if no auth ID found
          const clientAddress = '0x0000000000000000000000000000000000000000'; // Placeholder
          finalFeedbackAuthId = `eip155:${chainId}:${clientAddress}`;
          console.info('ERC-8004: No valid auth ID found (got:', authId, '), using fallback:', finalFeedbackAuthId);
        }
      } catch (error: any) {
        console.error('ERC-8004: Exception in getFeedbackAuthId:', error);
        console.info('ERC-8004: Failed to get feedback auth ID:', error?.message || error);
        // Fallback to CAIP-10 format
        const clientAddress = '0x0000000000000000000000000000000000000000';
        finalFeedbackAuthId = `eip155:${chainId}:${clientAddress}`;
        console.info('ERC-8004: Using fallback feedback auth ID:', finalFeedbackAuthId);
      }
    } else {
      console.info('ERC-8004: Using provided feedback auth ID:', finalFeedbackAuthId);
    }
    
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


