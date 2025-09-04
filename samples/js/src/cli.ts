#!/usr/bin/env node

import readline from "node:readline";
import crypto from "node:crypto";

import {
  // Specific Params/Payload types used by the CLI
  MessageSendParams, // Changed from TaskSendParams
  TaskStatusUpdateEvent,
  TaskArtifactUpdateEvent,
  Message,
  Task, // Added for direct Task events
  // Other types needed for message/part handling
  TaskState,
  FilePart,
  DataPart,
  // Type for the agent card
  AgentCard,
  Part, // Added for explicit Part typing
} from "@a2a-js/sdk";
import { A2AClient } from "@a2a-js/sdk/client";
import { getFeedbackAuthId, acceptFeedbackWithDelegation, addFeedback } from "./agents/movie-agent/agentAdapter.js";
import { getFeedbackDatabase } from "./agents/movie-agent/feedbackStorage.js";

// --- ANSI Colors ---
const colors = {
  reset: "\x1b[0m",
  bright: "\x1b[1m",
  dim: "\x1b[2m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
  gray: "\x1b[90m",
};

// --- Helper Functions ---
function colorize(color: keyof typeof colors, text: string): string {
  return `${colors[color]}${text}${colors.reset}`;
}

function generateId(): string { // Renamed for more general use
  return crypto.randomUUID();
}

// --- State ---
let currentTaskId: string | undefined = undefined; // Initialize as undefined
let currentContextId: string | undefined = undefined; // Initialize as undefined
const serverUrl = process.argv[2] || "http://localhost:41241"; // Agent's base URL
const client = new A2AClient(serverUrl);
let agentName = "Agent"; // Default, try to get from agent card later

// --- Readline Setup ---
const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  prompt: colorize("cyan", "You: "),
});

// --- Response Handling ---
// Function now accepts the unwrapped event payload directly
function printAgentEvent(
  event: TaskStatusUpdateEvent | TaskArtifactUpdateEvent
) {
  const timestamp = new Date().toLocaleTimeString();
  const prefix = colorize("magenta", `\n${agentName} [${timestamp}]:`);

  // Check if it's a TaskStatusUpdateEvent
  if (event.kind === "status-update") {
    const update = event as TaskStatusUpdateEvent; // Cast for type safety
    const state = update.status.state;
    let stateEmoji = "❓";
    let stateColor: keyof typeof colors = "yellow";

    switch (state) {
      case "working":
        stateEmoji = "⏳";
        stateColor = "blue";
        break;
      case "input-required":
        stateEmoji = "🤔";
        stateColor = "yellow";
        break;
      case "completed":
        stateEmoji = "✅";
        stateColor = "green";
        break;
      case "canceled":
        stateEmoji = "⏹️";
        stateColor = "gray";
        break;
      case "failed":
        stateEmoji = "❌";
        stateColor = "red";
        break;
      default:
        stateEmoji = "ℹ️"; // For other states like submitted, rejected etc.
        stateColor = "dim";
        break;
    }

    console.log(
      `${prefix} ${stateEmoji} Status: ${colorize(stateColor, state)} (Task: ${update.taskId}, Context: ${update.contextId}) ${update.final ? colorize("bright", "[FINAL]") : ""}`
    );

    if (update.status.message) {
      printMessageContent(update.status.message);
    }
  }
  // Check if it's a TaskArtifactUpdateEvent
  else if (event.kind === "artifact-update") {
    const update = event as TaskArtifactUpdateEvent; // Cast for type safety
    console.log(
      `${prefix} 📄 Artifact Received: ${update.artifact.name || "(unnamed)"
      } (ID: ${update.artifact.artifactId}, Task: ${update.taskId}, Context: ${update.contextId})`
    );
    // Create a temporary message-like structure to reuse printMessageContent
    printMessageContent({
      messageId: generateId(), // Dummy messageId
      kind: "message", // Dummy kind
      role: "agent", // Assuming artifact parts are from agent
      parts: update.artifact.parts,
      taskId: update.taskId,
      contextId: update.contextId,
    });
  } else {
    // This case should ideally not be reached if called correctly
    console.log(
      prefix,
      colorize("yellow", "Received unknown event type in printAgentEvent:"),
      event
    );
  }
}

