// Plaid Link in the browser: connect, reconnect (update mode), and OAuth resume.
//
// Flow: ask our server for a link token -> open Plaid Link -> on success,
// send the public token back to our server. The server holds all secrets;
// this page only ever sees short-lived link/public tokens.
//
// OAuth banks redirect the whole page away and back to /oauth-return. To
// resume, the in-progress session is kept in sessionStorage (cleared when
// the tab closes, and as soon as Link finishes or exits).
(function () {
  "use strict";

  const STORAGE_KEY = "fintrack.linkSession";

  function showError(message) {
    const el = document.getElementById("link-error");
    if (el) {
      el.textContent = message;
      el.hidden = false;
    }
  }

  async function postJSON(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  // session: { session_id, link_token, purpose: "create"|"update", connected: [...], allowDuplicate }
  function openLink(session, receivedRedirectUri) {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));

    const handler = Plaid.create({
      token: session.link_token,
      receivedRedirectUri: receivedRedirectUri, // only set when resuming after OAuth

      onSuccess: async function (publicToken, metadata) {
        sessionStorage.removeItem(STORAGE_KEY);
        try {
          // The server compares institution + accounts with existing Items and
          // refuses duplicates BEFORE exchanging the token (no Item is created).
          const result = await postJSON("/link/complete", {
            session_id: session.session_id,
            purpose: session.purpose,
            public_token: publicToken,
            institution_id: metadata.institution ? metadata.institution.institution_id : null,
            accounts: (metadata.accounts || []).map((a) => ({ name: a.name, mask: a.mask })),
            allow_duplicate: !!session.allowDuplicate,
          });
          window.location.href = result.redirect;
        } catch (err) {
          showError(err.message);
        }
      },

      onExit: function (err) {
        sessionStorage.removeItem(STORAGE_KEY);
        if (err) showError(err.display_message || err.error_message || err.error_code);
      },

      // Early-exit duplicate guard. Plaid doesn't guarantee onEvent timing (in
      // testing it fired too late), so the real check is server-side in /link/complete.
      onEvent: function (eventName, metadata) {
        if (
          eventName === "SELECT_INSTITUTION" &&
          session.purpose === "create" &&
          !session.allowDuplicate &&
          (session.connected || []).includes(metadata.institution_id)
        ) {
          handler.exit({ force: true });
          showError(
            `${metadata.institution_name} is already connected. Use Reconnect on the Accounts page ` +
              `instead of creating another Item.`
          );
        }
      },
    });
    handler.open();
  }

  // --- Connect page: new Item ---
  const connectForm = document.getElementById("connect-form");
  if (connectForm) {
    connectForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      const form = new FormData(connectForm);
      try {
        const session = await postJSON("/link/token/create", {
          kind: form.get("kind"),
          consent: form.get("consent") || "",
        });
        openLink({
          ...session,
          purpose: "create",
          connected: JSON.parse(connectForm.dataset.connected || "[]"),
          allowDuplicate: form.get("allow_duplicate") === "on",
        });
      } catch (err) {
        showError(err.message);
      }
    });
  }

  // --- Reconnect / "Add or remove accounts" buttons (update mode: never a new Item) ---
  document.querySelectorAll("[data-link-update]").forEach(function (button) {
    button.addEventListener("click", async function () {
      try {
        const query = button.dataset.accountSelect ? "?accounts=1" : "";
        const session = await postJSON(`/items/${button.dataset.itemId}/link-token${query}`);
        openLink({ ...session, purpose: "update" });
      } catch (err) {
        showError(err.message);
      }
    });
  });

  // --- OAuth return page: reopen the same Link session ---
  if (document.getElementById("oauth-resume")) {
    const saved = sessionStorage.getItem(STORAGE_KEY);
    if (saved) {
      openLink(JSON.parse(saved), window.location.href);
    } else {
      showError("This Link session has expired or was opened in another tab. Start again from Accounts.");
    }
  }
})();
