import streamlit as st
import pandas as pd

st.set_page_config(page_title="Lead & Referral Automation", layout="wide")

# Display brand logo in sidebar
st.logo("vezi logo.jpeg")

# 1. System Database Initialization
if 'database' not in st.session_state:
    st.session_state.database = {
        'leads': [
            {"Lead ID": "M001-L001", "Name": "Tshepo", "Email": "tshepo@mail.com", "Marketer ID": "M001", "Status": "consultation_paid", "POP Uploaded": True},
            {"Lead ID": "M001-L002", "Name": "John Smith", "Email": "john.smith@mail.com", "Marketer ID": "M001", "Status": "consultation_paid", "POP Uploaded": True},
            {"Lead ID": "M001-L003", "Name": "Emma Johnson", "Email": "emma.j@mail.com", "Marketer ID": "M001", "Status": "registered", "POP Uploaded": False},
            {"Lead ID": "M001-L004", "Name": "Michael Brown", "Email": "m.brown@mail.com", "Marketer ID": "M001", "Status": "consultation_paid", "POP Uploaded": True},
            {"Lead ID": "M001-L005", "Name": "Sarah Davis", "Email": "sarah.d@mail.com", "Marketer ID": "M001", "Status": "registered", "POP Uploaded": False},
            {"Lead ID": "M001-L006", "Name": "James Wilson", "Email": "james.w@mail.com", "Marketer ID": "M001", "Status": "consultation_paid", "POP Uploaded": True},
            {"Lead ID": "M001-L007", "Name": "Lisa Anderson", "Email": "lisa.a@mail.com", "Marketer ID": "M001", "Status": "consultation_paid", "POP Uploaded": True},
            {"Lead ID": "M002-L008", "Name": "Robert Taylor", "Email": "robert.t@mail.com", "Marketer ID": "M002", "Status": "registered", "POP Uploaded": False},
            {"Lead ID": "M002-L009", "Name": "Jennifer White", "Email": "jen.white@mail.com", "Marketer ID": "M002", "Status": "consultation_paid", "POP Uploaded": True},
            {"Lead ID": "M002-L010", "Name": "David Harris", "Email": "david.h@mail.com", "Marketer ID": "M002", "Status": "registered", "POP Uploaded": False},
            {"Lead ID": "M002-L011", "Name": "Patricia Martin", "Email": "patricia.m@mail.com", "Marketer ID": "M002", "Status": "consultation_paid", "POP Uploaded": True},
            {"Lead ID": "M002-L012", "Name": "Christopher Lee", "Email": "chris.lee@mail.com", "Marketer ID": "M002", "Status": "consultation_paid", "POP Uploaded": True},
            {"Lead ID": "M002-L013", "Name": "Mary Thompson", "Email": "mary.t@mail.com", "Marketer ID": "M002", "Status": "registered", "POP Uploaded": False}
        ],
        'marketers': {
            "M001": {"Name": "Sarah", "Joins": 7, "Balance": 900.0, "Bonus Earned": False},
            "M002": {"Name": "David", "Joins": 6, "Balance": 500.0, "Bonus Earned": False}
        },
        'lead_counter': 13,
        'inquiries': [],  # New: Store inquiries from marketers
        'update_requests': [],  # New: Store data update requests
        'logged_in_marketer': None  # Track currently logged-in marketer
    }

db = st.session_state.database

# ========== BRANDED HEADER WITH LOGO ==========
col1, col2 = st.columns([1, 4])
with col1:
    st.image("vezi logo.jpeg", width=120)
with col2:
    st.markdown("""
    <div style='padding-top: 15px;'>
        <h1 style='margin: 0; color: #001f3f; font-size: 2.5em;'>Lead & Referral Automation</h1>
        <p style='margin: 0; color: #666; font-size: 1.1em;'>Empowering Your Growth</p>
    </div>
    """, unsafe_allow_html=True)

st.divider()

# ========== MAIN NAVIGATION ==========
st.sidebar.header("🔐 Access Portal")
user_role = st.sidebar.selectbox("Select User Role", ["Public Lead Signup", "POP Upload", "Marketer Dashboard", "System Admin"])

