# Contributing

Thank you for your interest in contributing to Doctor Appointment Watcher.

## Project Goal

Doctor Appointment Watcher monitors supported doctor appointment pages and sends a Telegram notification when an available appointment is detected.

The project is intentionally simple and currently uses Python's standard library.

## Before You Start

1. Fork the repository.
2. Clone your fork.
3. Create a dedicated branch for your change.
4. Test the watcher locally before submitting a pull request.

## Adding Support for a New Website

The project is designed to support multiple appointment websites over time.

When adding a new website:

1. Keep the existing Doctoreto implementation working.
2. Do not break the current user setup flow.
3. Detect the website automatically from the doctor URL.
4. Keep website-specific request and parsing logic separated.
5. Reuse the existing monitoring and Telegram notification logic.
6. Do not require users to understand website-specific API details.
7. Never hard-code doctor IDs, Telegram tokens, chat IDs, or private user data.
8. Document the supported website and known limitations.

### Website Adapter Principle

A new website should provide only the logic required to:

- validate and normalize the doctor URL,
- identify the doctor,
- retrieve appointment information,
- normalize available appointments into the common internal format.

The rest of the application should remain shared.

## Code Style

- Use Python 3.
- Prefer the standard library.
- Keep functions small and focused.
- Use meaningful names and type hints.
- Handle network failures gracefully.
- Never expose secrets in logs or source code.
- Preserve UTF-8 and Persian text support.

## Security

Never commit:

- Telegram bot tokens
- Private Telegram chat IDs
- Cookies or session credentials
- API keys
- Personal information
- Local configuration or state files

If a secret is accidentally committed, revoke or rotate it immediately.

## Pull Requests

A pull request should include:

- A clear description of the change.
- The website being added or modified.
- How the change was tested.
- Known limitations.
- Screenshots or example output when useful.

Keep pull requests focused and avoid unrelated refactoring.

## Commit Messages

Use concise commit messages such as:

- feat: add support for example.com
- fix: handle changed appointment response
- docs: improve contributor guide
- refactor: simplify appointment parsing

## Testing Checklist

Before submitting:

- [ ] Existing Doctoreto support still works.
- [ ] Doctor URL validation works.
- [ ] Appointment detection works.
- [ ] Telegram notification works.
- [ ] No credentials are included in the commit.
- [ ] Network errors are handled gracefully.
- [ ] Persian text remains readable.
- [ ] README is updated when necessary.

## Reporting Issues

When reporting an issue, provide:

- Website name and doctor URL.
- Expected behavior.
- Actual behavior.
- Relevant terminal output.
- Python version.
- Device/OS information when relevant.

Never include Telegram tokens, cookies, passwords, or other secrets.

## License

By contributing, you agree that your contribution can be distributed under the project's license.
