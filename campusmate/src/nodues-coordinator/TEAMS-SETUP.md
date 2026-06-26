# Teams Integration Setup

The No-Dues Coordinator can optionally send Adaptive Card notifications to a
Microsoft Teams approver when a new no-dues certificate request is raised.

## Prerequisites

1. An Azure Bot registration (Bot Framework)
2. A Teams app manifest (available in the `teams/` directory)
3. The bot added to the approver's Teams chat or channel

## Environment variables

Set these in your `.env` (or as container environment variables):

```bash
TEAMS_APP_ID=<your-bot-app-id>
TEAMS_APP_PASSWORD=<your-bot-app-password>
TEAMS_TENANT_ID=<your-azure-ad-tenant-id>
TEAMS_SERVICE_URL=https://smba.trafficmanager.net/apis/
TEAMS_APPROVER_CONVERSATION_ID=<conversation-id-of-the-approver>
APPROVER_UPN=approver@yourtenant.onmicrosoft.com
```

## How it works

1. When `request_approval()` is called, the coordinator sends an Adaptive Card
   to the approver's Teams conversation.
2. The card shows the student's name, action requested, and a link to the
   approval portal (`/approvals/<id>`).
3. The approver clicks **Open Approval Page** in the card and approves or rejects
   in the browser UI.
4. The durable orchestration resumes and the coordinator can issue the certificate.

## Without Teams

If the Teams environment variables are not set, the coordinator works in
**web-only mode**: all approvals go through the web UI at
`http://localhost:8090/approvals` (or the configured `APPROVAL_SERVER_URL`).
