# Recipient Resolution — Staged Integration

## Current state

User Service is not ready, so Notification Service must not depend on a live User Service endpoint yet.

## Recommended abstraction

Use an interface boundary that fits the existing codebase, such as:

```java
public interface RecipientProvider {
    Optional<NotificationRecipient> findByUserId(String userId);
}
```

This is illustrative. Match existing naming and return/error conventions.

Possible implementations:
- `ConfiguredTestRecipientProvider`: development-only test recipients loaded from local configuration.
- `UserServiceRecipientProvider`: future production adapter once User Service API is ready.

Do not build both implementations mechanically if the repository already has an equivalent abstraction.

## Current development behavior

- Use test user IDs and test email addresses only.
- Select recipients using stable IDs in the event or a configured test mapping.
- If only an existing Knock recipient ID is needed for a web-only test, avoid an unnecessary email lookup.
- For email tests, ensure the test recipient has an email property in Knock.
- Keep test recipient fixtures out of production configuration.
- Never fall back silently from a failed production User Service call to a hardcoded email.

## Future User Service integration checklist

Before implementing:
- [ ] User Service API is available in a development environment.
- [ ] Endpoint and response DTO are confirmed by its owner.
- [ ] Service-to-service authentication is agreed.
- [ ] Timeout and error behavior are agreed.
- [ ] User-not-found behavior is agreed.
- [ ] User ID mapping to Knock recipient ID is confirmed.
- [ ] Email source and verification rules are confirmed.

Then implement and test the adapter without changing the event contract or Knock orchestration unnecessarily.
