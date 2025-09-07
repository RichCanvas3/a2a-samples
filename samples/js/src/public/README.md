# Movie Agent Feedback

A web-based interface for interacting with the Movie Agent and managing feedback data.

## Features

### 🎬 Movie Agent Integration
- **Agent Status Check**: Monitor connection to the movie agent
- **Feedback Auth Testing**: Test feedback authentication with agent IDs
- **Real-time Status**: Live connection status updates

### 📝 Feedback Management
- **Add Feedback**: Submit feedback with 1-5 star ratings
- **View Feedback**: Browse all feedback records with details
- **Statistics**: View comprehensive feedback analytics
- **Export**: Access feedback data via JSON API

### 🔗 API Endpoints

#### Public Endpoints
- `GET /.well-known/feedback.json` - Feedback data in standard format
- `GET /` - Web interface

#### API Endpoints
- `GET /api/feedback` - Get all feedback records
- `POST /api/feedback` - Add new feedback
- `GET /api/feedback/stats` - Get feedback statistics
- `GET /api/feedback-auth/:clientId/:serverId` - Get feedback auth ID
- `POST /api/feedback/accept` - Accept feedback via delegation
- `GET /api/movie-agent/status` - Check movie agent connection

## Usage

### Starting the Web Client
```bash
npm run web-client
```

The web client will start on `http://localhost:3000` (or the port specified in `WEB_CLIENT_PORT` environment variable).

### Environment Variables
```bash
# Web client port (default: 3000)
WEB_CLIENT_PORT=3000

# Movie agent URL (default: http://localhost:41241)
MOVIE_AGENT_URL=http://localhost:41241

# Agent configuration
AGENT_DOMAIN=movieclient.localhost:12345
AGENT_CLIENT_ID=12
AGENT_SERVER_ID=11

# Blockchain configuration
ERC8004_CHAIN_ID=11155111
RPC_URL=https://rpc.sepolia.org
BUNDLER_URL=https://api.pimlico.io/v2/11155111/rpc?apikey=your_key
```

### Adding Feedback
1. Open the web interface at `http://localhost:3000`
2. Select a rating (1-5 stars)
3. Enter your comment
4. Optionally specify agent ID and domain
5. Click "Add Feedback"

### Viewing Feedback
- **Recent Feedback**: Automatically displayed on the main page
- **Statistics**: Click "View Statistics" for analytics
- **JSON Export**: Access `/.well-known/feedback.json` for programmatic access

### Testing Movie Agent
1. Ensure the movie agent is running (`npm run agents:movie-agent`)
2. Click "Check Agent Status" to verify connection
3. Use "Test Feedback Auth" to test authentication

## Data Format

### Feedback Record Structure
```json
{
  "id": 1,
  "feedbackAuthId": "eip155:11155111:0x...",
  "agentSkillId": "finder:v1",
  "taskId": "task-123",
  "contextId": "context-456",
  "rating": 80,
  "domain": "movieclient.localhost:12345",
  "notes": "Great movie recommendations!",
  "proofOfPayment": "0x...",
  "createdAt": "2024-01-01T00:00:00.000Z"
}
```

### Statistics Response
```json
{
  "total": 10,
  "averageRating": 75.5,
  "byDomain": {
    "movieclient.localhost:12345": 8,
    "other.domain.com": 2
  },
  "byRating": {
    "60": 2,
    "80": 5,
    "100": 3
  }
}
```

## Integration

### For Other Applications
The web client provides a standard feedback endpoint at `/.well-known/feedback.json` that other applications can consume:

```javascript
const response = await fetch('http://localhost:3000/.well-known/feedback.json');
const feedbackData = await response.json();
```

### For Development
The web client serves as both a user interface and a development tool for testing the movie agent integration and feedback system.
