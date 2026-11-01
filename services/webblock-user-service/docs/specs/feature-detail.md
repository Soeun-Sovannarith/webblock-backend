# WebBlock — Feature Detail (source of truth, 10 features)

> Copied from `Feature_detail.docx` (latest version). If code and this file disagree, stop and ask the team before changing either.

## 1. Overview
WebBlock is an AI-powered no-code website builder that helps small businesses create, customize, deploy, and manage professional websites without coding. It provides templates, visual customization, responsive previews, and managed hosting and deployment, while excluding some features within the user’s generated website, such as online payments, orders and bookings. The only payment WebBlock handles is the business owner's monthly subscription to WebBlock itself (Feature 07).

## 2. Users & Roles
### 2.1. Target Users - Small Business Owners
Examples include:

- Restaurants

- Cafés

- Salons

- Barbershops

- Small retail businesses

- Personal businesses

### 2.2. Admin (WebBlock Platform Administrator)
Platform administrators operate the WebBlock platform itself. They sign in with the email and password provided by the development team and are responsible for:

- Managing user accounts

- Managing user websites, including website status and deploy results

- Redeploying a website when needed

- Managing user subscriptions

- Managing plans and pricing (Plus, Pro, Ultra)

- Creating offers (discounts or free trials)

Administrators work in the Admin Portal (Feature 08), which is separate from the business owner’s Dashboard and Business Portal.

### 2.3. User (Business Owner)
The business owner is the User role. Each business owner can:

- Create and edit websites in the visual website editor

- Choose the website’s WebBlock subdomain before the first deployment (it cannot be changed afterwards)

- Deploy and manage their websites

- Use the free trial and subscribe to a paid plan

- Manage products and business information in the Business Portal (the management area of each generated website, opened from Website Management, Feature 03), on the web or in the mobile app (Android + iOS)

- Receive notifications about their apps, payments (their subscription payment to WebBlock), and resources

### 2.4. System (Automated)
The system is not a person and has no login, so it is not a user role in the same sense as Admin and User. It is the name this document uses for the parts of WebBlock that run automatically, with no user action, after a user triggers them: Domain & SSL Management (Feature 04), AI Website Generation (Feature 05), Deployment (CI/CD) (Feature 06), Notifications (Feature 09) and Monitoring (Feature 10).

## 3. Core Features
The WebBlock MVP is made up of 10 features. Each feature below lists the roles involved, its sub-features, and what each role can do. The roles are Admin (WebBlock platform administrator), User (business owner) and System (runs automatically, with no user action).

| **\#** | **Feature**                   | **Roles**           | **Summary**                                                                                                                                                                       |
|--------|-------------------------------|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 01     | Authentication                | Admin, User         | Users register with Google or email (OTP verification), sign in, and reset a forgotten password. Admins sign in with accounts provided by the development team.                   |
| 02     | Dashboard                     | User                | Home screen to open existing websites, create a website through template selection and business information, manage settings, and contact support.                                |
| 03     | Website Management            | User                | Edit the website in the Puck visual editor (including AI prompts), preview it, manage products and business information in the Business Portal, view the subdomain, and redeploy. |
| 04     | Domain & SSL Management       | System, User        | The user chooses a WebBlock subdomain (no custom domains); the system maps it to the website and secures it with SSL (HTTPS).                                                     |
| 05     | AI Website Generation         | System              | Convert the Puck layout into Next.js code and generate the frontend and backend code for the website.                                                                             |
| 06     | Deployment (CI/CD)            | System              | Containerize and deploy the generated application to production.                                                                                                                  |
| 07     | Payment Service               | User, Admin         | Users get a 14-day free trial, then subscribe monthly to the Plus, Pro, or Ultra plan. Admins manage plan prices, limits, and offers.                                             |
| 08     | Admin Portal (WebBlock Admin) | Admin               | Manage user accounts, websites, deployment logs, subscriptions, plans and pricing, and offers.                                                                                    |
| 09     | Notifications                 | System, User, Admin | Automatically send web, mobile, and email alerts for website, payment, and platform events to the right recipients.                                                               |
| 10     | Monitoring                    | System, User, Admin | Track website status, deployment history, resource usage, and platform health.                                                                                                    |

