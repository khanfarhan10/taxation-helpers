# Streamlit Home Loan vs Cash Simulator
# Save as streamlit_home_loan_simulator.py and run with: streamlit run streamlit_home_loan_simulator.py

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import plotly.graph_objects as go
import base64

st.set_page_config(page_title="Home Loan vs Cash Simulator", layout="wide")

st.title("Home Loan vs Cash — Interactive Simulator")
st.markdown(
    "Use the controls in the left panel to change assumptions. The app calculates amortization, taxes (configurable regime with surcharges),\n"
    "investment growth, house appreciation and shows year-by-year net-worth for both strategies (Loan vs Cash)."
)

LOAN_TENURE_YEARS_DEFAULT = 7
# -------------------- Sidebar controls --------------------
st.sidebar.header("Main assumptions")
LOAN_AMOUNT = st.sidebar.number_input("Loan amount (INR)", value=40_00_000, step=50_000, format="%d")
HOUSE_PRICE = st.sidebar.number_input("House price (INR)", value=50_00_000, step=50_000, format="%d")
LOAN_TENURE_YEARS = st.sidebar.slider("Loan tenure (years)", 1, 30, LOAN_TENURE_YEARS_DEFAULT)
LOAN_INTEREST_PERCENT = st.sidebar.slider("Loan interest (annual %)", 0.0, 20.0, 8.0, step=0.1)

# Automatically calculate down payment
down_payment = max(0, HOUSE_PRICE - LOAN_AMOUNT)

st.sidebar.markdown(f"**Down Payment required:** ₹{down_payment:,.0f}")

st.sidebar.markdown("---")
INVESTMENT_RETURN_CAGR = st.sidebar.slider("Investment return (CAGR %)", 0.0, 25.0, 10.5, step=0.1)
INFLATION_PERCENT_PER_YEAR = st.sidebar.slider("House inflation / appreciation (% p.a.)", 0.0, 15.0, 6.5, step=0.1)

st.sidebar.markdown("---")
EMPLOYEE_SALARY_CURRENT = st.sidebar.number_input("Current annual gross salary (INR)", value=14_50_000, step=50_000, format="%d")
EMPLOYEE_SALARY_INCREMENT_PERCENT_PA = st.sidebar.slider("Salary growth (% p.a.)", 0.0, 30.0, 10.0, step=0.1)
INITIAL_CASH = st.sidebar.number_input("Initial cash available (INR)", value=0, step=50_000, format="%d")

st.sidebar.markdown("---")
# Tax & deduction parameters
tax_regime = st.sidebar.selectbox("Tax Regime", ["Old", "New", "Auto (choose lower)"], index=2)
INTEREST_DEDUCTION_CAP_ANNUAL = st.sidebar.number_input("Interest deduction cap / year (Sec 24b, old regime only)", value=2_00_000, step=10_000, format="%d")
PRINCIPAL_DEDUCTION_CAP_80C = st.sidebar.number_input("80C cap / year (old regime only)", value=1_50_000, step=10_000, format="%d")
HEALTH_EDU_CESS = st.sidebar.number_input("Health & edu cess (fraction)", value=0.04, step=0.01, format="%.2f")
YEARS = st.sidebar.slider("Simulation years", 1, 40, max(LOAN_TENURE_YEARS, 20))

st.sidebar.markdown("---")
if st.sidebar.button("Reset defaults"):
    st.rerun()

# Hardcoded standard deductions per regime (updated for AY 2025-26)
STANDARD_DEDUCTION_OLD = 50_000
STANDARD_DEDUCTION_NEW = 75_000

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
    for month in range(1, tenure_months+1):
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

    # Apply rebate u/s 87A
    if regime == "old":
        if taxable_income <= 500000:
            rebate = min(base_tax, 12500)
            base_tax -= rebate
    else:  # new
        if taxable_income <= 700000:
            rebate = min(base_tax, 25000)
            base_tax -= rebate

    base_tax = max(base_tax, 0.0)

    # Surcharge
    surcharge_rate = 0.0
    if taxable_income > 5000000:
        if regime == "old":
            if taxable_income > 50000000:
                surcharge_rate = 0.37
            elif taxable_income > 20000000:
                surcharge_rate = 0.25
            elif taxable_income > 10000000:
                surcharge_rate = 0.15
            else:
                surcharge_rate = 0.10
        else:  # new
            if taxable_income > 20000000:
                surcharge_rate = 0.25
            elif taxable_income > 10000000:
                surcharge_rate = 0.15
            else:
                surcharge_rate = 0.10
    surcharge = base_tax * surcharge_rate

    # Total tax before cess
    total_tax = base_tax + surcharge

    # Cess
    tax_with_cess = total_tax * (1 + cess)
    return tax_with_cess