function printMessageContent(message: Message) {
  message.parts.forEach((part: Part, index: number) => { // Added explicit Part type
    const partPrefix = colorize("red", `  Part ${index + 1}:`);
    if (part.kind === "text") { // Check kind property
      console.log(`${partPrefix} ${colorize("green", "📝 Text:")}`, part.text);
    } else if (part.kind === "file") { // Check kind property
      const filePart = part as FilePart;
      console.log(
        `${partPrefix} ${colorize("blue", "📄 File:")} Name: ${filePart.file.name || "N/A"
        }, Type: ${filePart.file.mimeType || "N/A"}, Source: ${("bytes" in filePart.file) ? "Inline (bytes)" : filePart.file.uri
        }`
      );
    } else if (part.kind === "data") { // Check kind property
      const dataPart = part as DataPart;
      console.log(
        `${partPrefix} ${colorize("yellow", "📊 Data:")}`,
        JSON.stringify(dataPart.data, null, 2)
      );
    } else {
      console.log(`${partPrefix} ${colorize("yellow", "Unsupported part kind:")}`, part);
    }
  });
}

// --- Agent Card Fetching ---
async function fetchAndDisplayAgentCard() {
  // Use the client's getAgentCard method.
  // The client was initialized with serverUrl, which is the agent's base URL.
  console.log(
    colorize("dim", `Attempting to fetch agent card from agent at: ${serverUrl}`)
  );
  try {
    // client.getAgentCard() uses the agentBaseUrl provided during client construction
    const card: AgentCard = await client.getAgentCard();
    agentName = card.name || "Agent"; // Update global agent name
    console.log(colorize("green", `✓ Agent Card Found:`));
    console.log(`  Name:        ${colorize("bright", agentName)}`);
    if (card.description) {
      console.log(`  Description: ${card.description}`);
    }
    console.log(`  Version:     ${card.version || "N/A"}`);
    if (card.capabilities?.streaming) {
      console.log(`  Streaming:   ${colorize("green", "Supported")}`);
    } else {
      console.log(`  Streaming:   ${colorize("yellow", "Not Supported (or not specified)")}`);
    }
    // Update prompt prefix to use the fetched name
    // The prompt is set dynamically before each rl.prompt() call in the main loop
    // to reflect the current agentName if it changes (though unlikely after initial fetch).
  } catch (error: any) {
    console.log(
      colorize("yellow", `⚠️ Error fetching or parsing agent card`)
    );
    throw error;
  }
}