### 3.1. Authentication
Authentication controls who can enter WebBlock and which area each person reaches. Business owners create their own account, either with a Google account or with an email address that is verified by a one-time password (OTP), and then sign in to reach their Dashboard.

WebBlock platform administrators do not register themselves; they sign in with the account provided by the development team and are taken to the Admin Portal.

**Roles:** **Admin** (WebBlock platform administrator), **User** (business owner)

**Sub-features**

- **Registration (Google or email)**

  - **User**: register in one step with a Google account or register by email by entering a full name and email address

- **Email OTP verification**

  - **User**: receive a one-time password (OTP) at the email address used for registration

  - **User**: enter the OTP to verify the email, then create and confirm a password to finish the account

- **Sign in**

  - **User**: sign in with email and password, or continue with Google

  - **Admin** (WebBlock platform administrator): sign in with the email and password provided by the development team; admin accounts cannot be created through registration

- **Forgot password**

  - **User**: request a password reset from the sign-in screen

  - **User**: receive a 6-digit OTP by email and enter it to confirm the request

  - **User**: set and confirm a new password, then sign in with it

### 3.2. Dashboard
The Dashboard is the business owner’s home screen after signing in. It allows owners to manage their existing websites, start creating new websites using predefined templates, manage account settings, and contact the WebBlock team for support. First-time users are guided to select a template to create their first website.

**Roles:** User

**Sub-features**

- **My Websites**

  - View the list of all websites the user has created.

  - Search the list to quickly find a specific website.

  - Display an empty state for first-time users and guide them to Template Selection.

  - Open an existing website in the Visual Website Editor (Puck) to continue editing.

- **Template Selection and Website Creation**

  - Browse the website templates provided by WebBlock (three templates in the MVP).

  - Search the available templates to find one that fits the business.

  - Select a template to open a pop-up form for entering business information.

  - Collect the business name, type, description, contact details, address, and opening hours.

  - Allow users to upload a business logo (optional).

  - Create a website draft using the selected template and submitted business information.

  - Redirect users to the Visual Website Editor (Puck) after successful creation.

- **Settings**

  - View and update the account's full name and email.

  - Change the account password.

  - Log out of WebBlock.

- **Contact Support**

  - Write and send an email to the WebBlock admin to ask a question or report a problem.

### 3.3. Website Management
Website Management allows business owners to manage their websites throughout their lifecycle, from editing and previewing changes to managing business information and redeploying updates. Owners can use the Puck Visual Website Editor to customize their websites without writing code and access the Business Portal to manage products and business information. The Business Portal is part of the WebBlock platform, not the generated website, and communicates with the website's data through the generated backend API (Feature 05). It is available on the web and in the WebBlock mobile app (Android + iOS). Each business owner can access only the websites and data belonging to their own businesses.

**Roles:** User

**Sub-features**

- **Edit Website (Puck Visual Editor)**

  - Add predefined blocks by dragging them from the component list or tapping them.

  - Remove or duplicate blocks and reorder them by dragging.

  - View the page structure in the sections list.

  - Use predefined blocks only: Hero, Text, Product Cards, About, Location, and Footer.

  - Customize headings, descriptions, welcome messages, button text, paragraphs, and supported images.

  - Use natural-language AI prompts to request supported changes to website elements.

  - Adjust Product Card columns (2–4), content alignment (left, center, right), spacing (tight, normal, roomy), color palette (accent, background, text), font style (Editorial, Clean, Rounded), and button style (square, rounded, pill).

  - Save changes as a draft, continue editing later, and undo or redo changes.

