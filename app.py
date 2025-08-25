# Streamlit Home Loan vs Rent Simulator
# Save as streamlit_home_loan_simulator.py and run with: streamlit run streamlit_home_loan_simulator.py

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import base64

st.set_page_config(page_title="Home Loan vs Rent Simulator", layout="wide")

st.title("Home Loan vs Rent — Interactive Simulator")
st.markdown(
    "This simulator compares buying a house with a loan vs renting and investing the savings. "
    "It accounts for taxes (old/new/auto regimes), investments, house appreciation, rent increases, "
    "and configurable living expenses. Use the left panel to adjust assumptions."
)

# -------------------- Sidebar controls --------------------
st.sidebar.header("Main Assumptions")
HOUSE_PRICE = st.sidebar.number_input("House Price (INR)", value=50_00_000, step=50_000, format="%d")
LOAN_AMOUNT = st.sidebar.number_input("Loan Amount (INR)", value=45_00_000, step=50_000, format="%d")
LOAN_TENURE_YEARS = st.sidebar.slider("Loan Tenure (years)", 1, 30, 7)
LOAN_INTEREST_PERCENT = st.sidebar.slider("Loan Interest (annual %)", 0.0, 20.0, 12.0, step=0.1)

# Automatically calculate down payment
down_payment = max(0, HOUSE_PRICE - LOAN_AMOUNT)
st.sidebar.markdown(f"**Down Payment Required:** ₹{down_payment:,.0f}")

st.sidebar.markdown("---")
st.sidebar.header("Rent Scenario Assumptions")
INITIAL_MONTHLY_RENT = st.sidebar.number_input("Initial Monthly Rent (INR)", value=15_000, step=1_000, format="%d")
RENT_INCREMENT_PERCENT = st.sidebar.slider("Rent Increment (% p.a.)", 0.0, 15.0, 12.0, step=0.1)

st.sidebar.markdown("---")
INVESTMENT_RETURN_CAGR = st.sidebar.slider("Investment Return (CAGR %)", 0.0, 25.0, 10.5, step=0.1)
INFLATION_PERCENT_PER_YEAR = st.sidebar.slider("House Appreciation (% p.a.)", 0.0, 15.0, 6.5, step=0.1)

st.sidebar.markdown("---")
EMPLOYEE_SALARY_CURRENT = st.sidebar.number_input("Current Annual Gross Salary (INR)", value=14_50_000, step=50_000, format="%d")
EMPLOYEE_SALARY_INCREMENT_PERCENT_PA = st.sidebar.slider("Salary Growth (% p.a.)", 0.0, 30.0, 10.0, step=0.1)
INITIAL_CASH = st.sidebar.number_input("Initial Cash Available (INR)", value=0, step=50_000, format="%d")
EXPENSE_PERCENT = st.sidebar.slider("Living Expenses (% of net salary)", 0, 100, 50)

st.sidebar.markdown("---")
st.sidebar.header("Tax Parameters")
tax_regime = st.sidebar.selectbox("Tax Regime", ["Old", "New", "Auto (choose lower)"], index=2)
INTEREST_DEDUCTION_CAP_ANNUAL = st.sidebar.number_input("Interest Ded Cap (Sec 24b, old only)", value=2_00_000, step=10_000, format="%d")
HEALTH_EDU_CESS = st.sidebar.number_input("Health & Edu Cess (fraction)", value=0.04, step=0.01, format="%.2f")
YEARS = st.sidebar.slider("Simulation Years", 1, 40, 7)

st.sidebar.markdown("---")
st.sidebar.header("Additional Deductions (Old Regime Only)")
OTHER_80C_ANNUAL = st.sidebar.number_input("Other 80C Annual (PPF, ELSS etc.)", value=0, step=10_000, format="%d")
NPS_EMPLOYEE_80CCD1B = st.sidebar.number_input("NPS Employee Addl (80CCD1B)", value=0, step=5_000, format="%d", max_value=50_000)
NPS_EMPLOYER_PERCENT = st.sidebar.slider("NPS Employer % of Salary (80CCD2)", 0.0, 14.0, 0.0, step=0.1)
HEALTH_80D = st.sidebar.number_input("80D Health Premium", value=0, step=5_000, format="%d", max_value=25_000)
HRA_EXEMPTION_ANNUAL = st.sidebar.number_input("HRA Exemption Annual (for rent scenario)", value=0, step=10_000, format="%d")
disability_options = {"None": 0, "Normal (₹75,000)": 75_000, "Severe (₹1,25,000)": 1_25_000}
DISABILITY_80U = st.sidebar.selectbox("80U Disability Deduction", list(disability_options.keys()))

