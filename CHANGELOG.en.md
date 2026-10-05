# What's new in LabelBridge

English version of [CHANGELOG.md](CHANGELOG.md). Each published version shows these notes in the app's update window when the app is in English, and on the English download page.

## 1.9.1

- **Windows: the installer no longer asks for administrator permission.** It always installs LabelBridge just for your user, without the “for me / for everyone” question.
- If you installed it “for all users” on a PC (which asked for administrator permission on every update), updating moves it to “just for you” by itself: Windows asks for permission one last time and then no more. Your settings, orders and printers are kept.

## 1.9.0

- **LabelBridge for Mac.** New version for Macs with Apple Silicon (M1 or newer) and macOS 15 or newer: on the downloads page press **Download for Mac**, open the .dmg and drag LabelBridge to Applications. It does the same as on Windows: prints Shopify orders, buys labels, alerts with sounds and includes the assistant and the step-by-step guide.
- On a Mac it lives in the **menu bar** (top right): closing the window keeps it printing, it opens by itself when you log in, and the window comes back from its icon or the Dock.
- It prints on the Mac's printers (USB or installed in System Settings) and on network or Wi‑Fi printers without a driver; keys are stored in the Mac **Keychain**.
- Updates also arrive on their own on the Mac: the app lets you know and updates with one click, keeping your settings and the permissions you already gave.
- The first time, since the app isn't signed by Apple yet, the Mac asks you to confirm it: **System Settings → Privacy & Security → Open Anyway** (only once).
- The downloads page offers Windows and Mac (your computer's first) and each version with both downloads.
- If “Quit” is canceled (for example, with a label purchase in progress), the app no longer closes by itself after 20 seconds.

## 1.8.1

- **Full code review** (architecture, bugs, security, screens and tests) and a fix for everything important it found:
- **Safer label purchases.** “Buy label…” can no longer buy at the same time as the automatic purchase (in a rare case two labels could be paid for the same order). An unconfirmed purchase is never repeated, one Shopify rejected can be bought again, and a pending purchase that errors no longer holds up the printing of the others.
- If Windows is in **dark mode**, the app stays light: before, tables, menus and lists could end up with white text on white.
- Important confirmations default to **“No”** (Enter doesn't buy or delete anything), “Reprocess” with automatic purchasing on asks for confirmation, and the assistant's permission window defaults to “Don't allow”.
- **Security:** the Shopify access is only used for Shopify jobs; a job from the local API or n8n can only print on local-network printers; a web page can no longer lock or use the local API; updates without their SHA-256 fingerprint aren't installed; the assistant asks for confirmation (showing every value) to change the daily label maximum, buy unpaid orders, email customers or move n8n to another server, and only adds local-network printers.
- Orders loads up to **1,000 orders** of the period (200 before) and tells you if there are more.
- Orders also works in English with Shopify apps that have few permissions, a canceled job no longer stays “Printing…”, estimated delivery dates no longer warn of a delay a day early, and “Reprocess” during a check is no longer lost.
- Settings added by a newer version are kept even if you go back to an earlier one and update again.
- More texts translated to English, windows free their memory when closed, the guide loads its screenshots when each step opens and sounds no longer freeze the window.
- Safer build: it stops at any error, checks the version before publishing, never publishes two versions at once and runs the Windows update test again.

## 1.8.0

- **Buy labels from LabelBridge.** Any order that needs a person (Shopify rejected the purchase, the daily limit was reached, it's from before the app was connected…) has the **Buy label…** button in **Orders**: you pick the package (Shopify's or your own box with its dimensions, which can be remembered), the weight if needed and whether to email the customer, press **Confirm and buy**, and LabelBridge buys it with Shopify Shipping, prints it, and Shopify marks the order fulfilled with its tracking number.
- Shopify's reasons are explained in plain words (“The package weight is missing”, “The customer's full phone number is missing”…).
- FedEx: Shopify doesn't let any app buy its labels, so it's still one click in Shopify (and it prints by itself) or, if you prefer, it can be bought here with the carrier Shopify picks.
- **Notifications and sounds** (Settings): for each event (new order, order that needs action, delivery delay, label printed, printing error) you choose whether to show a notification and which sound to play: cash register, chime, ding, bell, harp, bubble, alert, the Windows sound or your own .wav file. With volume, a button to preview each sound, quiet hours and a reminder for orders still waiting.
- When a paid order comes in, LabelBridge notifies you with its sound: who, how much and which carrier it ships with.
- **Delivery alerts.** New **Delays and deliveries** section in Orders (with its counter at the top): late deliveries, failed delivery attempts, shipments with problems, packages the carrier still hasn't received days after the label was bought and shipments on the way for too long, with buttons to track, copy the tracking number or open the order. They're checked by themselves every half hour and each one notifies once.
- The Orders table shows how each shipment is going (“On the way · arrives…”, “Delivered on…”), and unshipped orders show how many days they've been waiting.
- The download page offers **every version since 1.5**, each with its notes, in case you need to go back to an earlier one.

## 1.7.1

- **FedEx labels: one click in Shopify and LabelBridge prints it by itself.** Shopify doesn't let any app buy FedEx labels automatically, so orders that ship with FedEx (items over US$1,000) show up in **Orders → Need action** with the **Buy in Shopify** button. As soon as you buy the label in the order, LabelBridge detects it, gets it from Shopify and prints it at 4 × 6 in on your label printer: no downloading or printing by hand.
- The same goes for any order that was waiting for a person (rejected purchase, daily limit…): if its label is bought in Shopify, it prints by itself.
- **Reprint label** (Orders) also works with labels bought in Shopify: LabelBridge gets the PDF from there.

## 1.7.0

- **LabelBridge in English or Spanish.** The whole app is in both languages: tabs, windows, notices, the step-by-step guide with its screenshots, the test label and the notes of every update. The assistant answers in the app's language.
- **New branded installer**: the gold logo on every step (sharp on high-resolution screens) and, at the start, **English / Español**. The app stays in the language you pick.
- **Your store from the installer**: the first time, the installer asks for your Shopify store's address and Client ID to save time. When it finishes, LabelBridge opens straight on "Connect your store" so you can paste the Client secret in its secure window (the secret never goes through the installer). If a store was already set up, it isn't touched.
- New setting **Settings → General → Idioma / Language**: Automatic (the installer's or Windows'), Español or English. When you change it, LabelBridge offers to restart to apply it.
- If you already used LabelBridge, it stays in Spanish after updating, even if Windows is in English.
- Dates and numbers follow each language's format (day/month in Spanish, month/day in English).
- The download page is also in English.

## 1.6.0

- New **Orders** tab: all your Shopify orders, sorted.
  - At the top, the period summary: orders, spend on labels, average per label and spend per carrier.
  - First, **Need action**, each with the reason and the button to fix it: buy the FedEx label by hand, reprint a label that didn't print, retry a purchase Shopify rejected (with its reason), void the label of a canceled order or review orders Shopify marked as fulfilled without a label.
  - Then, the table by carrier (USPS, FedEx, others and local pickup): customer, destination, shipping method, item value, tracking number, cost of each label (including the ones bought by hand in Shopify and the voided ones) and whether it was printed. With search, one-click tracking and export to Excel (CSV).
  - In the sidebar, a badge with the number of orders that need action.
- **Carrier rules** (Orders → Carrier rules). By default, orders with items over US$1,000 ship with FedEx and their label is bought by hand; everything else ships with USPS and LabelBridge buys the label by itself. A USPS label is no longer bought by mistake for a FedEx order.
- If Shopify marks orders as fulfilled as soon as they're paid (so LabelBridge can't buy the label), the Orders tab detects it and explains how to change it.
