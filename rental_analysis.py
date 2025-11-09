"""
Rental Property Analysis Script

This script analyzes rental property listings by:
1. Estimating rent from comparables
2. Calculating monthly cash flow with all operating expenses
3. Filtering properties that meet investor criteria
4. Outputting all analyzed properties to CSV

Author: Real Estate Analysis Assistant
"""

import pandas as pd
import numpy as np
import logging
import requests
import time
import os
from typing import Dict, List, Optional, Union
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION SECTION
# ============================================================================
# All assumptions and default values are defined here.
# Modify these values to adjust the analysis criteria and expense calculations.

CONFIG = {
    # Investment Criteria
    'max_purchase_price': 250000,  # USD
    'down_payment': 50000,  # USD (or full price if property is cheaper)
    'qualification_threshold': 200,  # Monthly cash flow must be > this amount (USD)
    
    # Mortgage Assumptions
    'interest_rate': 0.05,  # Annual fixed rate (5%)
    'mortgage_term_years': 15,
    
    # Vacancy Assumptions
    'vacancy_rate': 0.20,  # 20% vacancy = 80% collection rate
    
    # Default Expense Percentages (annual, unless otherwise noted)
    # These are used when expense fields are not provided in the input data
    'expense_defaults': {
        'property_taxes_pct': 0.01,  # 1% of purchase price annually
        'insurance_pct': 0.005,  # 0.5% of purchase price annually
        'property_management_pct': 0.10,  # 10% of gross monthly rent
        'maintenance_pct_new': 0.05,  # 5% of gross monthly rent for newer/recently renovated
        'maintenance_pct_old': 0.09,  # 9% of gross monthly rent for older properties (conservative middle of 8-10%)
        'capex_pct': 0.05,  # 5% of gross monthly rent
        'hoa_fees_single_family': 0,  # $0 for detached single-family homes
        'hoa_fees_other': 150,  # $150/month for condos/townhouses (middle of $100-200 range)
        'utilities': 0,  # $0/month (or from input)
        'landscaping': 100,  # $100/month
        'accounting_legal': 50,  # $50/month
    },
    
    # Property Classification Thresholds
    'new_property_age_years': 10,  # Properties built/renovated within last 10 years use lower maintenance %
    'single_family_keywords': ['single_family', 'single-family', 'detached', 'house', 'home'],
    
    # API Configuration - RentCast API only
    'api_key': os.getenv('RENTCAST_API_KEY', ''),  # RentCast API key (loaded from .env file)
    'api_base_url': 'https://api.rentcast.io/v1',  # RentCast API base URL
    'api_rate_limit_delay': 0.2,  # Seconds to wait between API calls to respect rate limits
    'max_listings_per_request': 50,  # Maximum listings to fetch per API call
    'max_api_calls': 70,  # Maximum total API calls to make
}

# Global API call counter (for limiting total calls)
_api_call_count = 0


# ============================================================================
# DATA LOADING FUNCTIONS
# ============================================================================

def load_listings(csv_path: Union[str, Path]) -> pd.DataFrame:
    """
    Load property listings from a CSV file.
    
    Expected columns:
    - Required: property_id, address, city, state, zip_code, listing_price, beds, baths, sq_ft
    - Optional: property_type, property_age, year_built, and any expense fields
      (property_taxes, insurance, property_management, maintenance, capital_expenditures,
       hoa_fees, utilities, landscaping, accounting_legal_fees)
    
    Args:
        csv_path: Path to CSV file containing property listings
        
    Returns:
        DataFrame with property listings
        
    Raises:
        FileNotFoundError: If CSV file doesn't exist
        ValueError: If required columns are missing
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Listings file not found: {csv_path}")
    
    df = pd.read_csv(csv_path)
    
    # Required columns
    required_cols = ['property_id', 'address', 'city', 'state', 'zip_code', 
                     'listing_price', 'beds', 'baths', 'sq_ft']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns in listings CSV: {missing_cols}")
    
    logger.info(f"Loaded {len(df)} property listings from {csv_path}")
    return df


def load_rent_comps(csv_path: Union[str, Path]) -> pd.DataFrame:
    """
    Load rent comparables from a CSV file.
    
    Expected columns:
    - Required: beds, baths, sq_ft, monthly_rent
    - Required (one of): zip_code OR neighborhood
    - Optional: city, state
    
    Args:
        csv_path: Path to CSV file containing rent comparables
        
    Returns:
        DataFrame with rent comparables
        
    Raises:
        FileNotFoundError: If CSV file doesn't exist
        ValueError: If required columns are missing
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Rent comps file not found: {csv_path}")
    
    df = pd.read_csv(csv_path)
    
    # Required columns
    required_cols = ['beds', 'baths', 'sq_ft', 'monthly_rent']
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns in rent comps CSV: {missing_cols}")
    
    # Must have either zip_code or neighborhood
    if 'zip_code' not in df.columns and 'neighborhood' not in df.columns:
        raise ValueError("Rent comps CSV must have either 'zip_code' or 'neighborhood' column")
    
    logger.info(f"Loaded {len(df)} rent comparables from {csv_path}")
    return df


