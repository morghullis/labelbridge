# What's new in LabelBridge

English version of [CHANGELOG.md](CHANGELOG.md). Each published version shows these notes in the app's update window when the app is in English, and on the English download page.

## 1.20.0

- **Labels that work for any packaging, with nothing to set up.** Before, Shopify chose the service, almost always Ground Advantage, which USPS doesn't accept in its official packaging. Now USPS labels are bought with **Priority Mail**, which works on the white envelope, the poly mailer, your boxes and USPS Priority boxes or envelopes. Priority Mail costs more than Ground Advantage; if you want another service for some orders, change it in **Carrier rules**. Orders going abroad still use the service Shopify chooses.
- **Packaging: the label matches the packaging the order ships in.** Before, LabelBridge bought every label for Shopify's default package (the white envelope). Now you have your **Packaging** list (Orders → ⋯ → Packaging…): add your own, like the white envelope, the poly mailer or your boxes, with their outside size and empty weight. The official USPS flat rate packaging is already in the list: Flat Rate Envelope, Padded, Legal, and Small, Medium and Large Boxes.
- **Each order gets its packaging, based on the shipping it paid for.** **Carrier rules** have two new columns, **Packaging** and **Service**. For example: "Express" ships in the Flat Rate Envelope (flat rate) and everything else in the poly mailer. This applies to automatic purchases, to **Print label** (the rule's choice comes preselected and you can change it) and to batch printing.
- **Official USPS packaging always goes with Priority Mail.** USPS only accepts its official packaging (Flat Rate and the Priority boxes or envelopes) with a Priority Mail or Priority Mail Express label. With that packaging, LabelBridge only lets you pick those services and never buys Ground Advantage. A Priority box or envelope without flat rate is added as your own packaging, marked **Official USPS Priority**.
- **FedEx:** the **FedEx label** window shows the packaging from your rule and reminds you that FedEx envelopes, Paks and boxes only work with FedEx Express (2Day, Overnight, Express Saver), never with FedEx Ground.
- If Shopify doesn't accept a packaging for a shipment, it buys and charges nothing and LabelBridge explains what to choose. Your old "My box" becomes the first packaging in the list.

## 1.19.0

- **Always on** (Settings → General, on Windows): so LabelBridge prints even when nobody is using the PC. Each piece is turned on with a button and confirmed first:
  - **Don't sleep when plugged in:** when plugged in, the PC won't sleep or hibernate and closing the lid won't turn it off (the screen still turns off).
  - **Wake up if put to sleep:** if someone puts it to sleep, it wakes up by itself every 10 minutes and keeps printing.
  - **Print without anyone signed in:** when the PC starts, or when signed out, LabelBridge works in the background with your account (Windows asks once for your password, in its own security window; Windows keeps it, not LabelBridge). When you sign in, the window takes over without printing anything twice.
- With the screen locked it already printed; with the PC turned off nothing can print.

## 1.18.0

- **New design, dark with neon green.** An elegant dark background and neon green for what matters: the main button, the active tab (with a glowing bar), the print status, switches and charts. Smooth animations: the main button lights up on hover, each screen slides in gently and numbers count up to their value (turned off with Windows' "reduce animations").
- **Professional Windows notifications:** native Windows 10/11 notifications with LabelBridge's name and logo, the computer that sends them and a button that goes straight to what they're about (orders, history, report or connections). They stay in the Notification Center.
- **About** (Settings → About): the version, who develops LabelBridge, the support contact, **Copy system information** to get help, and the legal documents.
- **Terms of Use and Privacy Policy:** the installer shows them to accept and the app asks once (they're also on the download page). They explain what data LabelBridge uses, that it stays on your computer and which services it's shared with only if you connect them.
- **AI assistant notice:** before the first message, it explains what store data is sent to the artificial intelligence provider you chose (never your customers' email or phone, or your keys).
- **Sturdier updates:** if the download is cut, it continues where it stopped (up to 5 tries); if Windows (Defender) blocks the installer, the app explains in plain words how to allow it.
- **The weekly report now sees visits** (with the new `read_reports` permission; add it with **Copy permissions** in the Dev Dashboard and release the app version). It counts people only, not bots:
  - **Who reaches checkout and doesn't buy**, compared with the industry's cart abandonment (Baymard). If it happens a lot, it comes first in **What to fix**, with steps: Shopify's automatic abandoned checkout email (Messaging app), checking checkout as a customer and showing guarantee and delivery times next to the pay button.
  - **Visits that buy**, compared with online jewelry conversion (Dynamic Yield).
  - If your website is outside Shopify (like your own site that sends buyers to Shopify's checkout), Shopify only sees the visits that reach checkout: the report detects it, doesn't invent a conversion for your site and measures who reached checkout and didn't buy.
- Without the permission, the report still comes and says how to add it (before, it asked for that permission without using it).
- Clearer wording on several screens.

## 1.17.0

- **Weekly store report** (new **Report** tab, under Operation). Every Monday at 8 a.m. (store time) LabelBridge prepares the report for the week that just ended and lets you know:
  - The week's numbers vs. the previous week and the 4-week average: sales, orders, average order value, returning customers, fulfilled within 2 days, refunds, discounts, shipping charged vs. what labels cost and late deliveries, plus sales by day.
  - **What to fix**, with concrete steps, **what's going well** and the **plan for this week**.
  - **Industry comparison**: your store vs. public e-commerce and jewelry averages (Dynamic Yield, NRF, Baymard, Little Stream), each with its source and marked as an estimate.
  - With few orders it draws no conclusions from percentages (and says so), and a week in progress is compared with the same days of previous weeks.
- **The assistant reads the report** and explains it: ask it "how is the store doing?" or "what should I improve this week?".
- You can view previous weeks (they're saved on the computer) or recalculate with **Refresh**. Everything stays in the app: nothing is emailed.

## 1.16.0

- **The order label prints on another printer.** In Shopify → **Where each document prints** you choose the printer for the shipping label, the one for the order label (the one that prints first, with the order number, items and address) and the one for metafield labels, each with **Print test**. If the order label and the shipping label print on the same printer it warns you (even if they have different names) and, if you don't have another one, it takes you to add it. On an office printer with letter paper, the order label prints as a packing slip. The assistant sees and changes it too.
- **New, clean and sober design:** white surfaces without bordered boxes, alerts with a colored line, the active tab marked with a gold line and a clearer printing status (bottom left): a colored dot that pulses softly only while printing. Checked boxes show their check mark, and animations are short and respect Windows' “reduce animations”.
- **Small window:** the menu moves to a bar at the bottom (Home, Orders, Printers, History and **More**) and no screen spills off the sides: filters, buttons, forms and guide screenshots adapt to the width.
- **No silent errors:** if something fails, it's kept in the Log with its details and you find out, with an alert by the clock and another at the top of the window with **View log** (also when there's no system tray).
  - Printing and the Shopify and n8n connections recover by themselves from an unexpected error; meanwhile the status says **Printing problem**.
  - A label that Windows rejected is no longer marked as printed.
  - If the app can't open or its settings got damaged, it says so (and keeps a copy).
  - It also lets you know when n8n sends an invalid job, when a metafield has no label, when the order tag couldn't be added or when email alerts can't go out.

## 1.15.0

- **Reprinting never charges another label.** If an order already has a label, the button says **Reprint label**: it prints the same one (the copy saved on the computer or, if it's gone, the one Shopify has) and doesn't buy or pay for another.
- **Right-click everywhere in the app:** on orders (the table and the “Need action” and “Delays” cards), in History (preview, reprint, retry, cancel, copy the tracking number, open the order in Shopify), in Printers (edit, default, test, delete; add detected ones or install them in Windows) and in Home's “Latest”.
- **The assistant can set up the PC** (Settings → Assistant → **PC access**, Windows only and off by default):
  - **Windows tools:** it checks how the PC is doing, the Windows print queue and whether the printer answers on the network; it clears stuck queues, restarts the print service, changes the Windows default printer or sleep settings, opens Windows Settings and prints the Windows test page.
  - **Screen control (with Claude):** for what has no tool (a driver installer, the printer's web page), it sees the screen and uses the mouse and keyboard. First it asks permission for the task; then **each action** shows you a window with the spot marked or the exact text, and you can allow it, refuse it or **stop everything**. LabelBridge minimizes meanwhile and a bar with **Stop** stays at the top.
  - Always with your permission: administrator tasks also open Windows' own permission, which only you can accept. The assistant never uses LabelBridge's windows with the mouse or keyboard, never types passwords and can't change these settings.

## 1.14.0

- **Shopify Flow buys the FedEx label by itself.** In Orders → **Carrier rules** you choose who buys FedEx: **Shopify Flow, automatically**, or you, with the “FedEx label” window (as until now). With Flow, a workflow in your store buys the FedEx label as soon as the order comes in and LabelBridge does the rest: it doesn't ask you to buy it, it prints it as soon as it shows up and, if Shopify doesn't give it the PDF, it opens the order in Shopify so you click “Print label” (and stops asking once Shopify marks it printed). If Flow hasn't bought it within 15 minutes, it lets you know and the order moves to “Need action” with **Buy FedEx label**.
- **How to set up the workflow, step by step:** when you choose Shopify Flow, LabelBridge shows you the trigger, the conditions (with your FedEx rule's value and copy buttons) and the action, with a button to open Flow. Flow doesn't offer a signature or extra insurance: if you need them, keep using the “FedEx label” window.
- While Flow is buying a label, that order offers no buttons to buy another one (not in **Print N labels** or through the assistant either), so you never pay twice. With Flow, the FedEx rule comes before the others (as in the workflow), it only applies to fully paid orders and, if the order is on hold in Shopify, Flow buys it when it's released.

## 1.13.0

- **Always working.** By default, the computer doesn't go to sleep on its own while LabelBridge is open (the screen still turns off), so it keeps printing with the screen off. If someone puts it to sleep by hand or closes the lid, it catches up right away when it wakes. It opens by itself when the computer starts and, on the Mac, it reopens if it closes because of an error.
- **Updates itself** (Settings → General → Updates, on by default): it downloads and verifies the new version and installs it when it isn't printing or buying labels; when it opens again it shows what's new. If a version doesn't install, it lets you know as before and you decide.
- **Processed orders**: an **All / Pending / Processed / Delays** filter (processed = “Fulfilled” in Shopify; all by default, as always). In **More** or with right-click: **Mark as fulfilled in Shopify** (without a label, for what you shipped another way; several at once too) or **Cancel fulfillment** (back to Pending; if it has an active label, it explains that it's voided in Shopify and Shopify refunds it).
- **More organized design:**
  - Sidebar by sections (Operation, Store and connections, Help), with the same tabs.
  - **Home** as the day's summary: today's orders, printed labels, what's waiting and the printer, plus the connections and the latest activity.
  - Cleaner **Orders**: one strip with the numbers, two-line “Need action” cards and the carrier in a list.
  - **Settings** in tabs (General, Alerts, Email, Label, Assistant, Security), with **Save settings** always at the top.

## 1.12.0

- **Guided FedEx label, in a few clicks.** Shopify doesn't let any app buy FedEx or pass it the signature or insurance, so the label is bought in Shopify, but LabelBridge does everything else: one click opens the order in Shopify and shows a card on top of the browser with what to choose, in the order of Shopify's screen (FedEx 2Day, signature required, insurance for the value of the items with a button to copy it, and your box). As soon as you buy it, LabelBridge detects it, prints it by itself and confirms it with the FedEx number; the order is fulfilled in Shopify.
- In Orders, FedEx orders have **Buy FedEx label** (and **Print label** opens the same); **Another carrier…** is still available.
- In **Carrier rules** you choose how FedEx is bought: the service, the signature (required, adult or none) and whether it's insured for the value of the items.

## 1.11.0

- **Print label, with one button.** In Orders, choose an order and press **Print label**: if it already has a label it reprints it; if not, a short window opens to buy and print it with one click. Orders that don't take a label (store pickup, cancelled, already shipped) say why.
- **Choose the carrier and service:** USPS Ground Advantage, Priority Mail or Priority Mail Express; UPS Ground, 3 Day Select, 2nd Day Air or Next Day Air; and DHL Express for abroad. Or **Automatic**, which uses the service the customer paid for or your preferred carrier in Shopify. If Shopify doesn't offer that service for the shipment, it says so and charges nothing.
- **FedEx from the same list:** since Shopify doesn't let apps buy it, choosing it opens the order in Shopify (1 click to buy it) and LabelBridge prints it by itself as soon as it sees it.
- **Several orders at once:** choose several in the table (Ctrl or Shift + click) and press **Print N labels**. One confirmation: the ones that already have a label are reprinted, the rest are bought with the service and box you choose, and you see each result and the total spent (up to 25 purchases per batch; it can be stopped).
- **Only orders that ship.** LabelBridge only has orders that go out with a carrier and have who and where to send them (name, address, city, country, ZIP code and state where needed). Store pickups, in-store sales, cancelled orders and orders from other channels without an address don't show up, and a label is never bought without an address: not automatically, not with **Print label**, not by the assistant.
- **Track in a LabelBridge window**, without going to the web: the shipment's status, when it arrives, the steps (label created → picked up → in transit → out for delivery → delivered) and every event the carrier reported, with date and place.
- More compact buttons in Orders: **Print label**, **View order** and **More** (open in Shopify, track, copy the tracking number). The box is chosen from a list: Shopify's package, **My box** (the one you saved) or another.
- **New optional permissions** (add them with **Copy permissions** in the Dev Dashboard): `read_shipping` shows only the services Shopify offers for each shipment, `read_inventory` shows the stock in the manual label and `read_customers` tells you in **View order** whether the customer bought before (and their email, if you check **Email** in *Protected customer data*). Without them everything else works the same, and the Shopify screen says which ones are missing.
- The assistant can also choose the service when buying a label (always with your confirmation) and check which services Shopify offers for an order.

## 1.10.0

- **View order.** In Orders, double-click (or **View order**) shows what each order has without leaving the app: products with their photo, variant, SKU and personalization (engravings, sizes), shipping address and method, totals, the customer's note, the gift message and tracking.
- **Instructions on each order** (packing, gift, signature…): write them in **View order** or let the assistant add them; they go in the order note in Shopify (shown there and on the packing slip) without touching the customer's note.
- **New manual label.** For a shipment that didn't come from the online store (a replacement, a sale by message): type who it goes to and what it contains (store products or a custom item); LabelBridge creates an order marked “Manual · LabelBridge” in Shopify, paid and with nothing charged to the customer, then you choose the box and its label is bought and printed. Automatic purchasing leaves these orders alone.
- **Email alerts** to the owners from your email account (Gmail, iCloud or another): choose which alerts are also emailed (**Email** column in Notifications), with a button to send a test and an hourly limit. Emails only go to the addresses in Settings.
- **The assistant works with orders:** it tells you what each one has, writes their instructions and emails the owners without asking (with a limit), and creates manual orders and buys labels always with your confirmation.
- **Owner's rules** (Settings → Assistant): standing instructions the assistant always follows, for example “orders over $500 get a signature”. They can't skip confirmations or security limits.
- **Installer:** it also asks, if you want, for your Shopify app's Client secret; when the app opens it stores it in the secure store, tests the connection and leaves the store active. The first working connection also activates the store from the app (and on the Mac).
- **Copy scopes** includes read_products, to choose store products in the manual label.

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
