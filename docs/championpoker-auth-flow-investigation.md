# ChampionPoker authentication flow — investigation notes

Date: 2026-11-25
Scope: isolated iPoker parser test branch only. No production changes.

## Confirmed from HAR and integration JavaScript

- Client loads `https://login.championpoker.com/jswrapper/integration.js.php?casino=championpoker`.
- Integration configuration identifies casino `championpoker`, service type `GamePlay`, default system ID `5`, mobile client platform.
- Public function `iapiLoginAndGetTempToken(user, pass, realMode, language, systemId, serviceType, rememberMeLogin, inheritance)` constructs a request to `LoginAndGetTempToken.php`.
- For the standard username/password login type, it submits `username`, `password`, and `loginType`; it can also include challenge-response and device/risk fields such as iovation blackbox. Other login modes include nickname/password, email/password, external token, and session token.
- The integration script also contains `iapiLoginExternalTokenAndGetTempToken` and email-login variants.
- A separate request seen in the HAR, `POST GetTemporaryAuthenticationToken.php`, was for an already-authenticated browser session. It required existing web-session `userId` and `token`; this is not the initial login operation.
- The WebSocket bootstrap observed in DevTools uses message `classId:60108` with `usernameOrNickname` and `tempToken`. The response `classId:60109` contains the poker session token, session ID, currency, and error code.

## What this establishes

The integration script exposes a first-party username/password-to-temporary-token login flow. This is a plausible path for a backend worker to obtain a fresh temporary token without copying browser cookies. However, the HAR did not capture a fresh `LoginAndGetTempToken.php` exchange, its exact response shape, challenge/MFA behavior, or the exact mapping between returned token fields and WebSocket `tempToken`. These must be validated before implementation.

The default integration configuration says system ID 5, while the authenticated-session HAR request used system ID 18. Do not hardcode either value without confirming which is correct for the ChampionPoker real-money GamePlay login.

## Safe next implementation sequence

1. In an isolated test only, inspect the integration's `iapiAddClientHintsAndContinue` and callback handling to document headers, form encoding, client hints, response parsing, and error mapping.
2. Determine whether the first-party login endpoint can be called directly with username/password and what it returns. Do not run repeated password guesses or bypass CAPTCHA, device checks, MFA, or anti-bot challenges.
3. If the endpoint requires a challenge or interactive MFA, stop and design a supported user-assisted authorization step; do not attempt to defeat it.
4. Once a clean login exchange is verified, implement a separate backend auth module that keeps credentials in Railway secrets, requests a short-lived temp token, and redacts all secrets from logs.
5. Then implement the Socket.IO login using the fresh temp token, validate successful `60109`, and subscribe to tournament data.
6. Add token expiry/refresh, reconnect/backoff, and health reporting. Only then enable a scheduled worker in the isolated Railway environment.

## Security

- Never commit or log credentials, `userId`, web-session tokens, `tempToken`, poker session tokens, cookies, or raw HAR bodies.
- Tokens previously pasted into chat must be considered exposed and revoked by ending that session.
- Use only the user's own authorized account. Do not reuse browser cookies or impersonate a different client.
- Do not deploy any of this to production without explicit approval.

## Current status

- WebSocket endpoint and Engine.IO v3 handshake: confirmed.
- Poker WebSocket login message structure: confirmed.
- First-party integration function for username/password login and temporary-token request construction: confirmed from downloaded client JS.
- Exact login endpoint response and MFA/challenge requirements: not yet verified.
- Fully unattended Railway login: not yet proven.