- **Preview Website**

  - Preview the website in desktop, tablet, and mobile views.

  - Preview to the actual frame of each devices in /preview endpoint

  - Return to the editor to make further changes.

  - Choose the WebBlock subdomain before the first deployment.

- **Manage Products and Business Information (Business Portal)**

  - Open the Business Portal on the web or through the WebBlock mobile app (Android + iOS), without needing a computer.

  - Add, update, or delete products.

  - Update the business information displayed on the website.

- **Subdomain Settings**

  - View the website's current WebBlock subdomain.

  - Keep the selected subdomain, which cannot be changed after it is chosen (Feature 04).

- **Redeploy Website**

  - Redeploy the website to publish the latest saved changes to the live site.

  - Make the updated version visible to visitors once redeployment is complete.

### 3.4. Domain & SSL Management
Every website published on WebBlock is reached through its own address under the WebBlock domain, called a subdomain (for example, business.webblock.com). The business owner chooses this name, and the system then secures it with an SSL certificate so the website opens over HTTPS and connects it to the deployed website. In the MVP, owners cannot connect their own custom domain (such as business.com).

**Roles:** System, User

**Sub-features**

- **WebBlock subdomain (no custom domains)**

  - **User**: choose a subdomain name before the first deployment (e.g., business.webblock.com)

- **SSL certificates**

  - **System**: issue an SSL certificate automatically for every subdomain, so the website is served securely over HTTPS

  - **System**: renew each certificate automatically, with no action needed from the user

- **Subdomain mapping**

  - **System**: map each subdomain to the infrastructure of its deployed website, so visitors who open the address reach the right site

### 3.5. AI Website Generation
AI Website Generation is the step that turns the website the owner designed into a real, working web application. When the owner publishes, the Puck editor has already saved the page as a layout: a structured description of which blocks are used, in what order, and with what content and design settings. The system reads this layout and generates the source code of a Next.js application. This includes the frontend pages that visitors see and the backend that stores and serves the business’s data, such as its products. The Business Portal itself is also generated along the way with the user’s website, and its backend. After that, the generated application is then handed to Deployment (CI/CD) (Feature 06) to go live.

**Roles:** System (runs automatically)

**Sub-features**

- **Layout to code**

  - Read the PuckJS layout saved by the editor: the list of blocks, their order, their content and their design settings

  - Convert the PuckJS layout into Next.js code, so each block (Hero, Text, Product Cards, About, Location, Footer) becomes part of a real page

  - Keep the design the owner chose in the editor, such as colors, fonts, button style, alignment and spacing, so the generated website matches the Preview

- **Backend code**

  - Generate the backend code from a predefined project structure, so every generated website follows the same organisation of files and code

  - Generate the backend database schema from the custom fields, so the business’s data is stored with the fields it needs

  - Produce Next.js API route handlers that run CRUD (create, read, update, delete) operations on the shared database, including the custom fields

  - Through these API routes, the website’s data, such as products, can be created, read, updated and deleted

### 3.6. Deployment (CI/CD)
Deployment (CI/CD) is the automated pipeline that takes the code produced by AI Website Generation (Feature 05) and puts it live on the internet. The pipeline packages the application into a Docker container, creates the infrastructure that this website needs to run, and deploys both the frontend and the backend/API. Each generated website runs on its own independent infrastructure. When the deployment finishes, the website is reachable on its WebBlock subdomain, and the owner is told whether the deployment succeeded or failed. If the deployment fails, the users are able to retry the deployment twice.

**Roles:** System (runs automatically, no user action)

**Sub-features**

- **Deployment trigger**

  - Start the deployment of the generated app to production automatically when the owner publishes from the Preview, or deploys the latest changes from Website Management (Feature 03)

  - Start a redeploy when a WebBlock platform administrator redeploys a website from the Admin Portal (Feature 08)

- **Containerization**

  - Containerize the generated application using Docker, packaging the code and everything it needs to run into one container image

  - The same container runs in the same way wherever it is deployed

