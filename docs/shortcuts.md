# The Side Control Shortcut

The [Side Control app](../README.md) can show a QR code that adds a ready-made Shortcut to an iPhone
or iPad (**Add to iPhone** in Settings → Share Clipboard, or the setup guide of a device). The
Shortcut talks to the computer over the network: it sends and gets the clipboard, and returns the
pointer to the computer. This page is the source description of that Shortcut.

Since iOS 15, the Shortcuts app only imports shortcuts signed by Apple, and signing needs a Mac or an
iPhone. So the Shortcut is built **once, on an iPhone**, by hand from the steps below, and shared as
an **iCloud link**. The app shows that link as the first QR code — every user installs the same
Shortcut from it. Only the computer's address and token are per-user, and the Shortcut learns them
from the second QR code on first run.

## What the Shortcut does

- **Send Clipboard to Computer** — the phone's clipboard goes to the computer's.
- **Get Clipboard from Computer** — the computer's clipboard arrives on the phone.
- **Return Pointer to Computer** — the cursor goes back to the PC (like Super+Esc).
- **Set Up Again** — scans the setup QR code again (new computer, new token, ...).
- Started from the **share sheet** with text or a URL selected, it sends that text right away.

What it cannot do: it only runs when it is started on the device (Action Button, Back Tap, Siri, the
share sheet, a widget or the Shortcuts app), never by itself — so text does not follow the cursor
automatically. Text only; no images or files. iOS asks once for **Local Network** access.

## Build it (once, in Shortcuts on the iPhone, iOS 17/18)

The actions are added by name: tap **+**, then search for the action in the field at the bottom and
tap it. Every "tap X" below is a tap on that action's button or field.

1. Open **Shortcuts**, tap **+** (top right).
2. Tap the name at the top → **Rename**, type `Side Control`.
3. Search **Get File** and add it. Tap **File Path** and enter `Side Control/setup.json` (inside the
   *Shortcuts* folder of iCloud Drive). Turn **Error If Not Found** off: on the first run there is no
   file yet.
4. Search **Get Dictionary from Input** and add it (it parses the JSON the file holds).
5. Search **Set Variable** and add it. Tap the variable name and type `Setup`.
6. Search **Get Dictionary Value** and add it. Tap **Key** and type `url`, leave the input as the
   dictionary (it offers `Setup`; if not, tap the input and pick `Setup`).
7. Add **Set Variable**, name it `Computer URL`.
8. Add another **Get Dictionary Value**, this time for the key `token`.
9. Add **Set Variable**, name it `Token`.
10. Search **If** and add it. Tap **Input** and pick `Computer URL`; tap the condition and choose
    **has no value**. (This is the first-run check: with no setup file yet, it is time to scan.)
11. Inside the If, add the **Set Up block** — the eight actions listed here. Build them here first;
    the menu item in step 22 gets a copy (long-press an action → **Duplicate**, drag the copy into
    the menu item):
    1. **Scan QR or Barcode** — this is what opens the camera for the second QR code of the app.
    2. **Get Dictionary from Input** (the QR holds `{"url": "...", "token": "..."}`).
    3. **Set Variable**, name it `Setup`.
    4. **Get Dictionary Value** for the key `url`.
    5. **Set Variable**, name it `Computer URL`.
    6. **Get Dictionary Value** for the key `token`.
    7. **Set Variable**, name it `Token`.
    8. **Save File**. Tap the input and pick `Setup`. Tap **Destination Path** and enter
       `Side Control/setup.json`. Turn **Overwrite If File Exists** on and **Ask Where to Save** off.
12. Add **End If** (it is added at the right place automatically when you pick it right after the
    last action of the If; otherwise drag it).
13. Search **If** and add it. Tap **Input** and pick **Shortcut Input**; the condition is
    **has any value**. (This is the share-sheet case: text selected somewhere else arrives here.)
14. Inside this If, add **Text** and insert **Shortcut Input** into it (tap the field → the variable
    bar above the keyboard → **Shortcut Input**). For a URL this makes it plain text.
15. Search **Get Contents of URL** and add it:
    - Tap **URL** and enter `Computer URL`, then type `/clipboard` right after it
      (`Computer URL/clipboard`).
    - Tap **Show More**. **Method**: **POST**.
    - **Headers**: tap **Add new item**, key `X-Side-Control-Token`, value: the variable `Token`.
    - **Request Body**: **File** (it has no plain-text choice; a text variable given as a file is
      sent as its raw text, which is what the computer reads), and pick the variable `Text` (from
      step 14).