# Updated slabs for AY 2025-26 (resident under 60)
TAX_SLABS_OLD = [
    (250000, 0.0),
    (250000, 0.05),
    (500000, 0.20),
    (1e12, 0.30)  # Surcharge handled separately
]

TAX_SLABS_NEW = [
    (300000, 0.0),
    (400000, 0.05),  # 3L-7L
    (300000, 0.10),  # 7L-10L
    (200000, 0.15),  # 10L-12L
    (300000, 0.20),  # 12L-15L
    (1e12, 0.30)     # >15L, surcharge separate
]

# -------------------- Compute amortization & yearly aggregates --------------------
tenure_months = LOAN_TENURE_YEARS * 12
am_table = amortization_schedule(LOAN_AMOUNT, LOAN_INTEREST_PERCENT, tenure_months)

# Yearly aggregation
rows = []
months_per_year = 12
annual_salary = EMPLOYEE_SALARY_CURRENT
investment_value_loan = max(0.0, INITIAL_CASH - down_payment)  # Deduct down payment from cash in loan scenario
investment_value_cash = max(0.0, INITIAL_CASH - HOUSE_PRICE)  # Deduct full house price if cash purchase
house_value_loan = HOUSE_PRICE
house_value_cash = HOUSE_PRICE

emi_per_year_list = []
for y in range(0, YEARS):
    start = y*12
    end = min((y+1)*12, len(am_table))
    if start >= len(am_table):
        df_slice = pd.DataFrame(columns=am_table.columns)
    else:
        df_slice = am_table.iloc[start:end]
    interest_y = float(df_slice['interest'].sum()) if not df_slice.empty else 0.0
    principal_y = float(df_slice['principal_paid'].sum()) if not df_slice.empty else 0.0
    emi_y = float(df_slice['emi'].sum()) if not df_slice.empty else 0.0
    emi_per_year_list.append(emi_y)

    # Deductions (old regime only for loan-specific)
    interest_deduction = min(interest_y, INTEREST_DEDUCTION_CAP_ANNUAL)
    principal_deduction = min(principal_y, PRINCIPAL_DEDUCTION_CAP_80C)

    # Loan scenario taxes
    taxable_loan_old = max(0.0, annual_salary - STANDARD_DEDUCTION_OLD - interest_deduction - principal_deduction)
    tax_loan_old = compute_annual_tax(taxable_loan_old, TAX_SLABS_OLD, "old", HEALTH_EDU_CESS)
    taxable_loan_new = max(0.0, annual_salary - STANDARD_DEDUCTION_NEW)
    tax_loan_new = compute_annual_tax(taxable_loan_new, TAX_SLABS_NEW, "new", HEALTH_EDU_CESS)
    if tax_regime == "Old":
        tax_loan = tax_loan_old
    elif tax_regime == "New":
        tax_loan = tax_loan_new
    else:
        tax_loan = min(tax_loan_old, tax_loan_new)

    # Cash scenario taxes
    taxable_cash_old = max(0.0, annual_salary - STANDARD_DEDUCTION_OLD)
    tax_cash_old = compute_annual_tax(taxable_cash_old, TAX_SLABS_OLD, "old", HEALTH_EDU_CESS)
    taxable_cash_new = max(0.0, annual_salary - STANDARD_DEDUCTION_NEW)
    tax_cash_new = compute_annual_tax(taxable_cash_new, TAX_SLABS_NEW, "new", HEALTH_EDU_CESS)
    if tax_regime == "Old":
        tax_cash = tax_cash_old
    elif tax_regime == "New":
        tax_cash = tax_cash_new
    else:
        tax_cash = min(tax_cash_old, tax_cash_new)

    investable_loan = max(0.0, annual_salary - tax_loan - emi_y)
    investable_cash = max(0.0, annual_salary - tax_cash)

    # Grow investments
    investment_value_loan = investment_value_loan * (1 + INVESTMENT_RETURN_CAGR/100.0) + investable_loan
    investment_value_cash = investment_value_cash * (1 + INVESTMENT_RETURN_CAGR/100.0) + investable_cash

    # Grow house values by inflation
    house_value_loan = house_value_loan * (1 + INFLATION_PERCENT_PER_YEAR/100.0)
    house_value_cash = house_value_cash * (1 + INFLATION_PERCENT_PER_YEAR/100.0)

    # outstanding balance at year end
    if (y+1)*12-1 < len(am_table):
        balance = float(am_table.iloc[(y+1)*12-1]['balance'])
    else:
        balance = 0.0

    net_worth_loan = investment_value_loan + house_value_loan - balance
    net_worth_cash = investment_value_cash + house_value_cash

    rows.append({
        'year': y+1,
        'annual_salary': annual_salary,
        'interest_paid': interest_y,
        'principal_paid': principal_y,
        'emi_paid': emi_y,
        'tax_loan': tax_loan,
        'tax_cash': tax_cash,
        'investable_loan': investable_loan,
        'investable_cash': investable_cash,
        'investment_value_loan': investment_value_loan,
        'investment_value_cash': investment_value_cash,
        'house_value_loan': house_value_loan,
        'house_value_cash': house_value_cash,
        'loan_outstanding_balance_end_of_year': balance,
        'net_worth_loan': net_worth_loan,
        'net_worth_cash': net_worth_cash
    })

    # salary bump
    annual_salary *= (1 + EMPLOYEE_SALARY_INCREMENT_PERCENT_PA/100.0)

