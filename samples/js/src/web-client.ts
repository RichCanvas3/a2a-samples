#!/usr/bin/env node

import "dotenv/config";
import express from "express";
import { fileURLToPath } from 'url';
import path from 'path';
import fs from 'fs';
import { getFeedbackDatabase } from "./agents/movie-agent/feedbackStorage.js";
import { getFeedbackAuthId, acceptFeedbackWithDelegation, addFeedback } from "./agents/movie-agent/agentAdapter.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.WEB_CLIENT_PORT || 3001;
const HOST = process.env.HOST || 'movieassistant.localhost';

// Middleware
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// CORS middleware
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept, Authorization');
  if (req.method === 'OPTIONS') {
    res.sendStatus(200);
  } else {
    next();
  }
});

// Serve the main HTML page
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Get default agent IDs from environment variables
app.get('/api/config/agent-ids', (req, res) => {
  res.json({
    clientId: process.env.AGENT_CLIENT_ID || '1',
    serverId: process.env.AGENT_SERVER_ID || '4'
  });
});

// Agent card endpoint
app.get('/.well-known/agent-card.json', (req, res) => {
  try {
    const agentCardPath = path.join(__dirname, 'web-client-agent-card.json');
    const agentCard = JSON.parse(fs.readFileSync(agentCardPath, 'utf8'));
    res.json(agentCard);
  } catch (error) {
    console.error('Error serving agent card:', error);
    res.status(500).json({ error: 'Failed to load agent card' });
  }
});

// Feedback endpoint
app.get('/.well-known/feedback.json', (req, res) => {
  try {
    const feedbackDb = getFeedbackDatabase();
    const records = feedbackDb.getAllFeedback();
    
    // Convert database records to the format expected by other apps
    const feedbackRecords = records.map(record => ({
      FeedbackAuthID: record.feedbackAuthId,
      AgentSkillId: record.agentSkillId,
      TaskId: record.taskId,
      contextId: record.contextId,
      Rating: record.rating,
      Domain: record.domain,
      Data: { notes: record.notes },
      ...(record.proofOfPayment && { ProofOfPayment: { txHash: record.proofOfPayment } })
    }));
    
    res.json(feedbackRecords);
  } catch (error: any) {
    console.error('[WebClient] Error serving feedback.json:', error?.message || error);
    res.json([]);
  }
});

// API endpoints for the web interface

// Get feedback statistics
app.get('/api/feedback/stats', (req, res) => {
  try {
    const feedbackDb = getFeedbackDatabase();
    const stats = feedbackDb.getFeedbackStats();
    res.json(stats);
  } catch (error: any) {
    console.error('[WebClient] Error getting feedback stats:', error?.message || error);
    res.status(500).json({ error: error?.message || 'Internal server error' });
  }
});

// Get all feedback
app.get('/api/feedback', (req, res) => {
  try {
    const feedbackDb = getFeedbackDatabase();
    const feedback = feedbackDb.getAllFeedback();
    res.json(feedback);
  } catch (error: any) {
    console.error('[WebClient] Error getting feedback:', error?.message || error);
    res.status(500).json({ error: error?.message || 'Internal server error' });
  }
});

// Add feedback
app.post('/api/feedback', async (req, res) => {
  try {
    const { rating, comment, agentId, domain, taskId, contextId, isReserve, proofOfPayment } = req.body;
    
    if (!rating || !comment) {
      return res.status(400).json({ error: 'Rating and comment are required' });
    }
    
    if (rating < 1 || rating > 5) {
      return res.status(400).json({ error: 'Rating must be between 1 and 5' });
    }
    
    const result = await addFeedback({
      rating: parseInt(rating),
      comment,
      ...(agentId && { agentId: BigInt(agentId) }),
      ...(domain && { domain }),
      ...(taskId && { taskId }),
      ...(contextId && { contextId }),
      ...(isReserve !== undefined && { isReserve }),
      ...(proofOfPayment && { proofOfPayment })
    });
    
    res.json(result);
  } catch (error: any) {
    console.error('[WebClient] Error adding feedback:', error?.message || error);
    res.status(500).json({ error: error?.message || 'Internal server error' });
  }
});

// Get feedback auth ID
app.get('/api/feedback-auth/:clientId/:serverId', async (req, res) => {
  try {
    const { clientId, serverId } = req.params;
    const feedbackAuthId = await getFeedbackAuthId({
      clientAgentId: BigInt(clientId),
      serverAgentId: BigInt(serverId)
    });
    
    res.json({ feedbackAuthId });
  } catch (error: any) {
    console.error('[WebClient] Error getting feedback auth ID:', error?.message || error);
    res.status(500).json({ error: error?.message || 'Internal server error' });
  }
});

// Accept feedback via delegation
app.post('/api/feedback/accept', async (req, res) => {
  try {
    const { clientAgentId, agentServerId } = req.body;
    
    if (!clientAgentId || !agentServerId) {
      return res.status(400).json({ error: 'clientAgentId and agentServerId are required' });
    }
    
    const userOpHash = await acceptFeedbackWithDelegation({
      agentClientId: BigInt(clientAgentId),
      agentServerId: BigInt(agentServerId)
    });
    
    res.json({ userOpHash });
  } catch (error: any) {
    console.error('[WebClient] Error accepting feedback:', error?.message || error);
    res.status(500).json({ error: error?.message || 'Internal server error' });
  }
});

// Test movie agent connection
app.get('/api/movie-agent/status', async (req, res) => {
  try {
    const movieAgentUrl = process.env.MOVIE_AGENT_URL || 'http://localhost:41241';
    const response = await fetch(`${movieAgentUrl}/.well-known/agent-card.json`);
    
    if (response.ok) {
      const agentCard = await response.json();
      res.json({ 
        status: 'connected', 
        agentCard,
        url: movieAgentUrl 
      });
    } else {
      res.json({ 
        status: 'disconnected', 
        error: `HTTP ${response.status}`,
        url: movieAgentUrl 
      });
    }
  } catch (error: any) {
    console.error('[WebClient] Error checking movie agent status:', error?.message || error);
    res.json({ 
      status: 'disconnected', 
      error: error?.message || 'Connection failed',
      url: process.env.MOVIE_AGENT_URL || 'http://localhost:41241'
    });
  }
});

// Start the server
app.listen(PORT, HOST, () => {
  console.log(`[WebClient] Server started on http://${HOST}:${PORT}`);
  console.log(`[WebClient] Feedback Endpoint: http://${HOST}:${PORT}/.well-known/feedback.json`);
  console.log(`[WebClient] Web Interface: http://${HOST}:${PORT}`);
  console.log(`[WebClient] API Documentation: http://${HOST}:${PORT}/api`);
});