- **Infrastructure provisioning**

  - Provision the generated app’s independent infrastructure dynamically via the deploy management framework

  - Keep each website on its own infrastructure, separate from the other websites on WebBlock

- **Frontend and backend/API deploy**

  - Deploy the Next.js frontend and the backend/API to the provisioned infrastructure

  - Connect the deployed website to its WebBlock subdomain and SSL certificate (Feature 04)

  - Report the result: the owner is notified when the app is ready or when a deployment fails (Feature 09), and admins can see the deploy result in the Admin Portal (Feature 08)

### 3.7. Payment Service
The Payment Service controls how business owners pay for using WebBlock. A new owner can publish their first website for free, and it stays online for 14 days. To keep the website online after the trial, the owner subscribes to one of three paid plans (Plus, Pro or Ultra), each with its own monthly price and limits, and pays every month to keep the plan active.

**Roles:** **User** (business owner)

**Sub-features**

- **14-day free trial**

  - **User**: use the free trial for the first deploy, without choosing a paid plan

  - **User**: the website stays online for 14 days during the trial

- **Paid plans (Plus, Pro, Ultra)**

  - **User**: view the paid plans and compare their monthly price and limits

  - **User**: subscribe to a paid plan to keep the website online after the trial

- **Monthly payment**

  - **User**: pay each month to keep the plan active

  - **User**: receive a reminder by mobile and email when the monthly payment is due (Feature 09)

This service covers WebBlock subscriptions only. The MVP does not provide e-commerce transactions (shopping carts, checkout or payment collection) for a business’s own customers.

### 3.8. Admin Portal (WebBlock Admin)
The Admin Portal is WebBlock’s internal back office and is used only by WebBlock platform administrators. It gives admins an overview of every business owner’s account and every website on the platform. Admins use it to look up a user and their websites, to check whether each website was deployed successfully, to read the deployment logs and monitor each live website, and to fix a problem by redeploying a website when needed. The Admin Portal also holds the Account & Subscription section, which is the admin side of the Payment Service (Feature 07): here admins look after every business owner's subscription, keep the plans (Plus, Pro, Ultra) and their prices and limits up to date, and create offers such as discounts or free trials. Business owners have no access to any part of the Admin Portal.

**Roles:** Admin (WebBlock platform administrator; WebBlock’s own portal for running the platform)

**Sub-features**

- **Manage user accounts**

  - View the list of all user accounts and search them by name or email

  - Open an account to view its details

  - View the projects (websites) that belong to a user and the status of each website

- **Manage user websites**

  - View the list of all websites and search them by owner, domain or status

  - View the deploy result of each website, to check whether its latest deployment succeeded or failed

  - Redeploy a website when needed, for example after a failed deployment

- **Deployment logs & monitoring**

  - View the deployment logs of each website, to see what happened during a deployment and why it failed

  - Monitor each live website: whether it is up, whether it has crashed, and how close it is to the resource limits of its plan

- **Manage user subscriptions (Account & Subscription)**

  - Search subscriptions by user, project or status

  - View the details of a subscription: plan, status, start/end date and deploys left

  - View the free trial status and the number of days left

  - View the payment history of the subscription plan

  - Update a user’s subscription plan

- **Manage plans & pricing (Plus, Pro, Ultra)**

  - View all plans with their price, limits and active status

  - Select a plan to edit

  - Edit the monthly price of a plan (a confirmation is required before the change is saved)

  - Edit the plan limits: number of sites, redeploys per month, products, and logs/monitoring

- **Offers**

  - Create an offer: a discount or a free trial

  - Set the start date and end date of the offer

The Admin Portal is separate from the business owner's Dashboard and Website Management and must not expose platform-level administration to business users.

### 3.9. Notifications
The Notifications feature keeps both website owners and WebBlock admins informed about important events without requiring them to check the platform manually. The system automatically detects events, determines who needs to be notified, and delivers notifications through the appropriate channels based on the event’s importance.

