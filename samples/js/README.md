# JavaScript Samples

The provided samples are built using [Genkit](https://genkit.dev/) using the Gemini API.

## Agents

- [Movie Agent](src/agents/movie-agent/README.md): Uses TMDB API to search for movie information and answer questions.
- [Coder Agent](src/agents/coder/README.md): Generates full code files as artifacts.
- [Content Editor Agent](src/agents/content-editor/README.md)  
    Sample agent to proof-read and polish content. To make use of this agent in a content creation multi-agent system, check out the [content_creation](../python/hosts/content_creation/README.md) sample.

## Custom Domain Setup

The Movie Agent and Web Client use custom localhost domains for testing. To use these domains, you need to add them to your system's hosts file:

### Linux/macOS:
```bash
echo "127.0.0.1 movieagent.localhost" | sudo tee -a /etc/hosts
echo "127.0.0.1 movieclient.localhost" | sudo tee -a /etc/hosts
```

### Windows:
Add these lines to `C:\Windows\System32\drivers\etc\hosts`:
```
127.0.0.1 movieagent.localhost
127.0.0.1 movieclient.localhost
```

### Verify the setup:
```bash
ping movieagent.localhost
ping movieclient.localhost
```

Both should resolve to `127.0.0.1`.

## Testing the Agents

First, follow the instructions in the agent's README file, then run `npx tsx ./cli.ts` to start up a command-line client to talk to the agents. Example:

1. Navigate to the samples/js directory:
    ```bash
    cd samples/js
    ```
2. Run npm install:
    ```bash
    npm install
    ```
3. Run an agent:
```bash
export GEMINI_API_KEY=<your_api_key>
npm run agents:coder

# in a separate terminal
npm run a2a:cli
```

## Note

This is sample code and not production-quality libraries.

## Disclaimer
Important: The sample code provided is for demonstration purposes and illustrates the
mechanics of the Agent-to-Agent (A2A) protocol. When building production applications,
it is critical to treat any agent operating outside of your direct control as a
potentially untrusted entity.

All data received from an external agent—including but not limited to its AgentCard,
messages, artifacts, and task statuses—should be handled as untrusted input. For
example, a malicious agent could provide an AgentCard containing crafted data in its
fields (e.g., description, name, skills.description). If this data is used without
sanitization to construct prompts for a Large Language Model (LLM), it could expose
your application to prompt injection attacks.  Failure to properly validate and
sanitize this data before use can introduce security vulnerabilities into your
application.

Developers are responsible for implementing appropriate security measures, such as
input validation and secure handling of credentials to protect their systems and users.



echo "127.0.0.1 movieagent.localhost" | sudo tee -a /etc/hosts