# ==========================================
# ROLE 1: PUBLIC LEAD SIGNUP
# ==========================================
if user_role == "Public Lead Signup":
    st.header("📩 New Lead Registration")
    
    with st.form("signup_form", clear_on_submit=True):
        name = st.text_input("Full Name")
        email = st.text_input("Email Address")
        
        # Create mapping display for Marketers
        m_display = {k: f"{v['Name']} ({k})" for k, v in db['marketers'].items()}
        selected_m_id = st.selectbox("Who referred you? (Marketer)", list(m_display.keys()), format_func=lambda x: m_display[x])
        
        submit = st.form_submit_button("Register Now")
        
        if submit and name and email:
            db['lead_counter'] += 1
            new_id = f"{selected_m_id}-L{db['lead_counter']:03d}"
            
            # Save new lead structure
            db['leads'].append({
                "Lead ID": new_id, "Name": name, "Email": email, 
                "Marketer ID": selected_m_id, "Status": "registered", "POP Uploaded": False
            })
            
            # Increment Marketer channel join count automatically
            db['marketers'][selected_m_id]['Joins'] += 1
            
            # Check for Milestone Bonus (100 joins = R500)
            if db['marketers'][selected_m_id]['Joins'] >= 100 and not db['marketers'][selected_m_id]['Bonus Earned']:
                db['marketers'][selected_m_id]['Balance'] += 500.0
                db['marketers'][selected_m_id]['Bonus Earned'] = True
                
            st.success(f"🎉 Registration Successful! Your Lead ID is: {new_id}")
            st.balloons()
            
            # 🔥 PRIORITY: WhatsApp Registration (Immediate)
            st.markdown("---")
            st.markdown("### 🚀 JOIN OUR WHATSAPP COMMUNITY NOW")
            st.info("✨ **This is the final step of your registration!** Join our exclusive WhatsApp channel to stay connected with updates, support, and community benefits.")
            st.link_button("📢 Join Our WhatsApp Channel", "https://whatsapp.com", use_container_width=True)

    # Post-Registration: Optional Consultation Activation
    st.markdown("### 💡 Optional: Activate Consultation (Upgrade)")
    st.info("Load your proof of payment to activate consultation")

# ==========================================
# ROLE 1.5: POP UPLOAD (CONSULTATION ACTIVATION)
# ==========================================
elif user_role == "POP Upload":
    st.header("🎓 Activate Consultation Account")
    st.caption("Upload your Proof of Payment (POP) to unlock your consultation account and activate your referrer's commission.")
    
    st.warning("📌 **Consultation Activation** — This upgrade activates your full account features and immediately credits your marketer's commission (R100).", icon="⚡")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        pop_lead_id = st.text_input("Your Lead ID:", placeholder="e.g., M001-L001")
    
    with col2:
        uploaded_file = st.file_uploader("Upload POP (Receipt / Screenshot / PDF)", type=["png", "jpg", "jpeg", "pdf"])
    
    if st.button("Activate Consultation Now", type="primary", use_container_width=True):
        if pop_lead_id and uploaded_file:
            found = False
            for lead in db['leads']:
                if lead['Lead ID'] == pop_lead_id:
                    found = True
                    if not lead['POP Uploaded']:
                        lead['POP Uploaded'] = True
                        lead['Status'] = "consultation_paid"
                        # Trigger Marketer Commission (R100)
                        m_id = lead['Marketer ID']
                        db['marketers'][m_id]['Balance'] += 100.0
                        
                        st.success("✅ Consultation Account Activated!")
                        st.balloons()
                        st.markdown("---")
                        st.markdown("### 🎉 Welcome to Your Consultation Account!")
                        st.info(f"✨ Your account is now fully active with premium features. Your marketer has received their R100 commission for referring you.")
                        st.caption("📧 Confirmation Email: Sent to Admin (marongprojects@gmail.com) and your Marketer.")
                    else:
                        st.warning("⚠️ This account has already been activated.")
            if not found:
                st.error("❌ Invalid Lead ID. Please check and try again.")
        else:
            st.error("❌ Please provide both your Lead ID and upload a file.")
    
    st.divider()
    st.markdown("### ❓ Questions?")
    st.caption("If you need help or have questions, please contact your marketer or submit an inquiry through the Marketer Dashboard.")

