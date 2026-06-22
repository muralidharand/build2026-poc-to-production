# Microsoft Teams (Activity Protocol passthrough) — setup guide

> **Optional, advanced feature.** The coordinator runs fully without Teams. This
> guide adds Microsoft Teams so the agent can DM a human officer a proactive
> approval card and chat back in a Teams thread. If you just want to try the
> agent, skip this file.

This adds **Microsoft Teams** to `nodues-coordinator` via a **second protocol**,
`activity_protocol`, declared alongside `responses` in `agent.yaml`. It uses the
**activity passthrough** (the raw Bot Framework Activity is forwarded to the
container at `POST /api/messages`) — **not** the responses↔activity mapper.

The agent's **Entra Agent ID** is the bot identity; the platform provisions the
bot whose messaging endpoint is
`…/agents/nodues-coordinator/endpoint/protocols/activityProtocol`.
No separate Azure Bot resource or bot app secret is hand-authored.

## What's implemented (code)

| File | Role |
|------|------|
| `agent.yaml` / `agent.manifest.yaml` | Declare `activity_protocol` (v1.0.0) in addition to `responses`. |
| `teams_bot.py` | `POST /api/messages` handler (mounted on the agentserver port). Reactive chat (streamed), Approve/Reject submit → durable gate, and the proactive `send_approval_card`. |
| `teams_connector.py` | Bot Connector client. Mints an **agent-identity** token (audience `https://api.botframework.com`), `createConversation`, send/reply, and Teams **streaming** helpers (informative → token chunks → final) with the AI label, feedback buttons, and citations. |
| `teams_cards.py` | Adaptive Cards: approval (Approve/Reject) + decision result. |
| `teams_state.py` | Durable, **identity-keyed** conversation store (Azure Blob, with local-file fallback). Cross-session so the heartbeat can reach the officer. |
| `main.py` | Mounts `/api/messages`; `request_approval` also fires `send_approval_card` so the officer is notified via Teams. |

The proactive send is reached **through the existing `request_approval` tool** — so
the approval step naturally pushes the card to the officer's Teams DM.

## Demo flow

1. Student says "I need my no-dues certificate."
2. The agent checks dues → finds 1,500 rupees outstanding in hostel.
3. Agent calls `request_approval` → a proactive **adaptive card with an Approve button**
   arrives in the officer's Teams DM.
4. **Officer clicks Approve.** The submit posts back `{kind: approval_decision,
   approval_id, decision: approve}` → `durable_manager.approve(...)` raises the
   durable `HumanApproval` event → certificate is issued → a decision card replaces
   the prompt.
5. The student receives the certificate ID back in the original session.

## One-time enablement

1. **Deploy the agent with both protocols:**
   ```bash
   azd deploy nodues-coordinator
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

4. **Install the Teams app** for the officer who should receive approval cards
   (portal "Add to Teams", or admin/Graph install). The officer needs a
   Microsoft Teams license and permission to upload custom apps.

5. **Configure the durable conversation store (cross-session):** set
   `TEAMS_STATE_BLOB_URL` to a Blob **container URL** the agent identity can
   read/write (for example `https://<account>.blob.core.windows.net/teams-state`)
   and grant the identity **Storage Blob Data Contributor**. Without this, the
   local-file fallback is used and the heartbeat (a different session) cannot
   find the officer.

   Relevant env vars (in `agent.yaml`):
   - `APPROVER_UPN` = the officer's UPN (for example `officer@kit.edu`)
   - `TEAMS_STATE_BLOB_URL` = the blob container URL

## Seed the store

In Teams, **DM the agent "hi"** once after install. The inbound activity is
captured into the durable store (UPN → serviceUrl / tenantId / botId /
conversationId), which is what the proactive card needs.

## Notes / limits

- **Single-operator default:** `/api/messages` auth is relaxed (the platform
  validates the caller before forwarding). Add Bot Framework JWT validation for
  multi-user scenarios.
- **Streaming** is one stream at a time (Teams streaming UX).
- **Proactive addressing** uses the stored `conversationId` fast-path, else
  `createConversation` from `{aadObjectId, tenantId, botId, serviceUrl}` — so it
  works from a fresh session without a captured message id.
- This surface is **preview**; portal steps may shift toward GA.
