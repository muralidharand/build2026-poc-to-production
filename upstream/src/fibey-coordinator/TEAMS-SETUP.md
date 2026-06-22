# Microsoft Teams (Activity Protocol passthrough) — setup guide

> **Optional, advanced feature.** The coordinator runs fully without Teams. This
> guide adds Microsoft Teams so the agent can DM a human operator a proactive
> approval card and chat back in a Teams thread. If you just want to try the
> agent, skip this file.

This adds **Microsoft Teams** to `fibey-coordinator` via a **second protocol**,
`activity_protocol`, declared alongside `responses` in `agent.yaml`. It uses the
**activity passthrough** (the raw Bot Framework Activity is forwarded to the
container at `POST /api/messages`) — **not** the responses↔activity mapper.

The agent's **Entra Agent ID** is the bot identity; the platform provisions the
bot whose messaging endpoint is
`…/agents/fibey-coordinator/endpoint/protocols/activityProtocol`.
No separate Azure Bot resource or bot app secret is hand-authored.

## What's implemented (code)

| File | Role |
|------|------|
| `agent.yaml` / `agent.manifest.yaml` | Declare `activity_protocol` (v1.0.0) in addition to `responses`. |
| `teams_bot.py` | `POST /api/messages` handler (mounted on the agentserver port). Reactive chat (streamed), Approve/Reject submit → durable gate, and the proactive `send_approval_card`. |
| `teams_connector.py` | Bot Connector client. Mints an **agent-identity** token (audience `https://api.botframework.com`), `createConversation`, send/reply, and Teams **streaming** helpers (informative → token chunks → final) with the AI label, feedback buttons, and citations. |
| `teams_cards.py` | Adaptive Cards: approval (Approve/Reject) + decision result. |
| `teams_state.py` | Durable, **identity-keyed** conversation store (Azure Blob, with local-file fallback). Cross-session so the heartbeat can reach the operator. |
| `main.py` | Mounts `/api/messages`; `request_approval` also fires `send_approval_card` so the heartbeat pokes Teams. |

The proactive send is reached **through the existing `request_approval` tool** — so
the heartbeat routine's approval step naturally pushes the card.

## One-time enablement

1. **Deploy the agent with both protocols:**
   ```bash
   azd deploy fibey-coordinator
   ```
   The `activity_protocol` entry makes the endpoint take the passthrough branch.

2. **Enable the Microsoft Teams channel** for the agent in the Microsoft Foundry
   portal (Agent → Channels → Microsoft Teams). This provisions the bot bound to
   the agent's Entra Agent ID and sets the `BotServiceTenant` auth scheme. The
   messaging endpoint will be the `…/protocols/activityProtocol` URL.

3. **Confirm the agent's managed identity can mint a Bot token.** The container
   calls `credential.get_token("https://api.botframework.com/.default")`. The
   agent identity (set `AZURE_CLIENT_ID` for a user-assigned identity, else
   `DefaultAzureCredential`) must be allowed the `api.botframework.com` audience
   via the agentic-identity flow the platform configures.

4. **Install the Teams app** for the operator who should receive approval cards
   (portal "Add to Teams", or admin/Graph install). The operator needs a
   Microsoft Teams license and permission to upload custom apps.

5. **Configure the durable conversation store (cross-session):** set
   `TEAMS_STATE_BLOB_URL` to a Blob **container URL** the agent identity can
   read/write (for example `https://<account>.blob.core.windows.net/teams-state`)
   and grant the identity **Storage Blob Data Contributor**. Without this, the
   local-file fallback is used and the heartbeat (a different session) cannot
   find the operator.

   Relevant env vars (in `agent.yaml`):
   - `APPROVER_UPN` = the operator's UPN (for example `operator@your-tenant`)
   - `TEAMS_STATE_BLOB_URL` = the blob container URL

## Seed the store

In Teams, **DM the agent "hi"** once after install. The inbound activity is
captured into the durable store (UPN → serviceUrl / tenantId / botId /
conversationId), which is what the proactive heartbeat card needs.

## Example flow

1. **Trigger the network heartbeat routine.** The agent investigates a site and
   calls `request_approval` → a proactive **adaptive card with an Approve button**
   arrives in the operator's Teams DM.
2. **Click Approve.** The submit posts back `{kind: approval_decision,
   approval_id, decision: approve}` → `durable_manager.approve(...)` raises the
   durable `HumanApproval` event → dispatch proceeds → a decision card replaces
   the prompt.
3. **Keep chatting in Teams:** "Show me your active incidents." The reply streams
   live — informative updates mapped from tool calls, token-by-token text, then a
   final message with the **AI-generated** label, **thumbs up/down** feedback, and
   inline **citations**.

## Notes / limits

- **Single-operator default:** `/api/messages` auth is relaxed (the platform
  validates the caller before forwarding). Add Bot Framework JWT validation for
  multi-user scenarios.
- **Streaming** is one stream at a time (Teams streaming UX).
- **Proactive addressing** uses the stored `conversationId` fast-path, else
  `createConversation` from `{aadObjectId, tenantId, botId, serviceUrl}` — so it
  works from a fresh heartbeat session without a captured message id.
- This surface is **preview**; portal steps may shift toward GA.
