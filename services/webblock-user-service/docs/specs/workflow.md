# WebBlock — Workflow (from FigJam board "WebBlock workflow")

> Source: https://www.figma.com/board/dn0Sc9QfaZjfAPP7C5IPkc/WebBlock-workflow — read-only copy of the step labels, in board order. Decisions are marked **◆** (they are Yes/No "Is …?" questions on the board). Arrows/branches are not reproduced: open the board when the exact branch matters.


## Authentication (User, Admin)

### Verify OTP
- Verify OTP
- Input 6-Digit OTP
- Click Verify Email
- Verify OTP
- **◆** Is OTP Valid?
- Create Password
- Click Resend Code
- Send New OTP to Email

### Create Password
- Create Password
- Input Password & Confirm Password
- Click Create Account
- **◆** Is Password Matched?
- Create Account
- Sign In

### Sign In
- Sign In
- Continue with Google
- Sign In with Email
- Input Email & Password
- **◆** Is Password Forgotten?
- Click Forgot Password
- Forgot Password
- Verify Credentials
- **◆** Is Login Successful?
- **◆** Is Admin?
- Admin Portal (WebBlock)
- User Dashboard

### Continue with Google
- Continue with Google
- Continue with Google
- Authenticate with Google
- **◆** Is Authenticated?
- Sign In
- **◆** Is Account Exists?
- Create Account
- User Dashboard

### Forgot Password
- Forgot Password
- Input Email
- Send 6-Digit OTP to Email
- Verify OTP
- Input 6-Digit OTP
- **◆** Is OTP Valid?
- Reset Password
- Click Resend Code
- **◆** Is Email Valid?
- Verify OTP

### Reset Password
- Reset Password
- Input New & Confirm Password
- **◆** Is Password Matched?
- Update Password
- Sign In
- Start
- **◆** Is Already Registered?

### Register
- Register
- Continue with Google
- Sign Up with Email
- Input Full Name & Email
- **◆** Is Valid?
- Send 6-Digit OTP to Email
- Verify OTP

## User Dashboard (User)

### My Websites
- My Websites
- Display Websites
- Filter Websites
- Select a Website
- Click Browse Templates
- Templates
- Click Edit / Open Editor
- Puck Editor – Selected Website
- **◆** Is Website Live?
- Click Preview
- Website Preview
- Click Visit Live Site
- Live Website
- Click Menu
- Filter Websites
- **◆** Is Website Suspended?
- Click Reactivate Website
- Reactivate Website
- Click Suspend Website
- Suspend Website
- Click Portal
- Business Portal (User)
- Suspend Website Dialog
- Click Cancel
- Click Suspend Website
- Close Dialog
- **◆** Is Website Deployed?
- Hide Menu
- Click View live site
- **◆** Is draft that hasn’t been redeployed?
- Click View live site
- View Previously deployed build
- View Current live website
- Start a new website
- Show template pop up
- Search template (name)
- Filter category
- Preview
- Use template
- Website Preview
- Filter template by category
- Search template by name
- Virtual Editor – Selected Website
- Edit
- Puck Editor – Selected Website
- Hide menu
- Search Websites
- Search Websites

### Templates & Website creation
- Templates Page
- Display Available Templates
- (Search, Filter) by Category
- Select a Template
- **◆** Is Previewing First?
- Click Preview
- Show Template Preview
- Click Use Template
- Show Business Info Dialog
- Show Step 1: Business Details
- Input Business Name, Business Type, Description & Logo (Optional)
- **◆** Is Every Required Field Filled?
- Show Step 2: Contact & Hours
- Input Phone Number, Email, Address & Opening Hours
- **◆** Is Every Field Valid?
- Save Business Info
- Click continues to editor
- Visual Editor
- Show templates by Category
- **◆** Is user (Search, Filter )?
- User Dashboard

### Settings
- Settings Page
- Edit profile Photo, Edit Full Name
- Click Save Changes
- Input Current, New & Confirm Password
- Click Change Password
- Display Profile & Account
- **◆** Is Valid?
- **◆** Does user have a password?
- Click Connect Google Account
- Link Google Account
- Continue with Google
- Google Account Connected
- Edit Profile Feature
- Change Password Feature
- Connect with Google Feature
- Click Billing Tab
- Display Current Plan & Billing History
- Download Receipt
- Click Renew now
- Click Receipt
- Billing Feature
- Input New & Confirm Password
- Click Set Password
- (Save, Update) Password
- (Save, Update) Password
- Saved Change
- **◆** Is Valid?
- Click View Detail
- **◆** Is Free Plan?
- Click Choose/Change Plan
- Payment Checkout
- Subscription Plan
- Display Plan Details Dialog
- Click Choose/Change Plan
- Click Renew now
- Payment Checkout
- Subscription Plan
- Click View Detail
- Display Plan Details Dialog
- Click Choose/Change Plan
- Click Close
- Subscription Plan
- Dialog Closed

