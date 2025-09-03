declare module 'permissionless/clients/pimlico' {
  export function createPimlicoClient(...args: any[]): any;
}

declare module 'permissionless/utils' {
  export function encodeNonce(params: { key: bigint; sequence: bigint }): `0x${string}`;
}

declare module '@metamask/delegation-framework' {
  export const SINGLE_DEFAULT_MODE: number;
  export const DelegationFramework: {
    encode: {
      redeemDelegations(input: {
        delegations: any[];
        modes: any[];
        executions: Array<{ target: `0x${string}`; value?: bigint; callData: `0x${string}` }[]>;
      }): `0x${string}`;
    }
  };
}


