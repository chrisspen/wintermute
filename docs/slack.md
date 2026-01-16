# Slack Integration

Wintermute supports bidirectional Slack integration for agent sessions. Messages from Slack are routed to agent sessions, and agent responses are broadcast back to Slack channels.

## Slack App Setup

### 1. Create a Slack App

1. Go to https://api.slack.com/apps
2. Click **Create New App** ’ **From scratch**
3. Name your app (e.g., "Wintermute") and select your workspace

### 2. Enable Socket Mode

Socket Mode allows Wintermute to receive events via WebSocket instead of HTTP webhooks.

1. Go to **Socket Mode** in the left sidebar
2. Toggle **Enable Socket Mode** to ON
3. Create an **App-Level Token** with `connections:write` scope
4. Save the token (starts with `xapp-`) - this is your `app_token`

### 3. Configure Bot Token Scopes

Go to **OAuth & Permissions** ’ **Scopes** ’ **Bot Token Scopes** and add:

**Required scopes:**
- `channels:history` - View messages in public channels
- `channels:read` - View basic channel info
- `channels:join` - Join public channels
- `chat:write` - Send messages
- `chat:write.public` - Send messages to channels without joining

**For private channels:**
- `groups:history` - View messages in private channels
- `groups:read` - View basic private channel info
- `groups:write` - Manage private channels

**For direct messages:**
- `im:history` - View direct messages
- `im:read` - View basic DM info
- `im:write` - Send direct messages

**For user info (display names instead of IDs):**
- `users:read` - View user profiles

### 4. Subscribe to Bot Events

**Important:** With Socket Mode enabled, configure events in the **Socket Mode** section, NOT in Event Subscriptions (which requires a Request URL).

Go to **Socket Mode** ’ **Event Subscriptions** (or **Event Subscriptions** with Socket Mode enabled) and add:

- `message.channels` - Messages in public channels
- `message.groups` - Messages in private channels
- `message.im` - Direct messages (optional)

### 5. Install the App

1. Go to **OAuth & Permissions**
2. Click **Install to Workspace**
3. Authorize the app
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`) - this is your `bot_token`

### 6. Invite Bot to Channels

The bot must be in channels to receive messages:
```
/invite @YourBotName
```

## Wintermute Configuration

### Store Credentials

In Wintermute admin UI, go to **Credentials** and create two entries:

1. **Provider:** `slack`, **Name:** `bot_token`, **Reference:** `xoxb-...`
2. **Provider:** `slack`, **Name:** `app_token`, **Reference:** `xapp-...`

### Enable Slack Source

Go to **Task Sources** and ensure the `slack` source is enabled.

### Configure Agent Channels

For bidirectional messaging with agent sessions:

1. Go to **Agents** ’ Edit your agent
2. In the **Channels** section, add a Slack channel
3. Set **Type:** `slack`
4. Set **External Channel ID:** The Slack channel ID (e.g., `C0A9GCQ2ETT`)
   - Find this in Slack: right-click channel ’ View channel details ’ scroll to bottom

## How It Works

### Slack ’ Wintermute

1. User posts message in Slack channel linked to an agent
2. Slack sends event via Socket Mode WebSocket
3. `SlackSource` receives event and creates a work item
4. Message is stored as a comment and queued for the agent session
5. Session source dispatches queued message to the running agent

### Wintermute ’ Slack

1. Agent produces output (comments, responses)
2. Session source detects response and calls `ChatDispatcher`
3. `ChatDispatcher` broadcasts to all enabled channels for the agent
4. `SlackAdapter` sends message via Slack Web API

### Web UI ’ Slack

1. User sends message via web UI
2. Message is stored as comment with user's username
3. Message is relayed to Slack with `[username]` prefix

## Troubleshooting

### Messages not received from Slack

1. Check Socket Mode is enabled in Slack app settings
2. Verify bot events are subscribed (`message.channels`, etc.)
3. Ensure bot is invited to the channel
4. Check `external_channel_id` matches the Slack channel ID
5. Verify credentials are stored correctly in Wintermute
6. Check supervisor logs for Slack connection status

### Messages not sent to Slack

1. Verify `bot_token` has `chat:write` scope
2. Check channel is enabled in agent's channel list
3. Ensure `external_channel_id` is set correctly
4. Check supervisor logs for dispatch errors

### User shows as ID instead of name

Add `users:read` scope to your Slack app and reinstall to workspace.

### Socket disconnects frequently

This is normal - Slack periodically rotates connections. The supervisor automatically reconnects.

## Channel ID Reference

To find a Slack channel ID:
1. In Slack, right-click the channel name
2. Select "View channel details"
3. Scroll to bottom - the ID starts with `C` (public) or `G` (private)

Or use the Slack API:
```bash
curl -H "Authorization: Bearer xoxb-..." https://slack.com/api/conversations.list
```