Website owners receive notifications about their website generation, deployments, subscriptions, and live website health. Admins receive notifications about platform-wide issues, website failures, resource usage, and subscription changes. Each notification directs the recipient to the relevant page where they can review the details or take action.

**Roles: System, User, Admin**

- **System:** Detects events, determines the recipients and channels, and sends notifications automatically.

- **User (Website Owner):** Receives and manages notifications related to their own website and subscription.

- **Admin:** Receives and manages notifications related to platform operations, users, and subscriptions.

**Sub-features**

- **Web notifications**

  - Website owners receive notifications for app generation and deployment events through the WebBlock website.

  - Admins receive notifications through the Admin Portal for all admin-related events.

  - Both interfaces display notifications through a notification bell with an unread count.

  - Recipients can open the notification list, mark individual notifications or all notifications as read, and click a notification to navigate to the relevant page.

  - Website owners can receive web notifications before installing the mobile app.

  - Admins can receive email alerts for critical events even when they are not signed in to the Admin Portal.

- **Mobile notifications**

  - Website owners receive mobile notifications for all supported website and subscription events.

  - Notifications alert owners about generation completion, deployment failures, payment deadlines, website crashes, and resource-limit warnings.

- **Email notifications**

  - Website owners receive emails for important subscription and website health alerts, including payment deadlines, website crashes, and resource-limit warnings.

  - Admins receive emails for critical platform incidents, including stuck website generation and platform services that are down or unhealthy.

  - Email notifications allow recipients to be informed even when they are not actively using WebBlock or the mobile app.

**Notification events and channels**

| **Event**                                       | **Recipient** | **Web** | **Mobile** | **Email** |
|-------------------------------------------------|---------------|---------|------------|-----------|
| Website generation completes                    | Website Owner | ✓       | ✓          | —         |
| Website generation gets stuck                   | Admin         | ✓       | —          | ✓         |
| Deployment succeeds                             | Website Owner | ✓       | ✓          | —         |
| Deployment fails                                | Website Owner | ✓       | ✓          | —         |
| Deployment fails                                | Admin         | ✓       | —          | —         |
| Monthly payment is due                          | Website Owner | —       | ✓          | ✓         |
| Website crashes during live traffic             | Website Owner | —       | ✓          | ✓         |
| Website crashes during live traffic             | Admin         | ✓       | —          | —         |
| Website exceeds its resource limits             | Website Owner | —       | ✓          | ✓         |
| Website exceeds its resource limits             | Admin         | ✓       | —          | —         |
| Platform service goes down or becomes unhealthy | Admin         | ✓       | —          | ✓         |
| A new subscription becomes Active after payment | Admin         | ✓       | —          | —         |
| A user's subscription expires                   | Admin         | ✓       | —          | —         |



**Notification behavior**

- **Automatic delivery:** Notifications are triggered by system events without requiring manual action.

- **Recipient-based delivery:** The system sends each notification to the appropriate website owner, admin, or both when necessary.

- **Importance-based channels:** Each event uses the configured channels appropriate to its urgency.

- **Read status:** Web notifications maintain read and unread status for each recipient.

- **Contextual navigation:** Each notification links to the relevant page, such as website generation details, deployment logs, website monitoring, subscriptions, or user accounts.

- **Background delivery:** Critical email alerts can be sent even when recipients are offline or not signed in.

### 3.10. Monitoring
Monitoring lets people see how websites and the platform are running, so problems can be found and fixed quickly. In the Business Portal (user), the website owner sees the health of their own website: whether it is live, how its deployments went, how much of their plan’s resources it is using, and any errors it has had. In the Admin Portal (admin), the WebBlock team sees the whole platform: every website’s generation and publishing, the health of all sites and services, a lookup for any user, and a log of what admins have done. The system collects this data automatically; people only view it and act on it.

**Roles: System** (collects monitoring data automatically, no user action); **User** (Business Portal); **Admin** (Admin Portal)