### Contact Support
- Input Subject & Message
- Send Email to WebBlock Admin
- **◆** Is Fields Valid?
- Show Message send
- Admin received an email
- Show Answer
- Click Back to My Websites
- My Websites
- Select Website & Topic
- Click Quick Answer
- Click Write Another Message
- Contact Support Page

## Website Management – Puck Editor, Modify Website (User)
- Select View (Desktop, Tablet, Mobile)
- Canvas Resizes to Selected Device
- **◆** Is Any Change?
- **◆** Is First Deploy?
- Save Changes as Draft
- Hide "Unsaved Changes" Badge
- Disable Save Button
- Enable Save Button
- Click Save Draft
- Show Preview Website
- Show Portal Button
- Hide Portal Button
- Click Close Preview
- Puck Editor – Selected Website
- Preview
- Click Preview
- Click Tab (Desktop, Tablet, Mobile)
- Show Preview Website
- Click Portal Button
- Open Business Portal
- Business Portal (User) – Business Owner
- Puck Editor
- **◆** Is Anything Changed?
- Show "No Changes to Deploy" Message
- Click Deploy
- Domain Setup
- Is subscription active
- Deployment
- **◆** Is First Deployment?
- Subscription Plan
- Click Back Arrow
- **◆** Is Any Change Unsaved?
- Show "Save Changes?" Dialog
- **◆** Is Saving Changes?
- Save Changes as Draft
- My Websites Page
- Show Puck Editor With Selected Website
- Virtual Editor – Selected Website

### WebBlock AI
- WebBlock AI
- Show AI Chat UI
- Click Tap WebBlock AI
- Input Text Prompt
- AI Processing Prompt
- UI Updated
- **◆** Can process prompt?
- AI response “Request cannot be done”
- Block Added to Page
- Block Appears in Page Structure
- Drag or Tap a Block
- Drag to Reorder
- Click Duplicate
- Click Delete
- Page Structure Updated
- Select a Block
- Show Option Dialog
- Show Block Settings Panel
- **◆** Is Content Tab?
- Edit Label, Heading, Description, Buttons & Image
- Change Set Layout: Columns (2–4), Alignment, Spacing
- Set Visual Style (Whole Site): Color Palette, Font, Buttons
- UI Updated
- UI Updated
- Click a Block
- **◆** Is Change?
- (Revert, Reapply) Last Change
- Nothing to Undo (Button Disabled)
- Click Undo / Redo
- My Websites Page
- My Websites Page

## Admin Portal (WebBlock) – Admin

### Manage User Accounts
- Manage User Accounts
- Show User Account (stats)
- List Users Account
- Export CSV File
- Show Detail Info
- Show Dialog Account & Website Status
- Show Search Result
- Search By Name / Email
- View Account Details
- View Subscription
- View Websites
- Export CSV
- Show Deployment Info
- Contact Owner
- Redeploy Website
- Redeploy
- Show Form Input
- Fills Form
- **◆** Is Valid?
- Send Email
- Deployment (CI/CD)
- Manage User

### Manage User Websites
- Manage User Websites
- Search websites by owner name / domain
- Filter Status
- View details
- Show List User Website
- Show Search Result
- Show Filter Result
- Contact Owner
- Show Form Input
- Fills Form
- **◆** Is Valid?
- Send Email
- Show Detail Dialog
- **◆** Is Failed Deploy?
- Show Succeeded Deploy Info
- Contact Owner
- Fills Form
- Redeploy Website
- **◆** Is Valid?
- Redeploy
- Send Email
- Show Form Input
- Show Failed Deploy Info
- Redeploy Website
- Redeploy
- Deployment (CI/CD)
- Deployment (CI/CD)
- Manage User

### Overview
- Show Overview (stats, revenue)
- Show Detail Dialog
- Show Deployment Info
- Show Detail Dialog
- Show List of Needs Attention
- Show List Recent Deploys
- Click Review Needs Attention
- Click on Record
- Update Plan
- Cancel
- Contact Owner
- Fills Form
- Redeploy Website
- **◆** Is valid?
- **◆** Is Payment?
- **◆** Is Valid?
- **◆** Is Failed Deploy?
- Redeploy
- Send Email
- Show Form Input
- Show Failed Deploy Info
- Show Succeeded Deploy Info
- Update Plan
- Show Subscription Info
- Cancel
- Redeploy Website
- Contact Owner
- Show Form Input
- Redeploy
- Contact Owner
- Fills Form
- Redeploy Website
- **◆** Is Valid?
- Redeploy
- Send Email
- Show Form Input
- Fills Form
- **◆** Is Valid?
- Send Email
- Deployment (CI/CD)
- Deployment (CI/CD)
- Deployment (CI/CD)
- Overview

