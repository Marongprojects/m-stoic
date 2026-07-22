# 🤝 Lead & Referral Automation System

A comprehensive Streamlit-based platform for managing leads, referrals, and commissions with role-based access control.

## 📋 Features

- **Public Lead Signup**: Register new leads with automatic marketer assignment
- **Consultation Activation**: POP upload to unlock premium features and trigger commissions
- **Marketer Dashboard**: View referred leads, track commissions, submit inquiries and update requests
- **Admin Dashboard**: Comprehensive analytics, lead management, and support ticket handling
- **Real-time Commission Tracking**: Automatic calculation and balance updates
- **Data Privacy**: Role-based access with data isolation per marketer

## 🚀 Quick Start

### Local Development
```bash
# Clone the repository
git clone https://github.com/marongprojects/leads-system.git
cd leads-system

# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run app.py
```

The app will be available at `http://localhost:8501`

### Deploy to Streamlit Cloud
1. Push your code to GitHub (repository already configured)
2. Visit [share.streamlit.io](https://share.streamlit.io)
3. Click "New app"
4. Select:
   - **GitHub account**: marongprojects
   - **Repository**: leads-system
   - **Branch**: main
   - **File path**: app.py
5. Deploy!

**Live App**: https://marongprojects-leads-system.streamlit.app

## 📊 User Roles

### 1. **Public Lead Signup**
- Register with name, email, and marketer referral
- Automatic Lead ID generation (M###-L###)
- Join WhatsApp community
- Optional consultation activation

### 2. **POP Upload (Consultation Activation)**
- Upload Proof of Payment
- Unlock premium account features
- Triggers R100 commission to marketer

### 3. **Marketer Dashboard**
- View personal leads and statistics
- Track commission balance
- Monitor milestone progress (100 joins = R500 bonus)
- Submit inquiries and update requests
- View support history

### 4. **System Admin**
- **Overview**: Platform metrics and leaderboard
- **Marketer Details**: Individual analysis and lead tracking
- **Support Inquiries**: Manage and respond to support tickets
- **Data Updates**: Approve or reject lead data modifications
- **Master Database**: View all system data

## 💰 Commission Structure

- **Lead Registration**: Automatic join count increment
- **Consultation Activation (POP Upload)**: R100 per lead
- **Milestone Bonus**: R500 at 100 joins per marketer

## 🔐 Data Security

- Session-based authentication
- Role-based access control (RBAC)
- Data isolation per marketer
- No cross-marketer data visibility

## 📁 Project Structure

```
leads-system/
├── app.py              # Main Streamlit application
├── requirements.txt    # Python dependencies
├── .gitignore         # Git ignore rules
└── README.md          # This file
```

## 🛠️ Technology Stack

- **Frontend**: Streamlit 1.41.1
- **Data**: Pandas
- **Backend**: Python
- **Deployment**: Streamlit Cloud

## 📧 Support

For issues or questions, use the Support & Inquiries section in the Marketer Dashboard or contact the admin at marongprojects@gmail.com

## 📝 License

This project is proprietary and confidential.

---

**Repository**: https://github.com/marongprojects/leads-system  
**Deployed App**: https://marongprojects-leads-system.streamlit.app