# ============================================================================
# API INTEGRATION FUNCTIONS
# ============================================================================

def _parse_target_area(target_area: str) -> Dict[str, str]:
    """
    Parse target area string into components (city, state, zip_code).
    
    Args:
        target_area: Target area string (e.g., "Philadelphia, PA" or "19104")
        
    Returns:
        Dictionary with city, state, zip_code (some may be None)
    """
    result = {'city': None, 'state': None, 'zip_code': None}
    
    # Check if it's a ZIP code (5 digits)
    if target_area.strip().isdigit() and len(target_area.strip()) == 5:
        result['zip_code'] = target_area.strip()
        return result
    
    # Try to parse "City, State" format
    parts = [p.strip() for p in target_area.split(',')]
    if len(parts) == 2:
        result['city'] = parts[0]
        result['state'] = parts[1]
    elif len(parts) == 1:
        # Assume it's a city name
        result['city'] = parts[0]
    
    return result


def _fetch_rentcast_listings(target_area: str, config: Dict, max_calls_for_zip: Optional[int] = None) -> pd.DataFrame:
    """
    Fetch property listings from RentCast API.
    
    Makes multiple API calls if allowed to maximize the number of properties fetched.
    
    Args:
        target_area: Target area (e.g., "Philadelphia, PA" or ZIP code)
        config: Configuration dictionary with API settings
        max_calls_for_zip: Maximum API calls to use for this ZIP code (None = use all available)
        
    Returns:
        DataFrame with property listings
    """
    global _api_call_count
    
    api_key = config.get('api_key')
    if not api_key:
        raise ValueError(
            "RentCast API key not found. "
            "Set RENTCAST_API_KEY environment variable or update CONFIG['api_key']"
        )
    
    max_calls = config.get('max_api_calls', 70)
    if max_calls_for_zip is None:
        max_calls_for_zip = max_calls - _api_call_count
    
    if _api_call_count >= max_calls:
        logger.warning(f"API call limit reached ({max_calls}). Skipping listings fetch.")
        return pd.DataFrame()
    
    base_url = config.get('api_base_url', 'https://api.rentcast.io/v1')
    headers = {'X-Api-Key': api_key}
    
    area_info = _parse_target_area(target_area)
    listings_data = []
    limit_per_call = config.get('max_listings_per_request', 50)
    
    # RentCast API: Fetch listings by city/state or zip
    if area_info['zip_code']:
        # Make multiple calls to get as many listings as possible
        offset = 0
        calls_made_for_zip = 0
        
        while calls_made_for_zip < max_calls_for_zip and _api_call_count < max_calls:
            if _api_call_count >= max_calls:
                break
            
            url = f"{base_url}/listings/sale"
            params = {
                'zipCode': area_info['zip_code'],
                'status': 'Active',
                'limit': limit_per_call,
                'offset': offset
            }
            
            logger.info(f"Fetching listings for ZIP {area_info['zip_code']} (offset {offset}, API call {_api_call_count + 1}/{max_calls})")
            response = requests.get(url, headers=headers, params=params)
            _api_call_count += 1
            calls_made_for_zip += 1
            time.sleep(config.get('api_rate_limit_delay', 0.2))
            
            if response.status_code == 200:
                data = response.json()
                batch_listings = []
                if isinstance(data, list):
                    batch_listings = data
                elif isinstance(data, dict) and 'data' in data:
                    batch_listings = data['data']
                
                if not batch_listings:
                    # No more listings available
                    break
                
                listings_data.extend(batch_listings)
                logger.info(f"  → Fetched {len(batch_listings)} listings (total so far: {len(listings_data)})")
                
                # If we got fewer than the limit, we've reached the end
                if len(batch_listings) < limit_per_call:
                    break
                
                offset += limit_per_call
            else:
                logger.warning(f"API request failed with status {response.status_code}: {response.text}")
                break
    
    elif area_info['city'] and area_info['state']:
        # Fetch by city and state - make multiple calls
        offset = 0
        calls_made_for_zip = 0
        
        while calls_made_for_zip < max_calls_for_zip and _api_call_count < max_calls:
            if _api_call_count >= max_calls:
                break
            
            url = f"{base_url}/listings/sale"
            params = {
                'city': area_info['city'],
                'state': area_info['state'],
                'status': 'Active',
                'limit': limit_per_call,
                'offset': offset
            }
            
            logger.info(f"Fetching listings for {area_info['city']}, {area_info['state']} (offset {offset}, API call {_api_call_count + 1}/{max_calls})")
            response = requests.get(url, headers=headers, params=params)
            _api_call_count += 1
            calls_made_for_zip += 1
            time.sleep(config.get('api_rate_limit_delay', 0.2))
            
            if response.status_code == 200:
                data = response.json()
                batch_listings = []
                if isinstance(data, list):
                    batch_listings = data
                elif isinstance(data, dict) and 'data' in data:
                    batch_listings = data['data']
                
                if not batch_listings:
                    break
                
                listings_data.extend(batch_listings)
                logger.info(f"  → Fetched {len(batch_listings)} listings (total so far: {len(listings_data)})")
                
                if len(batch_listings) < limit_per_call:
                    break
                
                offset += limit_per_call
            else:
                logger.warning(f"API request failed with status {response.status_code}: {response.text}")
                break
    else:
        raise ValueError(f"Could not parse target area: {target_area}")
    
    if not listings_data:
        logger.warning("No listings found from API")
        return pd.DataFrame()
    
    # Transform API response to our expected format
    transformed_listings = []
    seen_ids = set()  # Track duplicates
    
    for listing in listings_data:
        prop_id = listing.get('id') or listing.get('mlsId') or f"prop_{len(transformed_listings)}"
        
        # Skip duplicates
        if prop_id in seen_ids:
            continue
        seen_ids.add(prop_id)
        
        transformed = {
            'property_id': prop_id,
            'address': listing.get('address', {}).get('addressLine1', '') or listing.get('addressLine1', ''),
            'city': listing.get('address', {}).get('city', '') or listing.get('city', ''),
            'state': listing.get('address', {}).get('state', '') or listing.get('state', ''),
            'zip_code': str(listing.get('address', {}).get('zipCode', '')) or str(listing.get('zipCode', '')),
            'listing_price': listing.get('price', 0),
            'beds': listing.get('bedrooms', 0),
            'baths': listing.get('bathrooms', 0),
            'sq_ft': listing.get('squareFootage', 0),
            'property_type': listing.get('propertyType', ''),
            'year_built': listing.get('yearBuilt'),
        }
        transformed_listings.append(transformed)
    
    df = pd.DataFrame(transformed_listings)
    logger.info(f"Fetched {len(df)} unique listings from RentCast API")
    return df