### Manage User Subscriptions
- Manage User’s Subscriptions
- Manage Plans
- Search subscriptions by user / project / status
- Manage Plans
- Filter Status & Plan
- Show User Subscriptions
- Show Form Edit Plan Dialog
- Subscription
- Show Subscriptions statistics
- Monthly Revenues
- Active plans
- Pending
- Subscription ending
- Show Search Result
- Show Filtered Result
- View User Subscriptions
- Show Detail Info Dialog
- Show Detail Info Dialog
- Save Change Plan
- View Detail Subscription
- Fills Form
- **◆** Is Valid?

### Admin Dashboard pages
- Admin Portal (WebBlock)
- Overview
- User Accounts
- User Websites
- Subscriptions
- Monitoring

### Monitoring
- Monitoring
- Pipeline & Generation
- Activity Logs
- Activity Logs
- Monitoring
- Show activities
- Filter activity type
- Show website publish flow
- Show activities from filter
- Filter by Actor
- Show activities based who did the activity
- Filter by (Request, Generation, Release, Live)
- Show filtered result
- View details
- Show publish flow
- Show steps details

## Use Case
- Register
- Forgot Password
- Manage Account Settings
- Contact Support (Email Admin)
- Browse & Search My Websites
- Select Template
- Enter Business Information
- Edit Website (Puck Editor + AI Prompts)
- Preview Website
- Choose Subdomain
- Publish Website
- Subscribe to Plan / Free Trial
- Manage Products (Business Portal)
- Login
- Logout
- Pay Monthly Subscription
- Manage User Accounts
- Manage User Websites
- Redeploy Website
- Manage Subscriptions
- View Payment History
- Generate Website Code (AI)
- Deploy Website (CI/CD)
- Issue & Renew SSL Certificate
- Redeploy / Retry Failed Deployment
- Update Business Information (Business Portal)
- Receive Notifications
- Suspend / Unsuspend User & Websites
- Manage Offers (Discount / Free Trial)
- Manage Plans & Pricing
- Send Notifications (Web / Mobile / Email)
- Monitor System (Pipeline, Health, Logs)
- Monitor Website Health
- Map Subdomain to Website
- Collect Monitoring Data
- View Subdomain

## Payment Service – (Subscription Plan)
- Review Order Summary (plan, price, billing period)
- Generate QR code
- User Scans QR & Pays in Banking App
- **◆** Is Payment Successful?
- Save Billing history
- Send Invoice to User's Email
- Activate Subscription (plan, start / end date, deploys left)
- Show Error (QR expired, cancelled, failed)
- **◆** Is Retrying Payment?
- Cancel & Return to Previous Step
- Monthly renewal: automatically remind user through email
- **◆** Is Upgrading from Dashboard?
- User Dashboard
- AI Website Generation
- Verify Transaction
- Click Generate New QR
- Payment Checkout

## Deployment

### Domain
- Domain Setup
- Setup Website name
- Show Input Fields
- Input Website Address
- **◆** Is Name Available?
- Use Website Address
- Set Address for Website

### Deployment
- Deployment
- AI Website Generation
- Ai generating code
- Push code to git server
- Deployment (CI/CD)
- Containerize app with Docker
- Set up resource and deploy
- Live Website
- **◆** Is Deploy Fail?
- **◆** Is Max Retry exceeded?
- Contact Admin
- Send email to admin
- **◆** Is First Deploy?
- Rollback to previous version
- Send email to admin
- Currently the max retry is 2 times but it can change.

## Notifications (System) – Website Owner, Admin V3
- Notifications
- Detect event
- **◆** Is Admin Event?
- **◆** Is it Urgent?
- Build update
- Urgent alert
- Web Notification
- Mobile Notification
- Email Notification
- WebBlock Website
- Notification Bell
- View notifications
- Mark as read
- Mark all as read
- **◆** Is App installed?
- Receive push
- Web / email only
- Receive email
- **◆** Is Critical?
- Platform update
- Critical incident
- Web Notification
- Email Notification
- Admin Portal
- Notification Bell
- View notifications
- Mark as read
- Mark all as read
- Receive email
- Marked message as read
- Marked all message as read
- Marked message as read
- Marked all message as read
- Web Notification
- Admin Portal
- Notification Bell
- View notifications
- Mark as read
- Mark all as read
- Marked message as read
- Marked all message as read

## Subscription Plan

### Subcription Plan
- Subscription Plan
- Show Plans
- Choose plan
- **◆** Is the plan active?
- **◆** Is Plan Different?
- Show Plan Change Dialog
- **◆** Is plan change?
- Click Confirm Change plan
- Click Close
- Payment Checkout
- Payment Checkout
- Dialog Closed
- Days Remaining + Extra Days
- Use New Plan
- Payment Checkout
- Days Remaining + Extra Days
