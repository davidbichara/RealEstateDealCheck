# Rental Property Analysis Script

A Python script that analyzes rental property listings by estimating rent from comparables, calculating monthly cash flow with all operating expenses, and filtering properties that meet investor criteria.

## Features

- **Rent Estimation**: Estimates monthly rent based on comparable properties using filtering by beds, zip code, and city
- **Cash Flow Calculation**: Computes monthly cash flow with all operating expenses including:
  - Property taxes
  - Insurance
  - Property management
  - Maintenance (adjusted for property age)
  - Capital expenditures
  - HOA fees (adjusted for property type)
  - Utilities
  - Landscaping
  - Accounting/legal fees
- **Mortgage Calculation**: Uses standard amortization formula for fixed-rate, 15-year mortgages
- **Qualification Filtering**: Identifies properties that meet investor criteria (cash flow > $200/month)
- **CSV Output**: Saves all analyzed properties with detailed financial metrics

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### 1. Choose an API Provider

The script supports multiple affordable real estate APIs:

**HasData (Recommended - Most Affordable)**
- **Price**: $49/month for 200,000 requests (~$0.00025 per request)
- **Best for**: High-volume usage, cost-effective
- **Sign up**: https://hasdata.com/

**Realie (Free Tier Available)**
- **Price**: Free tier available, paid plans from $50/month
- **Best for**: Low-volume usage, testing
- **Sign up**: https://www.realie.ai/

**RentCast (Limited Free Tier)**
- **Price**: 50 free calls/month, then paid
- **Best for**: Testing with limited usage
- **Sign up**: https://rentcast.io/

### 2. Get Your API Key

1. Sign up for your chosen API provider
2. Get your API key from your account dashboard
3. Note: The script defaults to HasData (most affordable option)

### 3. Set Your API Key

**Option A: Environment Variable (Recommended)**
```bash
# For HasData (default)
# Windows PowerShell
$env:HASDATA_API_KEY="your_api_key_here"

# Windows CMD
set HASDATA_API_KEY=your_api_key_here

# Linux/Mac
export HASDATA_API_KEY="your_api_key_here"

# For Realie
$env:REALIE_API_KEY="your_api_key_here"

# For RentCast
$env:RENTCAST_API_KEY="your_api_key_here"
```

**Option B: Update the Script**
Edit `rental_analysis.py` and update the `CONFIG` dictionary:
```python
'api_provider': 'hasdata',  # or 'realie' or 'rentcast'
'api_key': 'your_api_key_here',  # Replace with your actual API key
```

### 4. Configure Target Area

Edit the `main()` function in `rental_analysis.py` to set your target area:
```python
target_area = "Philadelphia, PA"  # Or use a ZIP code like "19104"
```

The script accepts:
- City and state: `"Philadelphia, PA"`
- ZIP code: `"19104"`

### 5. Run the Analysis

```bash
python rental_analysis.py
```

The script will:
1. Fetch property listings from your chosen API for your target area
2. Fetch rent comparables from your chosen API
3. Analyze each property
4. Save results to `analyzed_properties.csv`

### 6. Review Results

Open `analyzed_properties.csv` to see:
- All financial calculations for each property
- Whether each property meets the qualification criteria
- Properties that don't qualify are still included for review

## Configuration

All assumptions and default values are defined in the `CONFIG` dictionary at the top of `rental_analysis.py`. Key settings:

- **Max purchase price**: $250,000
- **Down payment**: $50,000 (or full price if cheaper)
- **Mortgage**: 5% annual, 15-year fixed
- **Vacancy rate**: 20%
- **Qualification threshold**: Monthly cash flow > $200

### Expense Defaults

- **Property taxes**: 1% of purchase price annually
- **Insurance**: 0.5% of purchase price annually
- **Property management**: 10% of gross rent
- **Maintenance**: 
  - 5% of gross rent for newer properties (≤10 years old)
  - 9% of gross rent for older properties
- **CapEx**: 5% of gross rent
- **HOA fees**: 
  - $0 for detached single-family homes
  - $150/month for condos/townhouses
- **Landscaping**: $100/month
- **Accounting/legal**: $50/month

## API Integration

The script supports multiple affordable real estate APIs. The API integration is fully implemented and ready to use.

### Supported API Providers

- **HasData** (default) - Most affordable option
  - Price: $49/month for 200,000 requests
  - Best for: High-volume usage
  - Website: https://hasdata.com/

- **Realie** - Free tier available
  - Price: Free tier + paid plans from $50/month
  - Best for: Low-volume usage and testing
  - Website: https://www.realie.ai/

- **RentCast** - Limited free tier
  - Price: 50 free calls/month, then paid
  - Best for: Testing with limited usage
  - Website: https://rentcast.io/

### Using CSV Files Instead

If you prefer to use CSV files instead of the API, you can modify the `main()` function:

```python
# Replace API calls with CSV loading
listings = load_listings("listings.csv")
rent_comps = load_rent_comps("rent_comps.csv")
```

See the function docstrings for the required CSV column formats.

## Output Format

The output CSV includes all required columns:
- Property identification (property_id, address, city, state, zip_code)
- Purchase details (listing_price, down_payment, loan_amount)
- Mortgage terms (interest_rate, mortgage_term_years)
- Rent estimates (estimated_gross_monthly_rent, vacancy_rate, effective_monthly_rent)
- All expense categories (property_taxes, insurance, property_management, maintenance, capital_expenditures, hoa_fees, utilities, landscaping, accounting_legal_fees)
- Financial summary (total_operating_expenses, monthly_mortgage_payment, monthly_cash_flow)
- Qualification status (meets_criteria: TRUE/FALSE)

## Notes

- Properties with listing prices > $250,000 are automatically skipped
- Properties without sufficient rent comparables will have `estimated_gross_monthly_rent = NaN` and `meets_criteria = FALSE`
- All expenses can be provided in the input data or will be calculated from defaults
- The script handles missing data gracefully and logs warnings
- The script includes rate limiting (0.2 second delay between API calls) to respect API limits
- For rent comparables, ZIP codes work best; city/state may have limited results depending on the API
- **Cost Comparison**: HasData at $49/month for 200k requests is the most affordable option (~$0.00025 per request)
- To switch API providers, change `CONFIG['api_provider']` to 'hasdata', 'realie', or 'rentcast'

