# Kula ChatGPT Connector

Load this folder as an unpacked extension in the browser/profile where you are signed in to `https://chatgpt.com`.

1. In Kula APP, open the automation connection and copy its pairing key.
2. Open this extension's popup, paste the key, and click **Connect to Kula APP**.
3. Keep this browser signed in. Start and Stop are controlled from Kula APP.

For each answer request, the connector opens a separate automation conversation, attaches that job's PDF resume, and asks ChatGPT for JSON answers grounded in the resume and saved profile. This sends that resume, profile, and application questions to your ChatGPT account. It does not use an API key or private ChatGPT endpoints.

The automation tab is managed by the connector. Do not type in it during a run. Previous automation tabs may be closed when they have no unsent draft. Existing user conversations are not used as answer sources.

Login prompts, CAPTCHA/browser checks, limits, missing attachments, unsupported page layouts, unanswerable questions, and malformed answers produce an attention/error result. The connector does not bypass those conditions or invent missing facts. Desktop Stop prevents subsequent authorized browser steps; messages already sent to ChatGPT cannot be unsent.

The desktop connection is fixed to `127.0.0.1:43127`, uses a locally saved pairing key, and can be disabled with **Disconnect**. The connector only has access to ChatGPT and the local desktop connection. ChatGPT's changing web UI still needs live verification with your account; local fixtures do not establish support for every future ChatGPT layout.