def _fetch_rentcast_rent_comps(target_area: str, config: Dict) -> pd.DataFrame:
    """
    Fetch rent comparables from RentCast API.
    
    Args:
        target_area: Target area (e.g., "Philadelphia, PA" or ZIP code)
        config: Configuration dictionary with API settings
        
    Returns:
        DataFrame with rent comparables
    """
    global _api_call_count
    
    api_key = config.get('api_key')
    if not api_key:
        raise ValueError(
            "RentCast API key not found. "
            "Set RENTCAST_API_KEY environment variable or update CONFIG['api_key']"
        )
    
    max_calls = config.get('max_api_calls', 10)
    base_url = config.get('api_base_url', 'https://api.rentcast.io/v1')
    headers = {'X-Api-Key': api_key}
    
    area_info = _parse_target_area(target_area)
    rent_comps_data = []
    
    # RentCast API: Fetch rental data by zip code
    if area_info['zip_code']:
        if _api_call_count >= max_calls:
            logger.warning(f"API call limit reached ({max_calls}). Skipping rent comps fetch.")
            return pd.DataFrame()
        
        # Try different endpoint formats for rent comparables
        url = f"{base_url}/rental-comps/{area_info['zip_code']}"
        params = {
            'limit': config.get('max_listings_per_request', 100)
        }
        
        logger.info(f"Fetching rent comparables for ZIP code: {area_info['zip_code']} (API call {_api_call_count + 1}/{max_calls})")
        response = requests.get(url, headers=headers, params=params)
        _api_call_count += 1
        time.sleep(config.get('api_rate_limit_delay', 0.5))
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                rent_comps_data.extend(data)
            elif isinstance(data, dict) and 'data' in data:
                rent_comps_data.extend(data['data'])
        else:
            logger.warning(f"API request failed with status {response.status_code}: {response.text}")
    
    elif area_info['city'] and area_info['state']:
        # For city/state, we need to get ZIP codes first, then fetch rent comps
        # This is a simplified approach - you may need to adjust based on API capabilities
        logger.warning(
            "RentCast API requires ZIP code for rent comparables. "
            "Please provide a ZIP code or implement ZIP code lookup for the city."
        )
        return pd.DataFrame()
    else:
        raise ValueError(f"Could not parse target area: {target_area}")
    
    if not rent_comps_data:
        logger.warning("No rent comparables found from API")
        return pd.DataFrame()
    
    # Transform API response to our expected format
    transformed_comps = []
    for comp in rent_comps_data:
        transformed = {
            'beds': comp.get('bedrooms', 0),
            'baths': comp.get('bathrooms', 0),
            'sq_ft': comp.get('squareFootage', 0),
            'monthly_rent': comp.get('rent', 0),
            'zip_code': str(comp.get('zipCode', '')),
            'city': comp.get('city', ''),
            'state': comp.get('state', ''),
        }
        transformed_comps.append(transformed)
    
    df = pd.DataFrame(transformed_comps)
    logger.info(f"Fetched {len(df)} rent comparables from RentCast API")
    return df


