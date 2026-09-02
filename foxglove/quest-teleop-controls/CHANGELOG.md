# Changelog

## 1.0.3

- Treat a command as successful only when the backend returns both
  `accepted=true` and `applied=true`.
- Serialize service requests, confirm destructive discard, and clarify compact
  operator feedback.
- Refresh compatible build tooling and keep all dependencies pinned.

## 1.0.2

- Add the compact Safety, Episode, and Recording control groups.
- Surface backend acknowledgement and errors without a raw JSON panel.
- Disable concurrent commands while a request is in flight.
