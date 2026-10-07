# Privacy Policy

**Version 1 · effective October 7, 2026**

LabelBridge is a Windows and Mac app that prints the shipping labels and order labels of your store's orders. This policy explains what data it uses, where it stays and who it's shared with.

**In short:** your data stays on your computer. It only goes to the services you connect (Shopify, your artificial intelligence provider, your email, n8n) and never to the developer.

## 1. Who is responsible

LabelBridge (the developer). Contact: tonymonzon94@gmail.com.

As the store owner, you're responsible for your customers' data that you handle with LabelBridge, just as in Shopify.

## 2. What data the app uses

- **From your Shopify store:** orders (number, products, totals, status and notes), the recipient's name and shipping address, labels and tracking, and store statistics (sales and visits) for the weekly report. Only what the permissions you give the app in Shopify allow.
- **From this computer:** printers, settings, print history and technical logs used to solve problems.
- **Access keys:** your Shopify key, your AI provider key and your email password are kept in the system's secure store (Windows Credential Manager or the Mac Keychain).

LabelBridge doesn't use cookies, has no analytics or telemetry and doesn't ask you to create an account.

## 3. Where it's stored

On your computer, in LabelBridge's data folder for your user (on Windows, `%LOCALAPPDATA%\LabelBridge`; on the Mac, `~/Library/Application Support/LabelBridge`). Settings, the print queue and label files are stored encrypted (AES-256-GCM), with the key in the system's secure store. Weekly reports are kept for 26 weeks and technical logs rotate on their own.

## 4. Who it's shared with (only if you turn it on)

- **Shopify:** the app reads and updates your orders and buys labels with the permissions you give it. Shopify's privacy policy applies.
- **Your artificial intelligence provider** (for example Anthropic, OpenAI or Google), if you turn on the assistant with your own key. See section 5.
- **Your email server**, if you turn on email alerts. Alerts only go to the addresses you enter in Settings.
- **n8n, MQTT or the local API**, if you connect them. They receive or send the print jobs you set up.
- **The updates page (GitHub):** every few hours the app checks for a new version and only downloads public files. Like any website, GitHub sees the IP address.

We don't sell data or share it with anyone else. The developer doesn't receive your data.

## 5. The artificial intelligence assistant

The assistant is optional and stays off until you connect a provider with your own key. When you use it, the provider you chose receives:

- what you write in the chat;
- the data the assistant looks up to answer you: orders (products, totals, status, notes, the recipient's name and shipping address), store products, the status of printers and the app, and the weekly report's numbers;
- if you turn on screen control (Windows only), screenshots during each task you approve.

**Never sent:** your customers' email or phone (unless you type them in the chat), or your keys: the app removes them before sending any message.

The provider processes this data under its own terms and privacy policy (for example, how long it keeps it and whether it uses it to improve its models). Review them before turning it on. You can turn off the assistant or delete your key at any time in Settings → Assistant.

Artificial intelligence can make mistakes: check what matters. Buying labels, creating orders and making changes always ask for your confirmation.

## 6. Your rights and your customers' rights

Your data is on your computer: you can review or delete it at any time. To delete it completely, uninstall LabelBridge and delete its data folder (section 3). If a customer asks to see or delete their data, handle it in Shopify; LabelBridge only keeps working copies (printed orders and labels) on your computer.

## 7. Children

LabelBridge is a business tool and isn't directed at minors.

## 8. Changes and contact

If this policy changes, the app will show you the new version to accept before continuing. For any question about your data: tonymonzon94@gmail.com.