def fetch_listings_from_api(target_area: str, config: Dict = None, max_calls_for_area: Optional[int] = None, **kwargs) -> pd.DataFrame:
    """
    Fetch property listings from RentCast API.
    
    Args:
        target_area: Target area (e.g., "Philadelphia, PA" or ZIP code)
        config: Configuration dictionary (defaults to CONFIG)
        max_calls_for_area: Maximum API calls to use for this area (None = use all available)
        **kwargs: Additional API-specific parameters
        
    Returns:
        DataFrame with property listings (same format as load_listings output)
    """
    if config is None:
        config = CONFIG
    
    return _fetch_rentcast_listings(target_area, config, max_calls_for_area)


def fetch_rent_comps_from_api(target_area: str, config: Dict = None, **kwargs) -> pd.DataFrame:
    """
    Fetch rent comparables from RentCast API.
    
    Args:
        target_area: Target area (e.g., "Philadelphia, PA" or ZIP code)
        config: Configuration dictionary (defaults to CONFIG)
        **kwargs: Additional API-specific parameters
        
    Returns:
        DataFrame with rent comparables (same format as load_rent_comps output)
    """
    if config is None:
        config = CONFIG
    
    return _fetch_rentcast_rent_comps(target_area, config)


# ============================================================================
# RENT ESTIMATION FUNCTION
# ============================================================================

def estimate_monthly_rent(property: Dict, comps: pd.DataFrame) -> float:
    """
    Estimate monthly rent for a property based on rent comparables.
    
    Filtering strategy:
    1. Try to match by beds (±1) and same zip_code
    2. If no matches, try same zip_code only
    3. If still no matches, try same city
    4. Return median rent of filtered comps
    5. Return NaN if insufficient comps
    
    Args:
        property: Dictionary with property attributes (beds, baths, sq_ft, zip_code, city)
        comps: DataFrame with rent comparables
        
    Returns:
        Estimated monthly rent (float) or np.nan if insufficient comps
    """
    # Handle empty comps DataFrame
    if comps.empty or len(comps) == 0:
        logger.warning(f"No rent comparables available for property")
        return np.nan
    
    property_beds = property.get('beds')
    property_zip = property.get('zip_code')
    property_city = property.get('city', '').lower()
    
    if pd.isna(property_beds) or pd.isna(property_zip):
        logger.warning(f"Property missing beds or zip_code, cannot estimate rent")
        return np.nan
    
    # Check if required columns exist
    required_cols = ['beds', 'zip_code', 'monthly_rent']
    missing_cols = [col for col in required_cols if col not in comps.columns]
    if missing_cols:
        logger.warning(f"Rent comps missing required columns: {missing_cols}")
        return np.nan
    
    # Strategy 1: Match by beds (±1) and same zip_code
    filtered = comps[
        (comps['beds'].between(property_beds - 1, property_beds + 1)) &
        (comps['zip_code'] == property_zip)
    ]
    
    # Strategy 2: Same zip_code only
    if len(filtered) < 3:
        filtered = comps[comps['zip_code'] == property_zip]
    
    # Strategy 3: Same city (if city info available)
    if len(filtered) < 3 and property_city:
        if 'city' in comps.columns:
            filtered = comps[comps['city'].str.lower() == property_city]
        elif 'neighborhood' in comps.columns:
            # If we have neighborhood instead, we can't match by city easily
            pass
    
    if len(filtered) < 1:
        logger.warning(
            f"Insufficient rent comps for property (beds={property_beds}, "
            f"zip={property_zip}). Found {len(filtered)} comps."
        )
        return np.nan
    
    estimated_rent = filtered['monthly_rent'].median()
    logger.debug(
        f"Estimated rent ${estimated_rent:.2f} for property using {len(filtered)} comps"
    )
    return float(estimated_rent)