16. Add **Show Notification**. Tap the text and type `Sent to the computer`. Then add **Stop This
    Shortcut**, so the share sheet does not go on to the menu below.
17. Add **End If**.
18. Search **Choose from Menu** and add it. Give it these four items (tap **Add new item** for each):
    `Send Clipboard to Computer`, `Get Clipboard from Computer`, `Return Pointer to Computer`,
    `Set Up Again`.
19. Under **Send Clipboard to Computer**, add:
    1. **Get Clipboard**.
    2. **Get Contents of URL** as in step 15 (URL `Computer URL/clipboard`, **POST**, the same
       header, **Request Body**: **File** → the variable `Clipboard`).
    3. **Show Notification** with `Sent to the computer`.
20. Under **Get Clipboard from Computer**, add:
    1. **Get Contents of URL**. Tap **URL**, enter `Computer URL/clipboard`. Tap **Show More** →
       **Headers** → **Add new item**: key `X-Side-Control-Token`, value `Token`. The method stays
       **GET**.
    2. **Copy to Clipboard** (its input is the answer).
    3. **Show Notification** with `Copied to the clipboard`.
21. Under **Return Pointer to Computer**, add **Get Contents of URL**: URL `Computer URL/return`,
    **Show More** → **Method** **POST** → **Headers**: `X-Side-Control-Token` = `Token`.
22. Under **Set Up Again**, add the same eight actions as in the Set Up block of step 11 (the
    duplicated copy).
23. Tap the shortcut's name at the top → **Details**.
24. Turn **Show in Share Sheet** on. Tap **Share Sheet Types**, keep only **Text** and **URLs**.
25. Tap **Done**, then **X** to save the shortcut.

## Set it up on the device

1. In Side Control, turn on **Settings → Share Clipboard** and click **Add to iPhone** (or open the
   setup guide of a device). Two QR codes appear.
2. On the iPhone, open the **Camera** app, point it at the **first** code and tap **Add Shortcut**.
3. Run **Side Control** once from the Shortcuts app: it notices it has no setup and opens the camera —
   point it at the **second** code. Tap **Allow** when iOS asks for Local Network access.
4. Run it again: the menu appears. **Get Clipboard from Computer** is a quick check that both sides
   talk.

Start it however suits you: the **Action Button**, **Back Tap** (Settings → Accessibility → Touch →
Back Tap), **Siri** ("Hey Siri, Side Control"), a **widget**, or the **share sheet**.

## Publish it

1. On the iPhone, open the shortcut in **Shortcuts**, tap **Share** (or the name at the top →
   **Share**).
2. Tap **Copy iCloud Link**.
3. Paste the link as `SHORTCUT_URL` in [`side_control/app/config.py`](../side_control/app/config.py),
   rebuild and restart Side Control. The first QR code now shows the link.

Keep this page in step with the shortcut: it is the only reviewable record of it (the signed file
cannot be inspected).

## Troubleshooting

- **"Local Network" prompt never appeared, or requests fail.** Settings → Privacy & Security →
  Local Network → turn **Shortcuts** on. The prompt only appears on the first request.
- **Requests time out.** The phone and the computer must be on the same network (not on a guest Wi-Fi
  that isolates clients). The address in the setup code is `<hostname>.local` when mDNS runs
  (avahi-daemon); without it, the IP address at setup time, which a new DHCP lease can change — run
  **Set Up Again** then.
- **The computer's firewall.** The server listens on TCP port 47913 (`CLIPBOARD_PORT`):

  ```sh
  sudo firewall-cmd --permanent --add-port=47913/tcp && sudo firewall-cmd --reload
  # or
  sudo ufw allow 47913/tcp
  ```

- **Everything worked, then stopped after reinstalling Side Control.** A reinstall makes a new token:
  run **Set Up Again** with the new setup code.
- **"The Shortcut isn't published yet."** `SHORTCUT_URL` in `side_control/app/config.py` is still
  empty; the maintainer has to publish the iCloud link first (see above).
- **The clipboard stays empty.** Only text travels; copied images and files do not. iOS also keeps
  passwords and other concealed pasteboard items from Shortcuts — by design.
