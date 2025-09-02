# Remote agent built by LangGraph

## Getting started

1. Create a `.env` file with:
   - `OPENAI_API_KEY` (required)
   - `OPENAI_MODEL` (optional, defaults to `gpt-4o-mini`)
   - ERC-8004 (optional unless you want identity/feedback integration):
     - `ERC8004_ENABLED=true`
     - `ERC8004_RPC_URL=<https RPC endpoint>`
     - `ERC8004_IDENTITY_REGISTRY=<identity registry address>`
     - `ERC8004_REPUTATION_REGISTRY=<reputation registry address>`
     - Finder server key: `ERC8004_PRIVATE_KEY_FINDER=<hex key>`
     - Reserve server key: `ERC8004_PRIVATE_KEY_RESERVE=<hex key>`
     - Assistant key (only for read ops or signatures): `ERC8004_PRIVATE_KEY_ASSISTANT=<hex key>`
     - Domains (used to resolve IDs):
       - `FINDER_DOMAIN=finder.localhost:10002`
       - `RESERVE_DOMAIN=reserve.localhost:10002`
       - `ASSISTANT_DOMAIN=assistant.localhost:8083`
     - Optional gas tuning:
       - `ERC8004_GAS_MULT=1.5`
       - `ERC8004_MIN_GAS=500000`
       - `ERC8004_GAS_PRICE_MULT=1.2` or `ERC8004_GAS_PRICE_GWEI=5`

2. Start the server

    ```bash
    # Finder variant (search)
    uv run . -- --port 10002 --variant finder

    # Reserve variant
    uv run . -- --port 10012 --variant reserve
    ```

## Disclaimer

Important: The sample code provided is for demonstration purposes and illustrates the mechanics of the Agent-to-Agent (A2A) protocol. When building production applications, it is critical to treat any agent operating outside of your direct control as a potentially untrusted entity.

All data received from an external agent—including but not limited to its AgentCard, messages, artifacts, and task statuses—should be handled as untrusted input. For example, a malicious agent could provide an AgentCard containing crafted data in its fields (e.g., description, name, skills.description). If this data is used without sanitization to construct prompts for a Large Language Model (LLM), it could expose your application to prompt injection attacks.  Failure to properly validate and sanitize this data before use can introduce security vulnerabilities into your application.

Developers are responsible for implementing appropriate security measures, such as input validation and secure handling of credentials to protect their systems and users.
