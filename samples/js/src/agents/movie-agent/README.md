# Movie Info Agent

This agent uses the TMDB API to answer questions about movies. To run with a .env file:

```bash
cp ../../.env.example ../../.env # or create your own .env
echo "OPENAI_API_KEY=sk-..." >> ../../.env
echo "TMDB_API_KEY=..." >> ../../.env # v3 key
# or use a v4 token instead of TMDB_API_KEY
# echo "TMDB_API_TOKEN=ey..." >> ../../.env

echo "127.0.0.1 movieagent.localhost" | sudo tee -a /etc/hosts
npm run agents:movie-agent
```

The agent will start on `http://localhost:41241`.