st.sidebar.markdown("---")
if st.sidebar.button("Reset Defaults"):
    st.rerun()

# Hardcoded constants
STANDARD_DEDUCTION_OLD = 50_000
STANDARD_DEDUCTION_NEW = 75_000
SEC_80C_CAP = 1_50_000
SEC_80CCD1B_CAP = 50_000
SEC_80D_CAP = 25_000  # Basic limit
SEC_80CCD2_CAP_PERCENT = 10.0  # Private sector limit, govt 14%

# -------------------- Helper functions --------------------
@st.cache_data
def monthly_emi(principal, annual_rate_percent, tenure_months):
    r = annual_rate_percent / 100.0 / 12.0
    n = tenure_months
    if r == 0:
        return principal / n
    emi = principal * r * (1 + r)**n / ((1 + r)**n - 1)
    return emi

@st.cache_data
def amortization_schedule(principal, annual_rate_percent, tenure_months):
    emi = monthly_emi(principal, annual_rate_percent, tenure_months)
    balance = principal
    schedule = []
    r = annual_rate_percent / 100.0 / 12.0
    for month in range(1, tenure_months + 1):
        interest = balance * r
        principal_paid = emi - interest
        balance -= principal_paid
        if balance < 1e-8:
            balance = 0.0
        schedule.append({
            "month": month,
            "emi": emi,
            "interest": interest,
            "principal_paid": principal_paid,
            "balance": balance
        })
    return pd.DataFrame(schedule)

@st.cache_data
def compute_annual_tax(taxable_income, slabs, regime, cess=0.04):
    base_tax = 0.0
    remaining = taxable_income
    for slab_amount, rate in slabs:
        taxable = min(slab_amount, remaining)
        base_tax += max(0.0, taxable) * rate
        remaining -= taxable
        if remaining <= 0:
            break

    # Rebate u/s 87A
    if regime == "old":
        if taxable_income <= 5_00_000:
            rebate = min(base_tax, 12_500)
            base_tax -= rebate
    else:  # new
        if taxable_income <= 7_00_000:
            rebate = min(base_tax, 25_000)
            base_tax -= rebate

    base_tax = max(base_tax, 0.0)

    # Surcharge
    surcharge_rate = 0.0
    if taxable_income > 50_00_000:
        if regime == "old":
            if taxable_income > 5_00_00_000:
                surcharge_rate = 0.37
            elif taxable_income > 2_00_00_000:
                surcharge_rate = 0.25
            elif taxable_income > 1_00_00_000:
                surcharge_rate = 0.15
            else:
                surcharge_rate = 0.10
        else:  # new
            if taxable_income > 2_00_00_000:
                surcharge_rate = 0.25
            elif taxable_income > 1_00_00_000:
                surcharge_rate = 0.15
            else:
                surcharge_rate = 0.10
    surcharge = base_tax * surcharge_rate

    total_tax = base_tax + surcharge
    tax_with_cess = total_tax * (1 + cess)
    return tax_with_cess

# Tax slabs for AY 2025-26
TAX_SLABS_OLD = [
    (2_50_000, 0.0),
    (2_50_000, 0.05),
    (5_00_000, 0.20),
    (1e12, 0.30)
]

TAX_SLABS_NEW = [
    (3_00_000, 0.0),
    (4_00_000, 0.05),
    (3_00_000, 0.10),
    (2_00_000, 0.15),
    (3_00_000, 0.20),
    (1e12, 0.30)
]

# -------------------- Compute amortization & yearly aggregates --------------------
tenure_months = LOAN_TENURE_YEARS * 12
am_table = amortization_schedule(LOAN_AMOUNT, LOAN_INTEREST_PERCENT, tenure_months)

rows = []
tax_details = []
annual_salary = EMPLOYEE_SALARY_CURRENT
investment_value_loan = max(0.0, INITIAL_CASH - down_payment)
investment_value_rent = INITIAL_CASH
house_value = HOUSE_PRICE

disability_ded = disability_options[DISABILITY_80U]