# ============================================================================
# FINANCIAL CALCULATION FUNCTIONS
# ============================================================================

def compute_mortgage_payment(loan_amount: float, annual_rate: float, term_years: int) -> float:
    """
    Compute monthly mortgage payment for a fixed-rate, fully amortizing loan.
    
    Uses the standard mortgage payment formula:
    payment = principal * (monthly_rate * (1 + monthly_rate)^term_months) / ((1 + monthly_rate)^term_months - 1)
    
    Args:
        loan_amount: Principal loan amount (USD)
        annual_rate: Annual interest rate (e.g., 0.05 for 5%)
        term_years: Loan term in years
        
    Returns:
        Monthly mortgage payment (USD)
    """
    if loan_amount <= 0:
        return 0.0
    
    monthly_rate = annual_rate / 12
    term_months = term_years * 12
    
    if monthly_rate == 0:
        return loan_amount / term_months
    
    payment = loan_amount * (
        monthly_rate * (1 + monthly_rate) ** term_months
    ) / (
        (1 + monthly_rate) ** term_months - 1
    )
    
    return float(payment)


def get_property_expenses(
    property: Dict,
    estimated_rent: float,
    listing_price: float,
    config: Dict
) -> Dict[str, float]:
    """
    Get monthly operating expenses for a property.
    
    If expense fields are provided in the property data, use those.
    Otherwise, calculate from percentage assumptions based on property characteristics.
    
    Args:
        property: Dictionary with property attributes and optional expense fields
        estimated_rent: Estimated gross monthly rent (USD) - may be NaN
        listing_price: Property purchase price (USD)
        config: Configuration dictionary with expense defaults
        
    Returns:
        Dictionary with all monthly expense amounts (USD)
    """
    defaults = config['expense_defaults']
    expenses = {}
    
    # If estimated_rent is NaN, use a rule of thumb: 0.8% of listing price per month
    if pd.isna(estimated_rent) or estimated_rent == 0:
        estimated_rent = listing_price * 0.008  # 0.8% rule of thumb
    
    # Property taxes (annual percentage of purchase price)
    if 'property_taxes' in property and pd.notna(property.get('property_taxes')):
        # Assume provided value is monthly if < 1000, otherwise annual
        prop_taxes = property['property_taxes']
        expenses['property_taxes'] = prop_taxes if prop_taxes < 1000 else prop_taxes / 12
    else:
        expenses['property_taxes'] = (listing_price * defaults['property_taxes_pct']) / 12
    
    # Insurance (annual percentage of purchase price)
    if 'insurance' in property and pd.notna(property.get('insurance')):
        ins = property['insurance']
        expenses['insurance'] = ins if ins < 1000 else ins / 12
    else:
        expenses['insurance'] = (listing_price * defaults['insurance_pct']) / 12
    
    # Property management (percentage of gross rent)
    if 'property_management' in property and pd.notna(property.get('property_management')):
        expenses['property_management'] = property['property_management']
    else:
        expenses['property_management'] = estimated_rent * defaults['property_management_pct']
    
    # Maintenance (percentage of gross rent, depends on property age)
    if 'maintenance' in property and pd.notna(property.get('maintenance')):
        expenses['maintenance'] = property['maintenance']
    else:
        # Determine if property is "new" based on property_age or year_built
        is_new = False
        current_year = 2024  # Update as needed
        
        if 'property_age' in property and pd.notna(property.get('property_age')):
            is_new = property['property_age'] <= config['new_property_age_years']
        elif 'year_built' in property and pd.notna(property.get('year_built')):
            age = current_year - property['year_built']
            is_new = age <= config['new_property_age_years']
        
        maintenance_pct = (
            defaults['maintenance_pct_new'] if is_new 
            else defaults['maintenance_pct_old']
        )
        expenses['maintenance'] = estimated_rent * maintenance_pct
    
    # Capital expenditures (percentage of gross rent)
    if 'capital_expenditures' in property and pd.notna(property.get('capital_expenditures')):
        expenses['capital_expenditures'] = property['capital_expenditures']
    else:
        expenses['capital_expenditures'] = estimated_rent * defaults['capex_pct']
    
    # HOA fees (depends on property type)
    if 'hoa_fees' in property and pd.notna(property.get('hoa_fees')):
        expenses['hoa_fees'] = property['hoa_fees']
    else:
        # Determine if single-family detached
        property_type = str(property.get('property_type', '')).lower()
        is_single_family = any(
            keyword in property_type 
            for keyword in config['single_family_keywords']
        )
        
        expenses['hoa_fees'] = (
            defaults['hoa_fees_single_family'] if is_single_family
            else defaults['hoa_fees_other']
        )
    
    # Utilities
    if 'utilities' in property and pd.notna(property.get('utilities')):
        expenses['utilities'] = property['utilities']
    else:
        expenses['utilities'] = defaults['utilities']
    
    # Landscaping
    if 'landscaping' in property and pd.notna(property.get('landscaping')):
        expenses['landscaping'] = property['landscaping']
    else:
        expenses['landscaping'] = defaults['landscaping']
    
    # Accounting/legal fees
    if 'accounting_legal_fees' in property and pd.notna(property.get('accounting_legal_fees')):
        expenses['accounting_legal_fees'] = property['accounting_legal_fees']
    else:
        expenses['accounting_legal_fees'] = defaults['accounting_legal']
    
    return expenses


