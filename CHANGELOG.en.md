# What's new in LabelBridge

English version of [CHANGELOG.md](CHANGELOG.md). Each published version shows these notes in the app's update window when the app is in English, and on the English download page.

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
