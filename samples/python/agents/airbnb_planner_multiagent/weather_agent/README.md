# Weather Agent (OpenAI-based)

This example shows how to create an A2A Server that uses an OpenAI-backed agent with tool/function calling to query weather information through built-in tools defined in `weather_mcp.py`.

## Environment

Create a `.env` file using `example.env` as a template and set:

- `OPENAI_API_KEY`: Your OpenAI API key
- `OPENAI_MODEL` (optional): Defaults to `gpt-4o-mini`

## Run

```bash
uv run .
```