def compute_property_cash_flow(
    property: Dict,
    estimated_rent: float,
    expenses: Dict[str, float],
    monthly_mortgage_payment: float,
    vacancy_rate: float
) -> Dict[str, Union[float, bool]]:
    """
    Compute monthly cash flow for a property.
    
    Cash flow = effective_rent - (mortgage_payment + total_operating_expenses)
    where effective_rent = gross_rent * (1 - vacancy_rate)
    
    Args:
        property: Dictionary with property attributes
        estimated_rent: Estimated gross monthly rent (USD)
        expenses: Dictionary of monthly operating expenses
        monthly_mortgage_payment: Monthly mortgage payment (USD)
        vacancy_rate: Vacancy rate (e.g., 0.20 for 20%)
        
    Returns:
        Dictionary with cash flow calculation results including:
        - effective_monthly_rent
        - total_operating_expenses
        - monthly_mortgage_payment
        - monthly_cash_flow
    """
    # Apply vacancy
    effective_rent = estimated_rent * (1 - vacancy_rate) if pd.notna(estimated_rent) else 0
    
    # Sum all operating expenses
    total_operating_expenses = sum(expenses.values())
    
    # Calculate cash flow
    monthly_cash_flow = effective_rent - (monthly_mortgage_payment + total_operating_expenses)
    
    return {
        'effective_monthly_rent': effective_rent,
        'total_operating_expenses': total_operating_expenses,
        'monthly_mortgage_payment': monthly_mortgage_payment,
        'monthly_cash_flow': monthly_cash_flow,
    }


# ============================================================================
# MAIN ANALYSIS FUNCTION
# ============================================================================