# DataFrame
df = pd.DataFrame(rows)

# -------------------- Display key results --------------------
col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("Net worth evolution")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['year'], y=df['net_worth_loan'], mode='lines+markers', name='Net worth (Loan)'))
    fig.add_trace(go.Scatter(x=df['year'], y=df['net_worth_cash'], mode='lines+markers', name='Net worth (Cash)'))
    fig.update_layout(title='Net worth over time (Loan vs Cash)', xaxis_title='Year', yaxis_title='INR', legend=dict(x=0.02, y=0.98))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Detailed components (select years)")
    show_years = st.slider("Select years to show in table", 1, YEARS, (1, min(10, YEARS)))
    st.dataframe(df.loc[df['year'].between(show_years[0], show_years[1])].reset_index(drop=True))

with col2:
    st.subheader("Summary (end of simulation)")
    final = df.iloc[-1]
    st.metric("Net worth (Loan)", f"₹{final['net_worth_loan']:,.0f}")
    st.metric("Net worth (Cash)", f"₹{final['net_worth_cash']:,.0f}")
    st.metric("Down Payment", f"₹{down_payment:,.0f}")
    st.write("\n")
    st.write("**Totals over period**")
    total_emi = df['emi_paid'].sum()
    total_interest = df['interest_paid'].sum()
    st.write(f"Total EMI paid over {YEARS} yrs: ₹{total_emi:,.0f}")
    st.write(f"Total interest paid over {YEARS} yrs: ₹{total_interest:,.0f}")

# -------------------- Additional plots --------------------
st.markdown("---")
st.subheader("Breakdown plot")
fig2 = go.Figure()
fig2.add_trace(go.Bar(x=df['year'], y=df['investment_value_loan'], name='Investment (Loan)'))
fig2.add_trace(go.Bar(x=df['year'], y=df['house_value_loan'], name='House value'))
fig2.add_trace(go.Bar(x=df['year'], y=-df['loan_outstanding_balance_end_of_year'], name='Loan outstanding (negative)'))
fig2.update_layout(barmode='stack', title='Loan scenario: investments, house value and outstanding loan', xaxis_title='Year')
st.plotly_chart(fig2, use_container_width=True)

# -------------------- Export / Download --------------------
st.markdown("---")
st.subheader("Export")
csv = df.to_csv(index=False)
b64 = base64.b64encode(csv.encode()).decode()
href = f'<a href="data:text/csv;base64,{b64}" download="loan_vs_cash_yearly.csv">Download year-by-year CSV</a>'
st.markdown(href, unsafe_allow_html=True)

st.info("Tip: change parameters on the left; charts and table update automatically.")

# -------------------- Footer --------------------
st.markdown("---")
st.caption("Model notes: \n1) Tax calculation includes basic slabs, rebate u/s 87A, surcharge (no marginal relief), and cess for AY 2025-26; simplified and does not include every possible deduction or nuance.\n2) Standard deduction: ₹50,000 (old), ₹75,000 (new).\n3) Investments are modelled as end-of-year contributions grown at a constant CAGR. \n4) House appreciation is modelled as a constant annual percent. \n5) This is a financial model for comparison & education, not financial advice.")