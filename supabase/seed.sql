-- Manual seed of real, verified securities -- a stand-in for the NSE/BSE/AMFI sync job
-- (CLAUDE.md "Securities sync" section) until that job can actually run somewhere with
-- unrestricted internet access (it can't from inside this sandbox -- NSE, BSE, AMFI and
-- Yahoo Finance are all outside its egress allowlist; see the conversation this was built
-- from). This file is idempotent (ON CONFLICT ... DO NOTHING) so it's safe to re-run once
-- the real sync exists and starts inserting the same ISINs.
--
-- Stocks: all 42 rows and their ISINs came from NSE's own current Nifty 50 constituent
-- list (nsearchives.nseindia.com/content/indices/ind_nifty50list.csv), fetched directly,
-- not recalled from memory -- a few (e.g. BAJFINANCE, KOTAKBANK, NESTLEIND) differ from
-- what's commonly misquoted elsewhere. ETF, mutual fund and bond ISINs were verified
-- individually via web search against NSE/AMFI/bond-registry sources. None of this is
-- guessed. No prices are seeded here -- that's what the Phase 1 "price refresh job" is
-- for (same network problem as this sync), so v_holdings_valued will show a null
-- current_value for these until that job runs.

insert into public.securities (isin, name, type, symbol, exchange, sector, is_custom, metadata) values
  ('INE002A01018', 'Reliance Industries Ltd.',              'STOCK', 'RELIANCE',   'NSE', 'Energy',              false, '{}'),
  ('INE467B01029', 'Tata Consultancy Services Ltd.',        'STOCK', 'TCS',        'NSE', 'IT',                  false, '{}'),
  ('INE040A01034', 'HDFC Bank Ltd.',                        'STOCK', 'HDFCBANK',   'NSE', 'Banking',             false, '{}'),
  ('INE009A01021', 'Infosys Ltd.',                          'STOCK', 'INFY',       'NSE', 'IT',                  false, '{}'),
  ('INE090A01021', 'ICICI Bank Ltd.',                       'STOCK', 'ICICIBANK',  'NSE', 'Banking',             false, '{}'),
  ('INE030A01027', 'Hindustan Unilever Ltd.',                'STOCK', 'HINDUNILVR', 'NSE', 'FMCG',                false, '{}'),
  ('INE062A01020', 'State Bank of India',                    'STOCK', 'SBIN',       'NSE', 'Banking',             false, '{}'),
  ('INE397D01024', 'Bharti Airtel Ltd.',                     'STOCK', 'BHARTIARTL', 'NSE', 'Telecom',             false, '{}'),
  ('INE154A01025', 'ITC Ltd.',                                'STOCK', 'ITC',        'NSE', 'FMCG',                false, '{}'),
  ('INE237A01036', 'Kotak Mahindra Bank Ltd.',                'STOCK', 'KOTAKBANK',  'NSE', 'Banking',             false, '{}'),
  ('INE018A01030', 'Larsen & Toubro Ltd.',                    'STOCK', 'LT',         'NSE', 'Infrastructure',      false, '{}'),
  ('INE238A01034', 'Axis Bank Ltd.',                          'STOCK', 'AXISBANK',   'NSE', 'Banking',             false, '{}'),
  ('INE296A01032', 'Bajaj Finance Ltd.',                      'STOCK', 'BAJFINANCE', 'NSE', 'Financial Services',  false, '{}'),
  ('INE918I01026', 'Bajaj Finserv Ltd.',                      'STOCK', 'BAJAJFINSV', 'NSE', 'Financial Services',  false, '{}'),
  ('INE917I01010', 'Bajaj Auto Ltd.',                         'STOCK', 'BAJAJ-AUTO', 'NSE', 'Automobile',          false, '{}'),
  ('INE021A01026', 'Asian Paints Ltd.',                       'STOCK', 'ASIANPAINT', 'NSE', 'Consumer Durables',   false, '{}'),
  ('INE585B01010', 'Maruti Suzuki India Ltd.',                'STOCK', 'MARUTI',     'NSE', 'Automobile',          false, '{}'),
  ('INE860A01027', 'HCL Technologies Ltd.',                   'STOCK', 'HCLTECH',    'NSE', 'IT',                  false, '{}'),
  ('INE044A01036', 'Sun Pharmaceutical Industries Ltd.',      'STOCK', 'SUNPHARMA',  'NSE', 'Pharma',              false, '{}'),
  ('INE280A01028', 'Titan Company Ltd.',                      'STOCK', 'TITAN',      'NSE', 'Consumer Durables',   false, '{}'),
  ('INE155A01022', 'Tata Motors Passenger Vehicles Ltd.',     'STOCK', 'TMPV',       'NSE', 'Automobile',          false, '{}'),
  ('INE075A01022', 'Wipro Ltd.',                              'STOCK', 'WIPRO',      'NSE', 'IT',                  false, '{}'),
  ('INE481G01011', 'UltraTech Cement Ltd.',                   'STOCK', 'ULTRACEMCO', 'NSE', 'Cement',              false, '{}'),
  ('INE239A01024', 'Nestle India Ltd.',                       'STOCK', 'NESTLEIND',  'NSE', 'FMCG',                false, '{}'),
  ('INE752E01010', 'Power Grid Corporation of India Ltd.',    'STOCK', 'POWERGRID',  'NSE', 'Power',               false, '{}'),
  ('INE733E01010', 'NTPC Ltd.',                               'STOCK', 'NTPC',       'NSE', 'Power',               false, '{}'),
  ('INE081A01020', 'Tata Steel Ltd.',                         'STOCK', 'TATASTEEL',  'NSE', 'Metals',              false, '{}'),
  ('INE101A01026', 'Mahindra & Mahindra Ltd.',                'STOCK', 'M&M',        'NSE', 'Automobile',          false, '{}'),
  ('INE019A01038', 'JSW Steel Ltd.',                          'STOCK', 'JSWSTEEL',   'NSE', 'Metals',              false, '{}'),
  ('INE423A01024', 'Adani Enterprises Ltd.',                  'STOCK', 'ADANIENT',   'NSE', 'Diversified',         false, '{}'),
  ('INE522F01014', 'Coal India Ltd.',                         'STOCK', 'COALINDIA',  'NSE', 'Mining',              false, '{}'),
  ('INE059A01026', 'Cipla Ltd.',                              'STOCK', 'CIPLA',      'NSE', 'Pharma',              false, '{}'),
  ('INE089A01031', 'Dr. Reddy''s Laboratories Ltd.',          'STOCK', 'DRREDDY',    'NSE', 'Pharma',              false, '{}'),
  ('INE047A01021', 'Grasim Industries Ltd.',                  'STOCK', 'GRASIM',     'NSE', 'Cement',              false, '{}'),
  ('INE038A01020', 'Hindalco Industries Ltd.',                'STOCK', 'HINDALCO',   'NSE', 'Metals',              false, '{}'),
  ('INE213A01029', 'Oil & Natural Gas Corporation Ltd.',      'STOCK', 'ONGC',       'NSE', 'Energy',              false, '{}'),
  ('INE066A01021', 'Eicher Motors Ltd.',                      'STOCK', 'EICHERMOT',  'NSE', 'Automobile',          false, '{}'),
  ('INE669C01036', 'Tech Mahindra Ltd.',                      'STOCK', 'TECHM',      'NSE', 'IT',                  false, '{}'),
  ('INE263A01024', 'Bharat Electronics Ltd.',                 'STOCK', 'BEL',        'NSE', 'Defence',             false, '{}'),
  ('INE646L01027', 'InterGlobe Aviation Ltd.',                'STOCK', 'INDIGO',     'NSE', 'Aviation',            false, '{}'),
  ('INE192A01025', 'Tata Consumer Products Ltd.',             'STOCK', 'TATACONSUM', 'NSE', 'FMCG',                false, '{}'),
  ('INE849A01020', 'Trent Ltd.',                              'STOCK', 'TRENT',      'NSE', 'Retail',              false, '{}'),

  -- ETFs
  ('INF204KB14I2', 'Nippon India ETF Nifty 50 BeES',          'ETF',   'NIFTYBEES',  'NSE', 'Diversified',         false, '{"underlying_index": "NIFTY 50"}'),
  ('INF204KB15I9', 'Nippon India ETF Nifty Bank BeES',        'ETF',   'BANKBEES',   'NSE', 'Banking',             false, '{"underlying_index": "NIFTY BANK"}'),
  ('INF200KA1FS1', 'SBI-ETF Nifty 50',                        'ETF',   'SETFNIF50',  'NSE', 'Diversified',         false, '{"underlying_index": "NIFTY 50"}'),
  ('INF179KC1981', 'HDFC Gold ETF',                           'ETF',   'HDFCGOLD',   'NSE', 'Commodities',         false, '{"underlying": "Gold"}'),

  -- Mutual funds (direct growth plans) -- symbol is null per CLAUDE.md rule 3 (MFs identify by ISIN/scheme code, not a ticker)
  ('INF179K01YV8', 'HDFC Large Cap Fund - Direct Plan - Growth', 'MF', null, 'AMFI', null, false,
     '{"amc": "HDFC Mutual Fund", "plan": "Direct", "option": "Growth", "category": "Equity - Large Cap"}'),
  ('INF200K01QX4', 'SBI Bluechip Fund - Direct Plan - Growth',   'MF', null, 'AMFI', null, false,
     '{"amc": "SBI Mutual Fund", "plan": "Direct", "option": "Growth", "category": "Equity - Large Cap"}'),

  -- Government bond -- see CLAUDE.md rule 10, bonds are approximated (face value + accrued interest) until priced
  ('IN0020230085', '7.18% GOI 2033',                          'BOND',  '718GS2033',  'NSE', 'Government Securities', false,
     '{"issuer": "Government of India", "coupon_pct": 7.18, "maturity_date": "2033-08-14"}')

on conflict (isin) do nothing;
