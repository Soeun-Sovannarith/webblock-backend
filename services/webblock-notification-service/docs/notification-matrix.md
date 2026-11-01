# Notification Matrix — from WebBlock Feature Detail

Source: `Feature detail.docx`, section 3.9, pages 14–17 (printed page labels 14–17).

The source specification lists Web, Mobile, and Email. This project pack scopes implementation to **Web + Email** because that is the user's current implementation scope. Mobile entries are shown as future scope and must not be accidentally enabled.

| Event | Recipient | Web | Email | Mobile in source spec |
|---|---|---:|---:|---:|
| Website generation completes | Website Owner | Yes | No | Yes |
| Website generation gets stuck | Admin | Yes | Yes | No |
| Deployment succeeds | Website Owner | Yes | No | Yes |
| Deployment fails | Website Owner | Yes | No | Yes |
| Deployment fails | Admin | Yes | No | No |
| Monthly payment is due | Website Owner | No | Yes | Yes |
| Website crashes during live traffic | Website Owner | No | Yes | Yes |
| Website crashes during live traffic | Admin | Yes | No | No |
| Website exceeds resource limits | Website Owner | No | Yes | Yes |
| Website exceeds resource limits | Admin | Yes | No | No |
| Platform service goes down or becomes unhealthy | Admin | Yes | Yes | No |
| A new subscription becomes Active after payment | Admin | Yes | No | No |
| A user's subscription expires | Admin | Yes | No | No |

## Product behavior requirements from the source

- Notifications are triggered automatically by system events.
- Recipients are selected by role and event context (owner, admin, or both when needed).
- The web interface uses a notification bell with an unread count.
- Recipients can open the notification list, mark one notification or all notifications as read, and click a notification to navigate to the relevant page.
- Owners receive web notifications before installing the mobile app.
- Admins can receive critical email alerts even when not signed in.
- Each notification links to the relevant page, such as generation details, deployment logs, monitoring, subscriptions, or user accounts.
- Read/unread status is maintained for web notifications.

## Implementation interpretation

- A row with Web=No and Email=Yes must not create an in-app feed message unless product owners change the requirement.
- A row with Web=Yes and Email=No must not send email.
- Owner and Admin are distinct recipient groups. For an event with both audiences, either trigger the same workflow for each recipient with recipient-specific data, or use a shared workflow with clear recipient-specific conditions. Do not assume that separate workflow keys are required.
- Mobile is intentionally omitted from current workflow configuration until mobile push is explicitly added.