// --- Main Loop ---
async function main() {
  console.log(colorize("bright", `A2A Terminal Client`));
  console.log(colorize("dim", `Agent Base URL: ${serverUrl}`));

  await fetchAndDisplayAgentCard(); // Fetch the card before starting the loop

  console.log(colorize("dim", `No active task or context initially. Use '/new' to start a fresh session or send a message.`));
  console.log(
    colorize("green", `Enter messages, or use '/new' to start a new session. '/exit' to quit.`)
  );
  console.log(
    colorize("cyan", `Available commands:`)
  );
  console.log(
    colorize("cyan", `  /new - Start a new session`)
  );
  console.log(
    colorize("cyan", `  /exit - Quit the CLI`)
  );
  console.log(
    colorize("cyan", `  /feedback-auth <clientId> <serverId> - Get feedback auth ID`)
  );
  console.log(
    colorize("cyan", `  /accept-feedback <clientId> <serverId> - Accept feedback via delegation`)
  );
  console.log(
    colorize("cyan", `  /add-feedback <rating> <comment> [agentId] [domain] - Add feedback record`)
  );
  console.log(
    colorize("cyan", `  /list-feedback - List all feedback records`)
  );
  console.log(
    colorize("cyan", `  /feedback-stats - Show feedback statistics`)
  );
  console.log(
    colorize("cyan", `  /test-feedback-endpoint [port] - Test the feedback JSON endpoint`)
  );

  rl.setPrompt(colorize("cyan", `${agentName} > You: `)); // Set initial prompt
  rl.prompt();

  rl.on("line", async (line) => {
    const input = line.trim();
    rl.setPrompt(colorize("cyan", `${agentName} > You: `)); // Ensure prompt reflects current agentName

    if (!input) {
      rl.prompt();
      return;
    }

    if (input.toLowerCase() === "/new") {
      currentTaskId = undefined;
      currentContextId = undefined; // Reset contextId on /new
      console.log(
        colorize("bright", `✨ Starting new session. Task and Context IDs are cleared.`)
      );
      rl.prompt();
      return;
    }

    if (input.toLowerCase() === "/exit") {
      rl.close();
      return;
    }

    // Handle /feedback-auth command
    if (input.toLowerCase().startsWith("/feedback-auth")) {
      const parts = input.split(" ");
      if (parts.length !== 3) {
        console.log(colorize("red", "Usage: /feedback-auth <clientAgentId> <serverAgentId>"));
        rl.prompt();
        return;
      }
      
      try {
        const clientAgentId = BigInt(parts[1]);
        const serverAgentId = BigInt(parts[2]);
        
        console.log(colorize("yellow", `Getting feedback auth ID for client=${clientAgentId}, server=${serverAgentId}...`));
        
        const feedbackAuthId = await getFeedbackAuthId({
          clientAgentId,
          serverAgentId
        });
        
        if (feedbackAuthId) {
          console.log(colorize("green", `Feedback Auth ID: ${feedbackAuthId}`));
        } else {
          console.log(colorize("yellow", "No feedback auth ID found (null or zero result)"));
        }
      } catch (error: any) {
        console.log(colorize("red", `Error: ${error?.message || error}`));
      }
      
      rl.prompt();
      return;
    }

    // Handle /accept-feedback command
    if (input.toLowerCase().startsWith("/accept-feedback")) {
      const parts = input.split(" ");
      if (parts.length !== 3) {
        console.log(colorize("red", "Usage: /accept-feedback <clientAgentId> <serverAgentId>"));
        rl.prompt();
        return;
      }
      
      try {
        const clientAgentId = BigInt(parts[1]);
        const serverAgentId = BigInt(parts[2]);
        
        console.log(colorize("yellow", `Accepting feedback for client=${clientAgentId}, server=${serverAgentId}...`));
        
        const userOpHash = await acceptFeedbackWithDelegation({
          agentClientId: clientAgentId,
          agentServerId: serverAgentId
        });
        
        console.log(colorize("green", `Feedback accepted! UserOp Hash: ${userOpHash}`));
      } catch (error: any) {
        console.log(colorize("red", `Error: ${error?.message || error}`));
      }
      
      rl.prompt();
      return;
    }

    // Handle /add-feedback command
    if (input.toLowerCase().startsWith("/add-feedback")) {
      const parts = input.split(" ");
      if (parts.length < 3) {
        console.log(colorize("red", "Usage: /add-feedback <rating> <comment> [agentId] [domain]"));
        console.log(colorize("red", "  rating: 1-5 scale"));
        console.log(colorize("red", "  agentId and domain are optional (use env vars or defaults)"));
        rl.prompt();
        return;
      }
      
      try {
        const rating = parseInt(parts[1]);
        const comment = parts.slice(2).join(" ");
        
        if (rating < 1 || rating > 5) {
          console.log(colorize("red", "Rating must be between 1 and 5"));
          rl.prompt();
          return;
        }
        
        // Optional parameters
        const agentId = parts.length > 3 ? BigInt(parts[2]) : undefined;
        const domain = parts.length > 4 ? parts[3] : undefined;
        
        console.log(colorize("yellow", `Adding feedback with rating=${rating}...`));
        if (agentId) console.log(colorize("yellow", `  Agent ID: ${agentId}`));
        if (domain) console.log(colorize("yellow", `  Domain: ${domain}`));
        
        const result = await addFeedback({
          rating,
          comment,
          ...(agentId && { agentId }),
          ...(domain && { domain })
        });
        
        if (result.status === 'ok') {
          console.log(colorize("green", `Feedback added successfully! ID: ${result.feedbackId}`));
          console.log(colorize("green", `  Agent ID: ${result.agentId}`));
          console.log(colorize("green", `  Domain: ${result.domain}`));
        } else {
          console.log(colorize("red", `Failed to add feedback: ${result.status}`));
        }
      } catch (error: any) {
        console.log(colorize("red", `Error: ${error?.message || error}`));
      }
      
      rl.prompt();
      return;
    }

    // Handle /list-feedback command
    if (input.toLowerCase().startsWith("/list-feedback")) {
      try {
        const feedbackDb = getFeedbackDatabase();
        const allFeedback = await feedbackDb.getAllFeedback();
        
        if (allFeedback.length === 0) {
          console.log(colorize("yellow", "No feedback records found"));
        } else {
          console.log(colorize("cyan", `Found ${allFeedback.length} feedback records:`));
          allFeedback.forEach((record, index) => {
            console.log(colorize("bright", `${index + 1}. ID: ${record.id}`));
            console.log(`   Domain: ${record.domain}`);
            console.log(`   Rating: ${record.rating}% (${record.rating / 20}/5 stars)`);
            console.log(`   Notes: ${record.notes}`);
            console.log(`   Created: ${record.createdAt}`);
            console.log(`   Auth ID: ${record.feedbackAuthId}`);
            console.log("");
          });
        }
      } catch (error: any) {
        console.log(colorize("red", `Error: ${error?.message || error}`));
      }
      
      rl.prompt();
      return;
    }

    // Handle /feedback-stats command
    if (input.toLowerCase().startsWith("/feedback-stats")) {
      try {
        const feedbackDb = getFeedbackDatabase();
        const stats = await feedbackDb.getFeedbackStats();
        
        console.log(colorize("cyan", "Feedback Statistics:"));
        console.log(`Total feedback records: ${stats.total}`);
        console.log(`Average rating: ${stats.averageRating.toFixed(1)}% (${(stats.averageRating / 20).toFixed(1)}/5 stars)`);
        
        if (Object.keys(stats.byDomain).length > 0) {
          console.log(colorize("bright", "\nBy Domain:"));
          Object.entries(stats.byDomain).forEach(([domain, count]) => {
            console.log(`  ${domain}: ${count} records`);
          });
        }
        
        if (Object.keys(stats.byRating).length > 0) {
          console.log(colorize("bright", "\nBy Rating:"));
          Object.entries(stats.byRating).forEach(([rating, count]) => {
            const stars = parseInt(rating) / 20;
            console.log(`  ${stars}/5 stars (${rating}%): ${count} records`);
          });
        }
      } catch (error: any) {
        console.log(colorize("red", `Error: ${error?.message || error}`));
      }
      
      rl.prompt();
      return;
    }

    // Handle /test-feedback-endpoint command
    if (input.toLowerCase().startsWith("/test-feedback-endpoint")) {
      try {
        const parts = input.split(" ");
        const port = parts[1] || '41241';
        const endpoint = `http://localhost:${port}/.well-known/feedback.json`;
        
        console.log(colorize("yellow", `Testing feedback endpoint: ${endpoint}`));
        
        const response = await fetch(endpoint);
        const data = await response.json();
        
        if (response.ok) {
          console.log(colorize("green", `✅ Endpoint working! Found ${data.length} feedback records`));
          if (data.length > 0) {
            console.log(colorize("cyan", "Sample record:"));
            console.log(JSON.stringify(data[0], null, 2));
          }
        } else {
          console.log(colorize("red", `❌ Endpoint error: ${response.status} ${response.statusText}`));
        }
      } catch (error: any) {
        console.log(colorize("red", `❌ Error testing endpoint: ${error?.message || error}`));
      }
      
      rl.prompt();
      return;
    }

    // Construct params for sendMessageStream
    const messageId = generateId(); // Generate a unique message ID

    const messagePayload: Message = {
      messageId: messageId,
      kind: "message", // Required by Message interface
      role: "user",
      parts: [
        {
          kind: "text", // Required by TextPart interface
          text: input,
        },
      ],
    };

    // Conditionally add taskId to the message payload
    if (currentTaskId) {
      messagePayload.taskId = currentTaskId;
    }
    // Conditionally add contextId to the message payload
    if (currentContextId) {
      messagePayload.contextId = currentContextId;
    }


    const params: MessageSendParams = {
      message: messagePayload,
      // Optional: configuration for streaming, blocking, etc.
      // configuration: {
      //   acceptedOutputModes: ['text/plain', 'application/json'], // Example
      //   blocking: false // Default for streaming is usually non-blocking
      // }
    };

    try {
      console.log(colorize("red", "Sending message..."));
      // Use sendMessageStream
      const stream = client.sendMessageStream(params);

      // Iterate over the events from the stream
      for await (const event of stream) {
        const timestamp = new Date().toLocaleTimeString(); // Get fresh timestamp for each event
        const prefix = colorize("magenta", `\n${agentName} [${timestamp}]:`);

        if (event.kind === "status-update" || event.kind === "artifact-update") {
          const typedEvent = event as TaskStatusUpdateEvent | TaskArtifactUpdateEvent;
          printAgentEvent(typedEvent);

          // If the event is a TaskStatusUpdateEvent and it's final, reset currentTaskId
          if (typedEvent.kind === "status-update" && (typedEvent as TaskStatusUpdateEvent).final && (typedEvent as TaskStatusUpdateEvent).status.state !== "input-required") {
            console.log(colorize("yellow", `   Task ${typedEvent.taskId} is final. Clearing current task ID.`));
            currentTaskId = undefined;
            // Optionally, you might want to clear currentContextId as well if a task ending implies context ending.
            // currentContextId = undefined; 
            // console.log(colorize("dim", `   Context ID also cleared as task is final.`));
          }

        } else if (event.kind === "message") {
          const msg = event as Message;
          console.log(`${prefix} ${colorize("green", "✉️ Message Stream Event:")}`);
          printMessageContent(msg);
          if (msg.taskId && msg.taskId !== currentTaskId) {
            console.log(colorize("dim", `   Task ID context updated to ${msg.taskId} based on message event.`));
            currentTaskId = msg.taskId;
          }
          if (msg.contextId && msg.contextId !== currentContextId) {
            console.log(colorize("dim", `   Context ID updated to ${msg.contextId} based on message event.`));
            currentContextId = msg.contextId;
          }
        } else if (event.kind === "task") {
          const task = event as Task;
          console.log(`${prefix} ${colorize("blue", "ℹ️ Task Stream Event:")} ID: ${task.id}, Context: ${task.contextId}, Status: ${task.status.state}`);
          if (task.id !== currentTaskId) {
            console.log(colorize("dim", `   Task ID updated from ${currentTaskId || 'N/A'} to ${task.id}`));
            currentTaskId = task.id;
          }
          if (task.contextId && task.contextId !== currentContextId) {
            console.log(colorize("dim", `   Context ID updated from ${currentContextId || 'N/A'} to ${task.contextId}`));
            currentContextId = task.contextId;
          }
          if (task.status.message) {
            console.log(colorize("gray", "   Task includes message:"));
            printMessageContent(task.status.message);
          }
          if (task.artifacts && task.artifacts.length > 0) {
            console.log(colorize("gray", `   Task includes ${task.artifacts.length} artifact(s).`));
          }
        } else {
          console.log(prefix, colorize("yellow", "Received unknown event structure from stream:"), event);
        }
      }
      console.log(colorize("dim", `--- End of response stream for this input ---`));
    } catch (error: any) {
      const timestamp = new Date().toLocaleTimeString();
      const prefix = colorize("red", `\n${agentName} [${timestamp}] ERROR:`);
      console.error(
        prefix,
        `Error communicating with agent:`,
        error.message || error
      );
      if (error.code) {
        console.error(colorize("gray", `   Code: ${error.code}`));
      }
      if (error.data) {
        console.error(
          colorize("gray", `   Data: ${JSON.stringify(error.data)}`)
        );
      }
      if (!(error.code || error.data) && error.stack) {
        console.error(colorize("gray", error.stack.split('\n').slice(1, 3).join('\n')));
      }
    } finally {
      rl.prompt();
    }
  }).on("close", () => {
    console.log(colorize("yellow", "\nExiting A2A Terminal Client. Goodbye!"));
    process.exit(0);
  });
}

// --- Start ---
main().catch(err => {
  console.error(colorize("red", "Unhandled error in main:"), err);
  process.exit(1);
});