# ==========================================
# ROLE 2: MARKETER DASHBOARD
# ==========================================
elif user_role == "Marketer Dashboard":
    # Marketer Login System (in sidebar)
    st.sidebar.divider()
    st.sidebar.subheader("🔑 Marketer Login")
    
    if db['logged_in_marketer'] is None:
        # Show login screen
        st.info("👉 Please log in with your Marketer ID in the sidebar to access your dashboard.")
        m_id_selected = st.sidebar.selectbox("Select Your Marketer ID:", list(db['marketers'].keys()), format_func=lambda x: f"{db['marketers'][x]['Name']} ({x})")
        if st.sidebar.button("Log In"):
            db['logged_in_marketer'] = m_id_selected
            st.rerun()
    else:
        # Show logged-in marketer's info
        m_id_login = db['logged_in_marketer']
        m_info = db['marketers'][m_id_login]
        st.sidebar.success(f"✅ Logged in as: **{m_info['Name']}** ({m_id_login})")
        if st.sidebar.button("Log Out"):
            db['logged_in_marketer'] = None
            st.rerun()
    
    # Only show dashboard if logged in
    if db['logged_in_marketer'] is not None:
        m_id_login = db['logged_in_marketer']
        m_info = db['marketers'][m_id_login]
        
        # Branded header for dashboard
        col1, col2 = st.columns([1, 4])
        with col1:
            st.image("vezi logo.jpeg", width=100)
        with col2:
            st.markdown("<h2 style='color: #001f3f; margin-top: 20px;'>Marketer Portal</h2>", unsafe_allow_html=True)
        st.divider()
        
        st.header("📊 Performance Metrics")
        st.subheader(f"Welcome back, {m_info['Name']}!")
        
        # Key Performance Indicators
        col1, col2, col3 = st.columns(3)
        col1.metric("My Total Joins", f"{m_info['Joins']} / 100")
        col2.metric("Total Commission Balance", f"R {m_info['Balance']:.2f}")
        col3.metric("Milestone Bonus Status", "🥇 Achieved (R500)" if m_info['Bonus Earned'] else "⏳ In Progress")
        
        # Filtered Leads View (Privacy Control)
        st.subheader("📋 My Referred Leads")
        my_leads = [l for l in db['leads'] if l['Marketer ID'] == m_id_login]
        if my_leads:
            st.dataframe(pd.DataFrame(my_leads), use_container_width=True)
        else:
            st.info("No leads registered under your code yet.")
        
        # Customer Support & Inquiries Section
        st.divider()
        st.subheader("💬 Support & Inquiries Center")
        
        tab1, tab2 = st.tabs(["Submit Inquiry", "Submit Data Update Request"])
        
        with tab1:
            st.markdown("### 📧 Submit an Inquiry")
            st.caption("Have a question or need assistance? Submit your inquiry below.")
            
            inquiry_subject = st.text_input("Subject", placeholder="e.g., Question about commission, Lead status, etc.")
            inquiry_message = st.text_area("Your Message", placeholder="Describe your inquiry in detail...", height=150)
            
            if st.button("Submit Inquiry", key="submit_inquiry"):
                if inquiry_subject and inquiry_message:
                    inquiry = {
                        "ID": f"INQ-{len(db['inquiries']) + 1:03d}",
                        "Marketer ID": m_id_login,
                        "Marketer Name": m_info['Name'],
                        "Subject": inquiry_subject,
                        "Message": inquiry_message,
                        "Status": "Open",
                        "Date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
                    }
                    db['inquiries'].append(inquiry)
                    st.success("✅ Your inquiry has been submitted successfully!")
                    st.caption(f"Inquiry ID: {inquiry['ID']} - You'll receive a response shortly.")
                else:
                    st.error("Please fill in both subject and message fields.")
        
        with tab2:
            st.markdown("### 📝 Request Data Update")
            st.caption("Notice an error in your lead data? Request an update below.")
            
            lead_id_update = st.text_input("Lead ID to Update", placeholder="e.g., M001-L001")
            update_field = st.selectbox("Field to Update", ["Status", "Email", "Phone", "Name", "Other"])
            update_details = st.text_area("Update Details", placeholder="Describe what needs to be updated...", height=120)
            
            if st.button("Submit Update Request", key="submit_update"):
                if lead_id_update and update_details:
                    # Check if lead exists
                    lead_exists = any(l['Lead ID'] == lead_id_update for l in db['leads'])
                    if lead_exists:
                        update_req = {
                            "ID": f"UPD-{len(db['update_requests']) + 1:03d}",
                            "Marketer ID": m_id_login,
                            "Marketer Name": m_info['Name'],
                            "Lead ID": lead_id_update,
                            "Field": update_field,
                            "Details": update_details,
                            "Status": "Pending",
                            "Date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
                        }
                        db['update_requests'].append(update_req)
                        st.success("✅ Update request submitted successfully!")
                        st.caption(f"Request ID: {update_req['ID']} - Admin will review and process shortly.")
                    else:
                        st.error(f"Lead ID '{lead_id_update}' not found. Please check and try again.")
                else:
                    st.error("Please fill in all required fields.")
        
        # View My Requests
        st.divider()
        st.subheader("📋 My Support History")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### 📧 My Inquiries")
            my_inquiries = [i for i in db['inquiries'] if i['Marketer ID'] == m_id_login]
            if my_inquiries:
                st.dataframe(pd.DataFrame(my_inquiries)[["ID", "Subject", "Status", "Date"]], use_container_width=True)
            else:
                st.info("No inquiries submitted yet.")
        
        with col2:
            st.markdown("#### 📝 My Update Requests")
            my_updates = [u for u in db['update_requests'] if u['Marketer ID'] == m_id_login]
            if my_updates:
                st.dataframe(pd.DataFrame(my_updates)[["ID", "Lead ID", "Field", "Status", "Date"]], use_container_width=True)
            else:
                st.info("No update requests submitted yet.")