for y in range(YEARS):
    # Amortization slice
    start = y * 12
    end = min((y + 1) * 12, len(am_table))
    if start >= len(am_table):
        df_slice = pd.DataFrame(columns=am_table.columns)
    else:
        df_slice = am_table.iloc[start:end]
    interest_y = float(df_slice['interest'].sum()) if not df_slice.empty else 0.0
    principal_y = float(df_slice['principal_paid'].sum()) if not df_slice.empty else 0.0
    emi_y = float(df_slice['emi'].sum()) if not df_slice.empty else 0.0

    # Annual rent
    annual_rent = INITIAL_MONTHLY_RENT * 12 * (1 + RENT_INCREMENT_PERCENT / 100) ** y

    # Common deductions
    ded_80ccd1b = min(SEC_80CCD1B_CAP, NPS_EMPLOYEE_80CCD1B)
    ded_80ccd2 = min(SEC_80CCD2_CAP_PERCENT / 100 * annual_salary, NPS_EMPLOYER_PERCENT / 100 * annual_salary)
    ded_80d = min(SEC_80D_CAP, HEALTH_80D)
    ded_80u = disability_ded

    # Loan scenario tax (old regime)
    hra_loan = 0
    interest_ded = min(interest_y, INTEREST_DEDUCTION_CAP_ANNUAL)
    principal_ded = principal_y
    ded_80c_loan = min(SEC_80C_CAP, OTHER_80C_ANNUAL + principal_ded)
    chvia_ded_loan_old = ded_80c_loan + ded_80ccd1b + ded_80ccd2 + ded_80d + ded_80u
    salary_income_loan_old = annual_salary - hra_loan - STANDARD_DEDUCTION_OLD
    house_prop_loan = -interest_ded
    gross_total_loan_old = salary_income_loan_old + house_prop_loan
    taxable_loan_old = max(0.0, gross_total_loan_old - chvia_ded_loan_old)
    tax_loan_old = compute_annual_tax(taxable_loan_old, TAX_SLABS_OLD, "old", HEALTH_EDU_CESS)

    # Rent scenario tax (old regime)
    hra_rent = HRA_EXEMPTION_ANNUAL
    interest_ded_rent = 0
    principal_ded_rent = 0
    ded_80c_rent = min(SEC_80C_CAP, OTHER_80C_ANNUAL + principal_ded_rent)
    chvia_ded_rent_old = ded_80c_rent + ded_80ccd1b + ded_80ccd2 + ded_80d + ded_80u
    salary_income_rent_old = annual_salary - hra_rent - STANDARD_DEDUCTION_OLD
    house_prop_rent = 0
    gross_total_rent_old = salary_income_rent_old + house_prop_rent
    taxable_rent_old = max(0.0, gross_total_rent_old - chvia_ded_rent_old)
    tax_rent_old = compute_annual_tax(taxable_rent_old, TAX_SLABS_OLD, "old", HEALTH_EDU_CESS)

    # New regime (same for both scenarios, no HRA, no interest/principal)
    hra_new = 0
    house_prop_new = 0
    ded_80c_new = 0
    ded_80ccd1b_new = 0
    ded_80d_new = 0
    ded_80u_new = 0
    chvia_ded_new = ded_80ccd2  # Only 80CCD(2)
    salary_income_new = annual_salary - hra_new - STANDARD_DEDUCTION_NEW
    gross_total_new = salary_income_new + house_prop_new
    taxable_new = max(0.0, gross_total_new - chvia_ded_new)
    tax_new = compute_annual_tax(taxable_new, TAX_SLABS_NEW, "new", HEALTH_EDU_CESS)

    # Select tax based on regime
    if tax_regime == "Old":
        tax_loan = tax_loan_old
        tax_rent = tax_rent_old
        selected_regime_loan = "Old"
        selected_regime_rent = "Old"
    elif tax_regime == "New":
        tax_loan = tax_new
        tax_rent = tax_new
        selected_regime_loan = "New"
        selected_regime_rent = "New"
    else:  # Auto
        tax_loan = min(tax_loan_old, tax_new)
        tax_rent = min(tax_rent_old, tax_new)
        selected_regime_loan = "Old" if tax_loan == tax_loan_old else "New"
        selected_regime_rent = "Old" if tax_rent == tax_rent_old else "New"

    # Investable amounts
    in_hand_loan = annual_salary - tax_loan
    expenses_loan = (EXPENSE_PERCENT / 100.0) * in_hand_loan
    remaining_loan = in_hand_loan - expenses_loan
    investable_loan = max(0.0, remaining_loan - emi_y)

    in_hand_rent = annual_salary - tax_rent
    expenses_rent = (EXPENSE_PERCENT / 100.0) * in_hand_rent
    remaining_rent = in_hand_rent - expenses_rent
    investable_rent = max(0.0, remaining_rent - annual_rent)

    # Grow investments and house
    investment_value_loan = investment_value_loan * (1 + INVESTMENT_RETURN_CAGR / 100.0) + investable_loan
    investment_value_rent = investment_value_rent * (1 + INVESTMENT_RETURN_CAGR / 100.0) + investable_rent
    house_value = house_value * (1 + INFLATION_PERCENT_PER_YEAR / 100.0)

    # Loan balance
    if (y + 1) * 12 - 1 < len(am_table):
        balance = float(am_table.iloc[(y + 1) * 12 - 1]['balance'])
    else:
        balance = 0.0

    net_worth_loan = investment_value_loan + house_value - balance
    net_worth_rent = investment_value_rent

    rows.append({
        'year': y + 1,
        'annual_salary': annual_salary,
        'interest_paid': interest_y,
        'principal_paid': principal_y,
        'emi_paid': emi_y,
        'annual_rent': annual_rent,
        'tax_loan': tax_loan,
        'tax_rent': tax_rent,
        'investable_loan': investable_loan,
        'investable_rent': investable_rent,
        'investment_value_loan': investment_value_loan,
        'investment_value_rent': investment_value_rent,
        'house_value': house_value,
        'loan_outstanding_balance_end_of_year': balance,
        'net_worth_loan': net_worth_loan,
        'net_worth_rent': net_worth_rent,
        'selected_regime_loan': selected_regime_loan,
        'selected_regime_rent': selected_regime_rent
    })

    # Tax details
    # Loan Old
    tax_details.append({
        'year': y + 1,
        'scenario': 'Loan',
        'regime': 'Old',
        'gross_salary': annual_salary,
        'standard_deduction': STANDARD_DEDUCTION_OLD,
        'hra_exemption': hra_loan,
        'house_property_loss': house_prop_loan,
        'sec_80c': ded_80c_loan,
        'sec_80ccd1b': ded_80ccd1b,
        'sec_80ccd2': ded_80ccd2,
        'sec_80d': ded_80d,
        'sec_80u': ded_80u,
        'total_chapter_via_deductions': chvia_ded_loan_old,
        'gross_total_income': gross_total_loan_old,
        'taxable_income': taxable_loan_old,
        'tax_amount': tax_loan_old
    })

    # Rent Old
    tax_details.append({
        'year': y + 1,
        'scenario': 'Rent',
        'regime': 'Old',
        'gross_salary': annual_salary,
        'standard_deduction': STANDARD_DEDUCTION_OLD,
        'hra_exemption': hra_rent,
        'house_property_loss': house_prop_rent,
        'sec_80c': ded_80c_rent,
        'sec_80ccd1b': ded_80ccd1b,
        'sec_80ccd2': ded_80ccd2,
        'sec_80d': ded_80d,
        'sec_80u': ded_80u,
        'total_chapter_via_deductions': chvia_ded_rent_old,
        'gross_total_income': gross_total_rent_old,
        'taxable_income': taxable_rent_old,
        'tax_amount': tax_rent_old
    })

    # Loan New
    tax_details.append({
        'year': y + 1,
        'scenario': 'Loan',
        'regime': 'New',
        'gross_salary': annual_salary,
        'standard_deduction': STANDARD_DEDUCTION_NEW,
        'hra_exemption': hra_new,
        'house_property_loss': house_prop_new,
        'sec_80c': ded_80c_new,
        'sec_80ccd1b': ded_80ccd1b_new,
        'sec_80ccd2': ded_80ccd2,
        'sec_80d': ded_80d_new,
        'sec_80u': ded_80u_new,
        'total_chapter_via_deductions': chvia_ded_new,
        'gross_total_income': gross_total_new,
        'taxable_income': taxable_new,
        'tax_amount': tax_new
    })

    # Rent New (same as Loan New)
    tax_details.append({
        'year': y + 1,
        'scenario': 'Rent',
        'regime': 'New',
        'gross_salary': annual_salary,
        'standard_deduction': STANDARD_DEDUCTION_NEW,
        'hra_exemption': hra_new,
        'house_property_loss': house_prop_new,
        'sec_80c': ded_80c_new,
        'sec_80ccd1b': ded_80ccd1b_new,
        'sec_80ccd2': ded_80ccd2,
        'sec_80d': ded_80d_new,
        'sec_80u': ded_80u_new,
        'total_chapter_via_deductions': chvia_ded_new,
        'gross_total_income': gross_total_new,
        'taxable_income': taxable_new,
        'tax_amount': tax_new
    })

    # Salary increment
    annual_salary *= (1 + EMPLOYEE_SALARY_INCREMENT_PERCENT_PA / 100.0)