**Sub-features**

**Business Portal (user, mobile + web)**

- **Website Status**

  - Shows whether the owner’s website is live, deploying, or down, and when it was last deployed

  - Shows the website’s uptime over recent days

- **Resource Usage**

  - Shows how much of the plan’s maximum resources the website is using (e.g. bandwidth, storage)

  - Warns the owner when usage is close to or over the limit, and points them to upgrade their plan

- **Deployment History**

<!-- -->

- Lists each deployment with its time and result (Success or Failed)

- For a failed deployment, shows a simple reason and lets the owner deploy again

**Admin Portal (admin, web)**

- **Pipeline & Generation**

  - Lists every user website going through generation and publishing, with its current step and status

  - Clicking a website opens a side drawer that shows each step of the publish flow, so the admin can see where it stopped

  - Highlights generation requests that are stuck

<!-- -->

- **Sites & Routing Health**

  - Shows the status of all user websites and their WebBlock subdomains (live, down, suspended)

<!-- -->

- **User Diagnostic Lookup**

  - Admin searches for a user and sees their account, subscription, websites, recent deployments and errors in one place

  - Admin can suspend or unsuspend the user; suspending a user also suspends their websites, while their plan still counts normally

<!-- -->

- **Audit & System Logs**

  - Records every admin action (who did what, to whom, and when), such as suspending a user or changing a plan

  - Records important system events for checking later

## 4. Feature-to-Workflow Mapping
The following mapping shows where each WebBlock feature participates in the end-to-end product workflow.

| **Workflow Stage**                | **Primary Feature**                                 | **Supporting Features**                         | **Purpose**                                                                                                              |
|-----------------------------------|-----------------------------------------------------|-------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| 1\. Create Account                | Authentication                                      | —                                               | Register with Google or email (OTP verification), or sign in.                                                            |
| 2\. Create Website                | Dashboard (Template Selection and Website Creation) | Authentication                                  | Browse and search templates, select a template, enter business information in a pop-up form, and create a website draft. |
| 3\. Edit Website                  | Website Management                                  | Dashboard                                       | Add, remove, and reorder predefined blocks; edit content, layout, and design; and save drafts.                           |
| 4\. Preview Website               | Website Management                                  | —                                               | Check desktop, tablet, and mobile views and return to the editor if changes are needed.                                  |
| 5\. Choose Subdomain              | Domain & SSL Management                             | Website Management                              | Choose a WebBlock subdomain before the first deployment; SSL is provisioned automatically.                               |
| 6\. Start Free Trial              | Payment Service                                     | Admin Portal (plans and offers)                 | Apply the 14-day free trial to the first deployment.                                                                     |
| 7\. Generate Code                 | AI Website Generation (System)                      | Website Management                              | Convert the Puck layout into Next.js frontend code, backend code, database schema, and CRUD API routes.                  |
| 8\. Deploy Website                | Deployment (CI/CD) (System)                         | AI Website Generation, Domain & SSL Management  | Containerize the application, provision its infrastructure, and deploy the frontend and backend/API.                     |
| 9\. Notify Owner                  | Notifications (System)                              | Deployment (CI/CD)                              | Notify the owner when website generation or deployment completes or fails, through supported channels.                   |
| 10\. Manage Website               | Website Management                                  | Domain & SSL Management, Deployment (CI/CD)     | Edit the website, preview changes, and redeploy the latest version.                                                      |
| 11\. Manage Business Content      | Website Management (Business Portal)                | —                                               | Manage products and business information through the web portal or mobile app.                                           |
| 12\. Keep Plan Active             | Payment Service                                     | Notifications, Admin Portal                     | Subscribe to Plus, Pro, or Ultra and pay monthly; receive payment reminders through mobile and email.                    |
| 13\. Monitor Website and Platform | Monitoring                                          | Notifications, Deployment (CI/CD), Admin Portal | Track website health, deployment history, resource usage, and platform operations.                                       |