# ==========================================
# ROLE 3: SYSTEM ADMIN
# ==========================================
elif user_role == "System Admin":
    # Branded header for admin dashboard
    col1, col2 = st.columns([1, 4])
    with col1:
        st.image("vezi logo.jpeg", width=100)
    with col2:
        st.markdown("<h2 style='color: #001f3f; margin-top: 20px;'>Executive Administration Dashboard</h2>", unsafe_allow_html=True)
    st.caption("Secure Management View for: marongprojects@gmail.com")
    st.divider()
    
    # Admin Tabs
    admin_tab1, admin_tab2, admin_tab3, admin_tab4 = st.tabs(["Overview", "Marketer Details", "Support Inquiries", "Data Update Requests"])
    
    with admin_tab1:
        # Overall Platform Performance Metrics
        total_revenue = len([l for l in db['leads'] if l['POP Uploaded']]) * 150
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total System Leads", len(db['leads']))
        col2.metric("Verified Paid Accounts", len([l for l in db['leads'] if l['POP Uploaded']]))
        col3.metric("Open Inquiries", len([i for i in db['inquiries'] if i['Status'] == 'Open']))
        col4.metric("Pending Updates", len([u for u in db['update_requests'] if u['Status'] == 'Pending']))
        
        st.subheader("👥 Marketer Leaderboard & Accounting Balance")
        st.dataframe(pd.DataFrame.from_dict(db['marketers'], orient='index'), use_container_width=True)
    
    with admin_tab2:
        # Detailed Marketer Analysis
        st.subheader("🔍 Detailed Marketer Analysis")
        selected_marketer = st.selectbox("Select Marketer to View Details:", list(db['marketers'].keys()), format_func=lambda x: f"{db['marketers'][x]['Name']} ({x})")
        
        if selected_marketer:
            m_data = db['marketers'][selected_marketer]
            
            # Marketer Details
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Marketer Name", m_data['Name'])
            col2.metric("Total Joins", f"{m_data['Joins']} / 100")
            col3.metric("Commission Balance", f"R {m_data['Balance']:.2f}")
            col4.metric("Bonus Status", "✅ Earned" if m_data['Bonus Earned'] else "⏳ Pending")
            
            # Marketer's Leads
            st.subheader(f"📋 Leads Referred by {m_data['Name']} ({selected_marketer})")
            marketer_leads = [l for l in db['leads'] if l['Marketer ID'] == selected_marketer]
            if marketer_leads:
                st.dataframe(pd.DataFrame(marketer_leads), use_container_width=True)
            else:
                st.info("No leads under this marketer yet.")
    
    with admin_tab3:
        # Manage Inquiries
        st.subheader("💬 Customer Inquiries Management")
        
        if db['inquiries']:
            inquiries_df = pd.DataFrame(db['inquiries'])
            st.dataframe(inquiries_df, use_container_width=True)
            
            # Filter inquiries by status
            inquiry_filter = st.radio("Filter by Status:", ["All", "Open", "In Progress", "Resolved"])
            filtered_inquiries = db['inquiries']
            if inquiry_filter != "All":
                filtered_inquiries = [i for i in db['inquiries'] if i['Status'] == inquiry_filter]
            
            if filtered_inquiries:
                st.subheader(f"Inquiries: {inquiry_filter}")
                for idx, inquiry in enumerate(filtered_inquiries):
                    with st.expander(f"📧 {inquiry['ID']} - {inquiry['Subject']} ({inquiry['Status']})"):
                        st.write(f"**From:** {inquiry['Marketer Name']} ({inquiry['Marketer ID']})")
                        st.write(f"**Date:** {inquiry['Date']}")
                        st.write(f"**Message:** {inquiry['Message']}")
                        
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            if st.button("Mark as In Progress", key=f"prog_{idx}"):
                                db['inquiries'][idx]['Status'] = 'In Progress'
                                st.rerun()
                        with col2:
                            if st.button("Mark as Resolved", key=f"res_{idx}"):
                                db['inquiries'][idx]['Status'] = 'Resolved'
                                st.rerun()
                        with col3:
                            response = st.text_input("Admin Response", key=f"resp_{idx}", placeholder="Type your response here...")
                            if st.button("Send Response", key=f"send_{idx}"):
                                st.info(f"✅ Response sent to {inquiry['Marketer Name']}")
        else:
            st.info("No inquiries received yet.")
    
    with admin_tab4:
        # Manage Update Requests
        st.subheader("📝 Data Update Requests Management")
        
        if db['update_requests']:
            updates_df = pd.DataFrame(db['update_requests'])
            st.dataframe(updates_df, use_container_width=True)
            
            # Filter by status
            update_filter = st.radio("Filter by Status:", ["All", "Pending", "Approved", "Rejected"])
            filtered_updates = db['update_requests']
            if update_filter != "All":
                filtered_updates = [u for u in db['update_requests'] if u['Status'] == update_filter]
            
            if filtered_updates:
                st.subheader(f"Update Requests: {update_filter}")
                for idx, update_req in enumerate(filtered_updates):
                    with st.expander(f"📝 {update_req['ID']} - Lead {update_req['Lead ID']} ({update_req['Status']})"):
                        st.write(f"**From:** {update_req['Marketer Name']} ({update_req['Marketer ID']})")
                        st.write(f"**Date:** {update_req['Date']}")
                        st.write(f"**Field to Update:** {update_req['Field']}")
                        st.write(f"**Details:** {update_req['Details']}")
                        
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            if st.button("Approve & Update", key=f"app_{idx}"):
                                db['update_requests'][idx]['Status'] = 'Approved'
                                st.success(f"✅ Update request approved!")
                                st.rerun()
                        with col2:
                            if st.button("Reject Request", key=f"rej_{idx}"):
                                db['update_requests'][idx]['Status'] = 'Rejected'
                                st.warning(f"❌ Update request rejected.")
                                st.rerun()
                        with col3:
                            note = st.text_input("Admin Note", key=f"note_{idx}", placeholder="Add internal note...")
        else:
            st.info("No update requests received yet.")
    
    st.divider()
    st.subheader("🗂️ Global Master Leads Database")
    if db['leads']:
        st.dataframe(pd.DataFrame(db['leads']), use_container_width=True)
    else:
        st.info("No system activity logged.")
    