# DataFrames
df = pd.DataFrame(rows)
df_tax = pd.DataFrame(tax_details)

# -------------------- Display key results --------------------
col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("Net Worth Evolution")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['year'], y=df['net_worth_loan'], mode='lines+markers', name='Net Worth (Loan)'))
    fig.add_trace(go.Scatter(x=df['year'], y=df['net_worth_rent'], mode='lines+markers', name='Net Worth (Rent)'))
    fig.update_layout(title='Net Worth Over Time (Loan vs Rent)', xaxis_title='Year', yaxis_title='INR', legend=dict(x=0.02, y=0.98))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Detailed Components (select years)")
    show_years = st.slider("Select years to show in table", 1, YEARS, (1, min(10, YEARS)))
    st.dataframe(df.loc[df['year'].between(show_years[0], show_years[1])].reset_index(drop=True))

    st.subheader("Detailed Tax Breakdown (All Regimes)")
    st.dataframe(df_tax)

with col2:
    st.subheader("Summary (End of Simulation)")
    final = df.iloc[-1]
    st.metric("Net Worth (Loan)", f"₹{final['net_worth_loan']:,.0f}")
    st.metric("Net Worth (Rent)", f"₹{final['net_worth_rent']:,.0f}")
    st.metric("Down Payment", f"₹{down_payment:,.0f}")
    st.metric("Final Loan Outstanding", f"₹{final['loan_outstanding_balance_end_of_year']:,.0f}")
    st.write("\n")
    st.write("**Totals Over Period**")
    total_emi = df['emi_paid'].sum()
    total_interest = df['interest_paid'].sum()
    total_principal = df['principal_paid'].sum()
    total_tax_loan = df['tax_loan'].sum()
    total_tax_rent = df['tax_rent'].sum()
    tax_saved_by_loan = total_tax_rent - total_tax_loan
    total_rent = df['annual_rent'].sum()
    st.write(f"Total EMI Paid over {YEARS} yrs: ₹{total_emi:,.0f}")
    st.write(f"Total Interest Paid over {YEARS} yrs: ₹{total_interest:,.0f}")
    st.write(f"Total Principal Paid over {YEARS} yrs: ₹{total_principal:,.0f}")
    st.write(f"Total Rent Paid over {YEARS} yrs: ₹{total_rent:,.0f}")
    st.write(f"Total Tax Paid (Loan): ₹{total_tax_loan:,.0f}")
    st.write(f"Total Tax Paid (Rent): ₹{total_tax_rent:,.0f}")
    st.write(f"Tax Saved by Loan: ₹{tax_saved_by_loan:,.0f}" if tax_saved_by_loan > 0 else f"Extra Tax in Loan: ₹{-tax_saved_by_loan:,.0f}")