def analyze_properties(
    listings: pd.DataFrame,
    rent_comps: pd.DataFrame,
    config: Dict
) -> List[Dict]:
    """
    Analyze all property listings and calculate cash flow.
    
    For each property:
    1. Skip if listing_price > max_purchase_price
    2. Calculate down_payment and loan_amount
    3. Estimate rent from comparables
    4. Get expenses
    5. Calculate mortgage payment
    6. Calculate cash flow
    7. Determine if meets criteria
    
    Args:
        listings: DataFrame with property listings
        rent_comps: DataFrame with rent comparables
        config: Configuration dictionary
        
    Returns:
        List of dictionaries, each containing analysis results for one property
    """
    analyzed_properties = []
    
    for idx, row in listings.iterrows():
        property_dict = row.to_dict()
        listing_price = property_dict['listing_price']
        
        # Skip if over max purchase price
        if listing_price > config['max_purchase_price']:
            logger.debug(
                f"Skipping property {property_dict.get('property_id')}: "
                f"price ${listing_price:,.0f} exceeds max ${config['max_purchase_price']:,.0f}"
            )
            continue
        
        # Calculate down payment and loan amount
        down_payment = min(config['down_payment'], listing_price)
        loan_amount = max(listing_price - down_payment, 0)
        
        # Estimate rent
        estimated_rent = estimate_monthly_rent(property_dict, rent_comps)
        
        # If rent estimate is NaN, use rule of thumb (0.8% of listing price per month)
        if pd.isna(estimated_rent) or estimated_rent == 0:
            estimated_rent = listing_price * 0.008
            logger.debug(f"Using rule-of-thumb rent estimate: ${estimated_rent:.2f} (0.8% of listing price)")
        
        # Get expenses
        expenses = get_property_expenses(
            property_dict, estimated_rent, listing_price, config
        )
        
        # Calculate mortgage payment
        monthly_mortgage = compute_mortgage_payment(
            loan_amount,
            config['interest_rate'],
            config['mortgage_term_years']
        )
        
        # Calculate cash flow
        cash_flow_results = compute_property_cash_flow(
            property_dict,
            estimated_rent,
            expenses,
            monthly_mortgage,
            config['vacancy_rate']
        )
        
        # Determine if meets criteria
        meets_criteria = (
            pd.notna(cash_flow_results['monthly_cash_flow']) and
            cash_flow_results['monthly_cash_flow'] > config['qualification_threshold']
        )
        
        # Compile results - ensure all values are filled (no NaN)
        result = {
            'property_id': property_dict.get('property_id', ''),
            'address': property_dict.get('address', ''),
            'city': property_dict.get('city', ''),
            'state': property_dict.get('state', ''),
            'zip_code': str(property_dict.get('zip_code', '')),
            'listing_price': float(listing_price) if pd.notna(listing_price) else 0.0,
            'down_payment': float(down_payment) if pd.notna(down_payment) else 0.0,
            'loan_amount': float(loan_amount) if pd.notna(loan_amount) else 0.0,
            'interest_rate': float(config['interest_rate']),
            'mortgage_term_years': int(config['mortgage_term_years']),
            'estimated_gross_monthly_rent': float(estimated_rent) if pd.notna(estimated_rent) else 0.0,
            'vacancy_rate': float(config['vacancy_rate']),
            'effective_monthly_rent': float(cash_flow_results['effective_monthly_rent']) if pd.notna(cash_flow_results['effective_monthly_rent']) else 0.0,
            'property_taxes': float(expenses['property_taxes']) if pd.notna(expenses['property_taxes']) else 0.0,
            'insurance': float(expenses['insurance']) if pd.notna(expenses['insurance']) else 0.0,
            'property_management': float(expenses['property_management']) if pd.notna(expenses['property_management']) else 0.0,
            'maintenance': float(expenses['maintenance']) if pd.notna(expenses['maintenance']) else 0.0,
            'capital_expenditures': float(expenses['capital_expenditures']) if pd.notna(expenses['capital_expenditures']) else 0.0,
            'hoa_fees': float(expenses['hoa_fees']) if pd.notna(expenses['hoa_fees']) else 0.0,
            'utilities': float(expenses['utilities']) if pd.notna(expenses['utilities']) else 0.0,
            'landscaping': float(expenses['landscaping']) if pd.notna(expenses['landscaping']) else 0.0,
            'accounting_legal_fees': float(expenses['accounting_legal_fees']) if pd.notna(expenses['accounting_legal_fees']) else 0.0,
            'total_operating_expenses': float(cash_flow_results['total_operating_expenses']) if pd.notna(cash_flow_results['total_operating_expenses']) else 0.0,
            'monthly_mortgage_payment': float(monthly_mortgage) if pd.notna(monthly_mortgage) else 0.0,
            'monthly_cash_flow': float(cash_flow_results['monthly_cash_flow']) if pd.notna(cash_flow_results['monthly_cash_flow']) else 0.0,
            'meets_criteria': meets_criteria,
        }
        
        analyzed_properties.append(result)
    
    logger.info(f"Analyzed {len(analyzed_properties)} properties")
    return analyzed_properties


# ============================================================================
# CSV OUTPUT FUNCTION
# ============================================================================

