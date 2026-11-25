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


## Additional client transport details (verified in integration.js)

- `iapiAddClientHintsAndContinue` checks whether DeviceAtlas is enabled. If not enabled, it sends the request immediately. If enabled, it waits briefly for browser client hints and DeviceAtlas properties; on timeout it still proceeds without them. This means these fields are conditional, not universally mandatory.
- `iapiMakeRedirectRequest` selects a configured login server from `iapiConf.loginServer`, appends casino context parameters, and adds a `redirectUrl` carrying a request ID. It creates a hidden iframe and transfers a message containing `requestType`, `postParams`, URL, casino, request ID, and login-server origin to `pasSetupPage.php`.
- The iframe transport uses `postMessage` when supported; otherwise the integration falls back to a form POST into the iframe. This is not a simple JSON REST call.
- The login request's POST fields are assembled according to the configured authentication type, with `loginType` always added. Username/password is only the default branch; nickname, email, OTP, external-token, and session-token modes have different field names.
- The wrapper adds context query parameters when configured, including client type/platform/version/skin, channel, device metadata, and language. The actual values are installation/runtime configuration, not all hardcoded constants.
- The HAR contains no `LoginAndGetTempToken.php` request or response. It therefore cannot establish the endpoint's successful response payload or error semantics. The observed `GetTemporaryAuthenticationToken.php` request remains a separate already-authenticated-session operation.

## Updated assessment

A backend implementation would need to reproduce a browser-oriented iframe/postMessage transport contract, or establish from a controlled, authorized test that the login server accepts the equivalent direct form POST. The client code alone does not prove that a raw backend POST is supported. Do not infer that the presence of a public JavaScript function guarantees a headless login will succeed.


## Tournament catalogue WebSocket protocol (verified from the captured session)

The HAR contains a complete authenticated Socket.IO session and the first bulk tournament catalogue exchange. The following protocol facts were extracted from message structure; account/session values are intentionally omitted.

### Lobby bootstrap and subscription

- After the Engine.IO / Socket.IO handshake, the client sends lobby initialization (classId 50001) and receives lobby/session configuration (50003), server time-zone information (50026), and other lobby metadata.
- The client later sends classId 50017, the observed tournament-container subscription request:
  - subscriptionType: 1
  - containerType: 1
  - subContainerType: 4
  - fun: false
  - filter expression: @game_type@ in (1, 2, 18, 6, 3, 16, 19, 13, 5, 14, 20, 21) and @start_type@=1
- The server acknowledges this subscription with classId 50018, errorCode 0.
- The first classId 40001 response arrived with 8 records, followed by a bulk response with 1,314 records. Additional 40001 messages carried smaller subsequent batches/updates. Across the captured HAR, 1,541 tournament records were present before deduplication.
- The tournament list is not nested directly as a flat record. Each item has containerId and tournamentGrid. The tournamentGrid object carries the actual schedule attributes.
- The client sends classId 50088 lobby-content requests for LCT_HOME and LCT_TWISTER; these are separate lobby widgets/content requests and are not the main tournament catalogue subscription.

### Tournament record fields observed

The tournamentGrid union includes (among others): name, nameTranslationsList, gameType, startDate, originalStartDate, registrationStartDate, registrationEndDate, endDate, buyIn, fee, buyInFeeJackpotFee, cur, guaranteedPrize, prizePool, prizeType, status, startType, tournamentType, mode, speedMode, speedGameplay, tableSize, maxPlayers, registeredPlayers, totalPlayers, lateRegistration, lateRegistrationStatus, reEntryEnabled, rebuyAddonAllowed, maxBuyinsCount, knockOutFee, knockOutProgressionFactor, mysteryBountyTournament, isBountyInTournamentMoney, presumableDuration, levelDuration, startStack, tagList, promoTags, and gridImageUrl. Optional fields vary by tournament; do not assume every record has every key.

### Implications for the parser

- The existing frame decoder's classId 40001 extraction is aligned with the actual catalogue stream.
- A live worker should authenticate, complete lobby bootstrap, send the 50017 subscription, wait for the 50018 acknowledgement, and consume every 40001 batch/update. It should upsert by containerId, not assume the first response is the entire catalogue.
- Keep the subscription filter configurable. The captured filter requests multiple game types and scheduled-start tournaments; it is evidence of this client session's request, not proof that it is the only valid filter for every lobby.
- Handle Socket.IO heartbeat/ping traffic and reconnects separately from tournament data. The HAR contains frequent application heartbeat messages; do not treat them as catalogue updates.
- The HAR demonstrates the authenticated browser's subscription protocol, but it does not establish that a backend can obtain a valid login token unattended. Authentication remains a separate unverified dependency as described above.

## Updated progress

- Tournament subscription request (50017) and successful acknowledgement (50018): confirmed.
- Bulk tournament list response (40001) and nested record shape (containerId + tournamentGrid): confirmed.
- Bulk response observed: 1,314 records; total records across captured frames: 1,541 before deduplication.
- Full field union from the captured records: documented above.
- A clean server-side authentication exchange and standalone Railway WebSocket worker: not yet implemented or verified.


### Observed field types and normalization notes

In the initial 1,314-record bulk batch, startDate, originalStartDate, registrationStartDate, registrationEndDate, and lrStopTime were integer values; buyIn, fee, buyInFeeJackpotFee, guaranteedPrize, knockOutFee, and knockOutProgressionFactor were numeric; cur was a string; speedMode and lateRegistration were booleans. The HAR's tournament timestamps are integer epoch values in milliseconds (the client data shape and magnitudes indicate millisecond timestamps); normalize them to UTC ISO timestamps while preserving the original integer values. Several fields are optional and their presence differs between batches and tournament types.

The presence of knockOutFee and knockOutProgressionFactor alone does not prove that a tournament is PKO. Preserve knockout metadata separately and leave progressive-format classification unknown unless the source exposes an explicit, validated format indicator.