# -------------------- Additional plots --------------------
st.markdown("---")
st.subheader("Breakdown Plot (Loan Scenario)")
fig2 = go.Figure()
fig2.add_trace(go.Bar(x=df['year'], y=df['investment_value_loan'], name='Investment (Loan)'))
fig2.add_trace(go.Bar(x=df['year'], y=df['house_value'], name='House Value'))
fig2.add_trace(go.Bar(x=df['year'], y=-df['loan_outstanding_balance_end_of_year'], name='Loan Outstanding (negative)'))
fig2.update_layout(barmode='stack', title='Loan Scenario: Investments, House Value, and Outstanding Loan', xaxis_title='Year')
st.plotly_chart(fig2, use_container_width=True)

# -------------------- Export / Download --------------------
st.markdown("---")
st.subheader("Export")
csv = df.to_csv(index=False)
b64 = base64.b64encode(csv.encode()).decode()
href = f'<a href="data:text/csv;base64,{b64}" download="loan_vs_rent_yearly.csv">Download Year-by-Year CSV</a>'
st.markdown(href, unsafe_allow_html=True)

tax_csv = df_tax.to_csv(index=False)
tax_b64 = base64.b64encode(tax_csv.encode()).decode()
tax_href = f'<a href="data:text/csv;base64,{tax_b64}" download="tax_breakdown.csv">Download Tax Breakdown CSV</a>'
st.markdown(tax_href, unsafe_allow_html=True)

st.info("Tip: Change parameters on the left; charts and table update automatically. INITIAL_CASH is the starting cash balance, used for down payment in loan or fully invested in rent scenario.")

# -------------------- Footer --------------------
st.markdown("---")
st.caption("Model Notes: \n1) Tax calculations are simplified for AY 2025-26, including slabs, rebates, surcharges, and cess; does not cover all nuances or additional incomes. \n2) Deductions apply only in old regime except 80CCD(2). \n3) Investments grow at constant CAGR with end-of-year contributions. \n4) House appreciation and rent increases are annual constants. \n5) EMI/rent constraints: Investable set to max(0, remaining after expenses - EMI/rent). \n6) This is for educational comparison, not financial advice.")