def save_results_to_csv(analyzed_properties: List[Dict], output_path: Union[str, Path]) -> None:
    """
    Save analyzed properties to CSV file.
    
    Args:
        analyzed_properties: List of dictionaries with analysis results
        output_path: Path to output CSV file
    """
    if not analyzed_properties:
        logger.warning("No properties to save")
        return
    
    df = pd.DataFrame(analyzed_properties)
    
    # Fill any remaining NaN values with 0 or empty string
    # Fill numeric columns with 0
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    df[numeric_cols] = df[numeric_cols].fillna(0)
    
    # Fill string columns with empty string
    string_cols = df.select_dtypes(include=['object']).columns
    df[string_cols] = df[string_cols].fillna('')
    
    # Ensure boolean column is properly formatted
    df['meets_criteria'] = df['meets_criteria'].map({True: 'TRUE', False: 'FALSE'})
    
    # Save to CSV
    output_path = Path(output_path)
    df.to_csv(output_path, index=False, na_rep='0')
    logger.info(f"Saved {len(df)} analyzed properties to {output_path}")
    
    # Print summary
    qualified = df['meets_criteria'] == 'TRUE'
    print(f"\n{'='*60}")
    print(f"Analysis Summary")
    print(f"{'='*60}")
    print(f"Total properties analyzed: {len(df)}")
    print(f"Properties meeting criteria: {qualified.sum()}")
    print(f"Properties not meeting criteria: {(~qualified).sum()}")
    if qualified.sum() > 0:
        avg_cash_flow = df[qualified]['monthly_cash_flow'].mean()
        print(f"Average cash flow (qualified): ${avg_cash_flow:,.2f}/month")
    print(f"{'='*60}\n")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """
    Main entry point for the rental property analysis script.
    
    This function uses API integration to fetch property listings and rent comparables.
    Set the target area and API key before running.
    """
    global _api_call_count
    
    # Reset API call counter
    _api_call_count = 0
    
    # Configuration
    zip_codes = ["19146", "19147", "19107", "19103"]  # ZIP codes to analyze
    output_csv = "analyzed_properties.csv"
    
    # Check if API key is set
    if not CONFIG.get('api_key'):
        logger.error(
            "RentCast API key not found!\n"
            "Please set your RentCast API key in one of these ways:\n"
            "1. Set environment variable: export RENTCAST_API_KEY='your_api_key'\n"
            "2. Update CONFIG['api_key'] in the script\n"
            "\n"
            "Get your API key at: https://rentcast.io/\n"
        )
        return
    
    max_calls = CONFIG.get('max_api_calls', 10)
    logger.info(f"API call limit set to {max_calls} calls")
    logger.info(f"Analyzing {len(zip_codes)} ZIP codes: {', '.join(zip_codes)}")
    
    try:
        # Calculate API call allocation
        # Reserve 1 call per ZIP for rent comparables
        num_zip_codes = len(zip_codes)
        calls_for_rent_comps = num_zip_codes
        calls_remaining_for_listings = max_calls - calls_for_rent_comps
        
        # Distribute listing calls evenly across ZIP codes
        calls_per_zip = max(1, calls_remaining_for_listings // num_zip_codes)
        logger.info(f"Allocating {calls_per_zip} API calls per ZIP code for listings (reserving {calls_for_rent_comps} for rent comparables)")
        
        # Fetch data from API for all ZIP codes
        all_listings = []
        all_rent_comps = []
        
        for zip_code in zip_codes:
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing ZIP code: {zip_code}")
            logger.info(f"{'='*60}")
            
            # Fetch listings for this ZIP code (with allocated calls)
            listings = fetch_listings_from_api(zip_code, CONFIG, max_calls_for_area=calls_per_zip)
            if not listings.empty:
                all_listings.append(listings)
                logger.info(f"Found {len(listings)} listings for ZIP {zip_code}")
            else:
                logger.warning(f"No listings found for ZIP {zip_code}")
            
            # Fetch rent comps for this ZIP code (1 call per ZIP)
            if _api_call_count < max_calls:
                rent_comps = fetch_rent_comps_from_api(zip_code, CONFIG)
                if not rent_comps.empty:
                    all_rent_comps.append(rent_comps)
                    logger.info(f"Found {len(rent_comps)} rent comparables for ZIP {zip_code}")
                else:
                    logger.warning(f"No rent comparables found for ZIP {zip_code}")
            else:
                logger.warning(f"API call limit reached, skipping rent comps for ZIP {zip_code}")
        
        # Combine all results
        if all_listings:
            listings_df = pd.concat(all_listings, ignore_index=True)
            logger.info(f"\nTotal listings across all ZIP codes: {len(listings_df)}")
        else:
            listings_df = pd.DataFrame()
            logger.warning("No property listings found across all ZIP codes. Check your API key.")
            return
        
        if all_rent_comps:
            rent_comps_df = pd.concat(all_rent_comps, ignore_index=True)
            logger.info(f"Total rent comparables across all ZIP codes: {len(rent_comps_df)}")
        else:
            rent_comps_df = pd.DataFrame()
            logger.warning(
                "No rent comparables found. "
                "Rent estimation may be limited. Check your API key."
            )
        
        # Analyze properties
        logger.info("\nAnalyzing properties...")
        analyzed_properties = analyze_properties(listings_df, rent_comps_df, CONFIG)
        
        if not analyzed_properties:
            logger.warning("No properties were analyzed. Check your filters and data.")
            return
        
        # Save results
        logger.info("Saving results...")
        save_results_to_csv(analyzed_properties, output_csv)
        
        logger.info("Analysis complete!")
        
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
    except requests.exceptions.RequestException as e:
        logger.error(f"API request error: {e}")
        logger.info(
            "\nTroubleshooting tips for RentCast API:\n"
            "1. Check your internet connection\n"
            "2. Verify your API key is correct and has an active subscription\n"
            f"3. Check if you've exceeded API rate limits ({max_calls} calls limit configured)\n"
            "4. Ensure the target area is valid (city, state or ZIP code)\n"
            "5. Check RentCast API documentation: https://developers.rentcast.io/\n"
        )
    except Exception as e:
        logger.error(f"Error during analysis: {e}", exc_info=True)


if __name__ == "__main__":
    main()

